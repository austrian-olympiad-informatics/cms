import logging
import os
import shlex
import shutil
import string
import subprocess
import gzip
import lzma
import zipfile
from abc import ABC
from pathlib import Path
from typing import List, Optional, Union

import jinja2

from cmscontrib.aoi.const import CONF_INPUT_TEMPLATE, CONF_LATEX_CONFIG, CONF_CPP_CONFIG, CONF_GCC_ARGS, \
    CONF_ADDITIONAL_FILES, CONF_LATEXMK_ARGS
from cmscontrib.aoi.core import core, CMSAOIError
from cmscontrib.aoi.util import stable_hash, copytree, copy_if_necessary, expand_vars

_LOGGER = logging.getLogger(__name__)


class Rule(ABC):
    def __init__(self, *, input_files: List[Path] = None, output_extension: str = "",
                 dependencies: List['Rule'] = None, entropy: Optional[str] = None,
                 run_entropy: str = '', base_directory: Path = None, name: str = None):
        assert input_files is not None
        self.input_files = [base_directory / Path(x) for x in input_files]
        self.dependencies = dependencies or []

        input_filenames_s = [str(x) for x in self.input_files]
        allowed = string.digits + string.ascii_letters + '-_.'
        name_filtered = ''.join(c for c in name.replace(' ', '_') if c in allowed)
        filename = name_filtered + '-' + stable_hash('|'.join(input_filenames_s) + entropy) + output_extension
        self.output_file: Path = core.internal_dir / filename
        self._has_run = False
        self.name = name

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
        all_files = self.all_input_files
        if not all_files:
            # No input files, can't know if up to date or not
            return False
        for file in all_files:
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
    def __init__(self, *, command: List[str] = None,
                 stdin_file: Optional[Path] = None, stdin_raw: Optional[bytes] = None,
                 stdout_to_output: Optional[bool] = None, env = None,
                 cwd = None,
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
        self._env = env
        self._cwd = cwd

    def pre_run(self):
        pass

    def post_run(self):
        pass

    @property
    def command(self):
        return self._command

    @command.setter
    def command(self, value):
        env = {**os.environ, **{
            'STDOUT': str(self.output_file),
            'TASKDIR': str(core.task_dir),
        }}
        self._command = [expand_vars(x, env) for x in value]

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
        kwargs = {
            'stdin': stdin,
            'stdout': stdout,
        }
        if self._env is not None:
            kwargs['env'] = self._env
        if self._cwd is not None:
            kwargs['cwd'] = str(self._cwd)
        try:
            subprocess.check_call(self.command, **kwargs)
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

        base_directory = kwargs['base_directory']
        latex_config = core.config[CONF_LATEX_CONFIG]
        additional_files = latex_config[CONF_ADDITIONAL_FILES]
        input_files = [base_directory / Path(x) for x in args] + list(map(Path, additional_files))
        self._main_file = input_files[0]

        command = shlex.split(latex_config[CONF_LATEXMK_ARGS])
        compile_tex_file = (core.internal_build_dir / self._main_file.absolute().relative_to(core.task_dir)).absolute()
        self._compile_tex_file = compile_tex_file
        command.append(str(compile_tex_file))

        self._pdf_file: Path = compile_tex_file.with_suffix('.pdf')

        super().__init__(
            input_files=input_files,
            command=command,
            output_extension='.pdf',
            dependencies=[],
            env={**os.environ, 'SOURCE_DATE_EPOCH': '0'},
            cwd=compile_tex_file.parent,
            **kwargs,
        )

    @property
    def _main_rel(self):
        """The main file relative to the task dir."""
        return self._main_file.parent.absolute().relative_to(core.task_dir)

    def pre_run(self):
        for additional_file in core.latex_additional_files:
            dst = (core.internal_build_dir / self._main_rel) / additional_file.name
            copy_if_necessary(additional_file, dst)


    def post_run(self):
        shutil.copy(self._pdf_file, self.output_file)


class CppCompileRule(CommandRule):
    def __init__(self, args: str, **kwargs):
        args = shlex.split(args)
        # FIXME: Find better way to distinguish between GCC args and input files
        input_files = [Path(x) for x in args]
        input_files = [x for x in input_files if x.suffix in ('.h', '.cpp', '.c', '.cc')]
        command = shlex.split(core.config[CONF_CPP_CONFIG][CONF_GCC_ARGS])
        command += args
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
        name = kwargs.pop('name')
        compile_rule = CppCompileRule(cpp_file, name='CppRunCompile', **kwargs)
        command = [str(compile_rule.output_file), *args[1:]]
        super().__init__(
            input_files=[],
            output_extension='.exec',
            stdout_to_output=True,
            dependencies=[compile_rule],
            command=command,
            name=name,
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

        raw_rule = RawRule(content, **kwargs)
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
        entropy = kwargs.pop('entropy', '') + stable_hash(stdin)
        super().__init__(
            input_files=[],
            output_extension='.exec',
            stdout_to_output=True,
            command=command,
            stdin_raw=stdin,
            entropy=entropy,
            **kwargs,
        )

    def is_up_to_date(self):
        return self.output_file.exists()


class RawRule(Rule):
    def __init__(self, content: Union[str, bytes], **kwargs):
        entropy = stable_hash(content)
        if isinstance(content, str):
            content = content.encode()
        self.content = content
        super().__init__(
            input_files=[],
            output_extension=".raw",
            entropy=entropy,
            **kwargs,
        )

    def is_up_to_date(self):
        return self.output_file.exists() and self.output_file.read_bytes() == self.content

    def _execute(self):
        self.output_file.write_bytes(self.content)


class MakeRule(CommandRule):
    def __init__(self, args: str, **kwargs):
        parts = shlex.split(args)
        if len(parts) != 1:
            raise ValueError("Only one make target is supported at a time")
        self._make_target = Path(parts[0])
        super().__init__(
            input_files=[],
            output_extension=self._make_target.suffix,
            **kwargs,
        )

    def post_run(self):
        if not self._make_target.is_file():
            raise CMSAOIError(f"Make rule did not produce target {self._make_target}")
        copy_if_necessary(self._make_target, self.output_file)


class ZipRule(Rule):
    def __init__(self, args: str, **kwargs):
        self._members = []
        input_files = []
        for arg in shlex.split(args):
            if '*' in arg:
                assert '=' not in arg
                for p in Path.cwd().glob(arg):
                    zipname = p.name
                    self._members.append((p.name, p))
                    input_files.append(p)
                continue

            if '=' in arg:
                zipname, pathname = arg.split('=')
            else:
                zipname = Path(arg).name
                pathname = arg
            path = Path(pathname)
            self._members.append((zipname, path))
            input_files.append(path)

        super().__init__(
            input_files=input_files,
            output_extension='.zip',
            entropy=kwargs.pop('entropy', ''),
            **kwargs,
        )

    def _execute(self):
        with zipfile.ZipFile(self.output_file, 'w') as zipf:
            for zipname, path in self._members:
                zipf.writestr(zipname, path.read_bytes())


class GunzipRule(Rule):
    def __init__(self, path: str, **kwargs):
        self._gzip_file = Path(path)
        super().__init__(
            input_files=[self._gzip_file],
            output_extension=''.join(self._gzip_file.suffixes[:-1]),
            entropy=kwargs.pop('entropy', ''),
            **kwargs,
        )

    def _execute(self):
        with gzip.GzipFile(self._gzip_file, 'rb') as ifh:
            with self.output_file.open('wb') as ofh:
                shutil.copyfileobj(ifh, ofh)


class XZUnzipRule(Rule):
    def __init__(self, path: str, **kwargs):
        self._xz_file = Path(path)
        super().__init__(
            input_files=[self._xz_file],
            output_extension=''.join(self._xz_file.suffixes[:-1]),
            entropy=kwargs.pop('entropy', ''),
            **kwargs,
        )

    def _execute(self):
        with lzma.LZMAFile(self._xz_file, 'rb') as ifh:
            with self.output_file.open('wb') as ofh:
                shutil.copyfileobj(ifh, ofh)


class UnzipRule(Rule):
    def __init__(self, args: str, **kwargs):
        zipfile, filename = shlex.split(args)
        self._zip_file = Path(zipfile)
        self._extract_filename = filename

        super().__init__(
            input_files=[self._zip_file],
            output_extension=Path(filename).suffix,
            entropy=kwargs.pop('entropy', ''),
            **kwargs,
        )

    def _execute(self):
        with zipfile.ZipFile(self._zip_file, 'r') as zipf:
            with zipf.open(self._extract_filename, 'r') as ifh:
                with self.output_file.open('wb') as ofh:
                    shutil.copyfileobj(ifh, ofh)


class EmptyRule(Rule):
    def __init__(self, **kwargs):
        super().__init__(
            input_files=[],
            entropy=kwargs.pop('entropy', ''),
            **kwargs,
        )

    def _execute(self):
        self.output_file.write_bytes(b'')
