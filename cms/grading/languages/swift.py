#!/usr/bin/env python3

"""Swift programming language definition."""

from cms.grading import CompiledLanguage


__all__ = ["Swift"]


class Swift(CompiledLanguage):
    """This defines the Swift programming language, compiled with the
    standard Swift compiler available in the system.

    """

    @property
    def name(self):
        """See Language.name."""
        return "Swift"

    @property
    def source_extensions(self):
        """See Language.source_extensions."""
        return [".swift"]

    def get_compilation_commands(self,
                                 source_filenames, executable_filename,
                                 for_evaluation=True):
        """See Language.get_compilation_commands."""
        cmds = []
        fnames = source_filenames.copy()
        if fnames[0] != "main.swift":
            cmds.append(["/bin/mv", fnames[0], "main.swift"])
            fnames[0] = "main.swift"
        cmds += [["/usr/bin/swiftc", "-O", "-o", executable_filename, *fnames]]
        return cmds
