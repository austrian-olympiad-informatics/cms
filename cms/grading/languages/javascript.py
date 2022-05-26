#!/usr/bin/env python3
"""Javascript programming language definition."""

from cms.grading import Language


__all__ = ["Javascript"]


class Javascript(Language):

    @property
    def name(self):
        """See Language.name."""
        return "Javascript"

    @property
    def source_extensions(self):
        """See Language.source_extensions."""
        return [".js"]

    @property
    def executable_extension(self):
        """See Language.executable_extension."""
        return ".js"

    def get_compilation_commands(self,
                                 source_filenames, executable_filename,
                                 for_evaluation=True):
        """See Language.get_compilation_commands."""
        cmds = [
            [
                "/bin/sh", "-c",
                " ".join(["cp", "-r", "/js-base/*", "."])
            ],
            ["/bin/mv", source_filenames[0], "main.js"],
        ]

        for fn in source_filenames[1:]:
            if fn != fn.lower():
                cmds.append(["/bin/mv", fn, fn.lower()])

        cmds += [
            ["/usr/bin/npm", "run", "build"],
            ["/bin/cp", "dist/bundle.js", executable_filename],
        ]
        return cmds

    def get_evaluation_commands(
            self, executable_filename, main=None, args=None):
        """See Language.get_evaluation_commands."""
        args = args if args is not None else []
        return [
            ["/usr/bin/node", executable_filename, *args],
        ]
