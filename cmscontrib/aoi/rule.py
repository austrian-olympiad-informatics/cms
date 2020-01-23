import logging
import os
import shlex
import shutil
import subprocess
from abc import ABC
from pathlib import Path
from typing import List, Optional, Union

from cmscontrib.aoi.core import core, CMSAOIError
from cmscontrib.aoi.util import stable_hash, copytree, copy_if_necessary

_LOGGER = logging.getLogger(__name__)


class Rule(ABC):
    def __init__(self, *, input_files: List[Path] = None, output_extension: str = "",
                 dependencies: List['Rule'] = None, entropy: Optional[str] = None,
                 run_entropy: str = '', base_directory: Path = None):
        assert input_files is not None
        self.input_files = [base_directory / Path(x) for x in input_files]
        self.dependencies = dependencies or []

        input_filenames_s = [str(x) for x in self.input_files]
        filename = self.__class__.__name__ + stable_hash('|'.join(input_filenames_s) + entropy) + output_extension
        self.output_file: Path = core.internal_dir / filename
        self._has_run = False

    @property
    def all_input_files(self):
        ret = self.input_files.copy()
        for dep in self.dependencies:
            ret += dep.all_input_files
        return ret

    def is_up_to_date(self):
        if not self.output_file.exists():
            return False
        out_mtime = self.output_file.stat().st_mtime
        for file in self.all_input_files:
            if not file.exists():
                # If the input file does not exist
                return False
            if file.stat().st_mtime > out_mtime:
                return False
        return True

    def _execute(self):
        raise NotImplementedError

    def execute(self):
        if self._has_run:
            return
        for dep in self.dependencies:
            dep.ensure()
        input_max_mtime = 0
        for m in self.input_files:
            if not m.exists():
                raise CMSAOIError(f"Input file {m} for task {self} does not exist!")
            input_max_mtime = max(input_max_mtime, m.stat().st_mtime)

        self._execute()

        if not self.output_file.exists():
            raise CMSAOIError(f"Task {self} did not produce output file {self.output_file}")
        if input_max_mtime > 0:
            # Set mtime on output file
            os.utime(str(self.output_file), (self.output_file.stat().st_atime, input_max_mtime))
        self._has_run = True

    def ensure(self):
        if not self.is_up_to_date():
            self.execute()


class CommandRule(Rule, ABC):
    STDOUT_MAGIC = '${STDOUT}'

    def __init__(self, *, command: List[str] = None,
                 stdin_file: Optional[Path] = None, stdin_raw: Optional[bytes] = None,
                 stdout_to_output: Optional[bool] = None,
                 **kwargs):
        assert command is not None
        # Entropy is calculated before stdout magic
        entropy = kwargs.pop('entropy', '') + ' '.join(command)
        input_files = kwargs.pop('input_files')
        stdin_file = stdin_file
        if stdin_file:
            stdin_file = kwargs['base_directory'] / Path(stdin_file)
            entropy += str(stdin_file)
            input_files.append(stdin_file)
        if stdin_raw:
            entropy += stable_hash(stdin_raw)

        super().__init__(entropy=entropy, input_files=input_files, **kwargs)

        self.command = command
        self.stdin_file = stdin_file
        self.stdin_raw = stdin_raw
        self.stdout_to_output = stdout_to_output or False

    def pre_run(self):
        pass

    def post_run(self):
        pass

    @property
    def command(self):
        return self._command

    @command.setter
    def command(self, value):
        self._command = []
        for x in value:
            x = x.replace(CommandRule.STDOUT_MAGIC, str(self.output_file))
            self._command.append(x)

    def _open_stdin(self):
        if self.stdin_file is not None:
            return self.stdin_file.open("rb")
        if self.stdin_raw is not None:
            read, write = os.pipe()
            os.write(write, self.stdin_raw)
            os.close(write)
            return read
        return None

    def _execute(self):
        self.pre_run()
        stdout = None
        if self.stdout_to_output:
            stdout = self.output_file.open('wb')
        cmd_s = ' '.join(shlex.quote(x) for x in self.command)
        if self.stdin_file:
            cmd_s += f' <{self.stdin_file}'
        if self.stdout_to_output:
            cmd_s += f' >{self.output_file}'
        _LOGGER.info("Executing %s", cmd_s)
        stdin = self._open_stdin()
        try:
            subprocess.check_call(self.command, stdin=stdin, stdout=stdout)
        except subprocess.CalledProcessError as err:
            _LOGGER.error("Command %s failed, please view stderr logs above.", cmd_s)
            _LOGGER.error(str(err))
            if self.stdout_to_output:
                os.unlink(str(self.output_file))
            raise CMSAOIError(f"Failed to build {self.output_file}") from err
        finally:
            for fh in (stdin, stdout):
                if isinstance(fh, int):
                    os.close(fh)
                elif fh is not None:
                    fh.close()

        self.post_run()


class LatexCompileRule(CommandRule):
    def __init__(self, args: str, **kwargs):
        args: List[str] = shlex.split(args)
        assert len(args) >= 1

        # Temporary directory to compile in
        compile_dir_rule = DirectoryRule(**kwargs)
        self._compile_dir = compile_dir_rule.output_file

        base_directory = kwargs['base_directory']
        input_files = [base_directory / Path(x) for x in args] + core.latex_additional_files
        self._main_file = input_files[0]

        command = ['latexmk', '-latexoption=-interaction=nonstopmode', '-pdf', '-cd']
        command += shlex.split(core.latexmk_args)
        compile_tex_file = self._compile_dir / self._main_file.absolute().relative_to(core.task_dir)
        command.append(str(compile_tex_file))

        self._pdf_file: Path = compile_tex_file.with_suffix('.pdf')

        super().__init__(
            input_files=input_files,
            command=command,
            output_extension='.pdf',
            dependencies=[compile_dir_rule],
            **kwargs,
        )

    def pre_run(self):
        copytree(core.task_dir, self._compile_dir, ignore={core.internal_dir})
        for additional_file in core.latex_additional_files:
            dst = self._main_file.parent / additional_file.name
            copy_if_necessary(additional_file, dst)

    def post_run(self):
        shutil.copy(self._pdf_file, self.output_file)


class DirectoryRule(Rule):
    def __init__(self, **kwargs):
        entropy = kwargs.get('entropy', '') + kwargs['run_entropy']
        super().__init__(
            input_files=[],
            output_extension=".dir",
            entropy=entropy,
            **kwargs,
        )

    def _execute(self):
        self.output_file.mkdir(exist_ok=True)


class CppCompileRule(CommandRule):
    def __init__(self, args: str, **kwargs):
        args = shlex.split(args)
        # FIXME: Find better way to distinguish between GCC args and input files
        input_files = [Path(x) for x in args]
        input_files = [x for x in input_files if x.suffix in ('.h', '.cpp', '.c')]
        # Has to be static for binaries that are uploaded to CMS (maybe incompatible stdlib ABI)
        command = ['g++', '-O2', '-std=gnu++11', '-pipe', '-o', CommandRule.STDOUT_MAGIC, '-static', '-s',
                   '-Wno-unused-result']
        for arg in args:
            p = Path(arg)
            if p.is_file():
                command.append(str(p))
            else:
                command.append(arg)
        super().__init__(
            input_files=input_files,
            command=command,
            **kwargs,
        )


class CppRunRule(CommandRule):
    def __init__(self, args: str, **kwargs):
        args = shlex.split(args)
        assert len(args) >= 1
        cpp_file = args[0]
        compile_rule = CppCompileRule(cpp_file)
        command = [str(compile_rule.output_file), *args[1:]]
        super().__init__(
            input_files=[],
            output_extension='.exec',
            stdout_to_output=True,
            dependencies=[compile_rule],
            command=command,
            **kwargs
        )


class ShellRule(CommandRule):
    def __init__(self, args: str, **kwargs):
        args = shlex.split(args)
        assert len(args) >= 1
        input_files = []
        p = Path(args[0])
        if p.is_file():
            input_files.append(p)
        super().__init__(
            input_files=input_files,
            output_extension='.exec',
            stdout_to_output=True,
            command=args,
            **kwargs
        )


PYTHON_PRE_PROG = """
from random import seed as _aoi_seed
_aoi_seed({})
"""


class PyRunRule(CommandRule):
    def __init__(self, args: str, **kwargs):
        args = shlex.split(args)
        assert len(args) >= 1
        py_file = Path(args[0])

        run_entropy = kwargs['run_entropy']
        content = PYTHON_PRE_PROG.format(run_entropy).encode()
        content += py_file.read_bytes()

        raw_rule = RawRule(content)
        command = ['python3', str(raw_rule.output_file), *args[1:]]

        super().__init__(
            input_files=[py_file, raw_rule.output_file],
            output_extension='.exec',
            stdout_to_output=True,
            command=command,
            dependencies=[raw_rule],
            **kwargs,
        )


class PyinlineRule(CommandRule):
    def __init__(self, prog: str, **kwargs):
        command = ['python3']
        run_entropy = kwargs['run_entropy']
        stdin = PYTHON_PRE_PROG.format(run_entropy).encode()
        stdin += prog.encode()
        super().__init__(
            input_files=[],
            output_extension='.exec',
            stdout_to_output=True,
            command=command,
            stdin_raw=stdin,
            **kwargs,
        )


class RawRule(Rule):
    def __init__(self, text: Union[str, bytes], **kwargs):
        entropy = stable_hash(text)
        self.text = text
        super().__init__(
            input_files=[],
            output_extension=".raw",
            entropy=entropy,
            **kwargs,
        )

    def _execute(self):
        if isinstance(self.text, str):
            self.output_file.write_text(self.text)
        else:
            self.output_file.write_bytes(self.text)

