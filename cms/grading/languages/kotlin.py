#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2016-2017 Stefano Maggiolo <s.maggiolo@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Java programming language definition, using the default JDK installed
in the system.

"""

from shlex import quote as shell_quote

from cms.grading import Language


__all__ = ["Kotlin"]


class Kotlin(Language):

    @property
    def name(self):
        """See Language.name."""
        return "Kotlin"

    @property
    def source_extensions(self):
        """See Language.source_extensions."""
        return [".kt"]

    @property
    def executable_extension(self):
        """See Language.executable_extension."""
        return ".jar"

    @property
    def requires_multithreading(self):
        """See Language.requires_multithreading."""
        return True

    def get_compilation_commands(self,
                                 source_filenames, executable_filename,
                                 for_evaluation=True):
        """See Language.get_compilation_commands."""
        return [
            ["/usr/bin/kotlinc", "-include-runtime",
             "-d", executable_filename,
             *source_filenames]
        ]

    def get_evaluation_commands(
            self, executable_filename, main=None, args=None):
        """See Language.get_evaluation_commands."""
        args = args if args is not None else []
        return [["/usr/bin/java", "-Deval=true", "-Xmx512M", "-Xss128M",
                 "-jar", executable_filename] + args]
