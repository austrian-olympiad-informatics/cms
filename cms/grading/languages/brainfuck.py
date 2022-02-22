from cms.grading import Language


__all__ = ["Brainfuck"]


class Brainfuck(Language):

    @property
    def name(self):
        return "Brainfuck"

    @property
    def source_extensions(self):
        return [".bf"]

    @property
    def executable_extension(self):
        """See Language.executable_extension."""
        return ".bf"

    def get_compilation_commands(self,
                                 source_filenames, executable_filename,
                                 for_evaluation=True):
        if source_filenames[0] != executable_filename:
            return [["/bin/cp", source_filenames[0], executable_filename]]
        else:
            # We need at least one command to collect execution stats.
            return [["/bin/true"]]

    def get_evaluation_commands(
            self, executable_filename, main=None, args=None):
        """See Language.get_evaluation_commands."""
        args = args if args is not None else []
        return [["/usr/bin/bf", executable_filename] + args]
