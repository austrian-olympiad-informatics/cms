from pathlib import Path
import argparse
import tempfile
import shutil
import subprocess
import sys

yaml_contents_template = """\
---
feedback_level: FULL

score_options:
  decimal_places: 0
  mode: SUM_SUBTASK_BEST
  type: GROUP_MIN

time_limit: 1.0s
memory_limit: 256MiB
task_type:
    type: OJUZ
    ojuz_key: %s

cpp_config:
  gcc_args: -O2 -std=c++17 -static -s -Wno-unused-result -I${TASKDIR}/../lib

latex_config:
  additional_files: []

name: %s
long_name: %s

attribution: 'oj.uz'
author: '?'
uses: []

statements:
  de: !latexcompile temp.tex

subtasks:
  - points: 100
    testcases:
      - input: !raw ''
        output: !raw ''

test_submissions: {}
"""

tex_contents_template = r"""
\documentclass{article}
\usepackage{hyperref}

\begin{document}

Statement/Attachments siehe \href{https://oj.uz/problem/view/%s}{hier}. Es kann ganz normal über unseren Server eingesendet werden.

\end{document}

"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", type=int, help="Contest to add the task to")
    parser.add_argument("ojuz_key", type=str)
    args = parser.parse_args()

    dirpath = tempfile.mkdtemp()

    (Path(dirpath) / "task.yaml").write_text(yaml_contents_template.replace("%s", args.ojuz_key))
    (Path(dirpath) / "temp.tex").write_text(tex_contents_template.replace("%s", args.ojuz_key))
    subprocess.run(["cmsAOI", "-c", str(args.c), "."], cwd=dirpath, check=True)
    shutil.rmtree(dirpath)


if __name__ == "__main__":
    sys.exit(main())
