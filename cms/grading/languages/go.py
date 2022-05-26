#!/usr/bin/env python3
from shlex import shlex
from cms.grading import CompiledLanguage


__all__ = ["Go"]


class Go(CompiledLanguage):
    @property
    def name(self):
        """See Language.name."""
        return "Go"

    @property
    def source_extensions(self):
        """See Language.source_extensions."""
        return [".go"]

    def get_compilation_commands(self,
                                 source_filenames, executable_filename,
                                 for_evaluation=True):
        """See Language.get_compilation_commands."""
        command = [
            "/usr/bin/go", "build", "-o", executable_filename,
            *source_filenames
        ]
        return [command]
