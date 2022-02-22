from cms.grading import CompiledLanguage
from pathlib import Path


__all__ = ["Brainfuck"]


class Brainfuck(CompiledLanguage):
    @property
    def name(self):
        return "Brainfuck"

    @property
    def source_extensions(self):
        return [".bf"]

    @property
    def object_extensions(self):
        """See Language.source_extensions."""
        return [".o"]

    def get_compilation_commands(self,
                                 source_filenames, executable_filename,
                                 for_evaluation=True):
        """See Language.get_compilation_commands."""
        fname = source_filenames[0]
        creat = Path(fname).with_suffix("").name
        return [
            ["/usr/bin/bfc", fname],
            ["/bin/mv", creat, executable_filename]
        ]
