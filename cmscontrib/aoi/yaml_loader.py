from pathlib import Path

import yaml

from cmscontrib.aoi.rule import LatexCompileRule, CppCompileRule, CppRunRule, ShellRule, PyRunRule, PyinlineRule, \
    RawRule


class AOITag:
    def __init__(self, base_directory, rule_type, value):
        self.rule_type = rule_type
        self.value = value
        self.base_directory = base_directory


def register_tag(tag, rule_type):
    yaml.SafeLoader.add_constructor(tag, lambda loader, node: AOITag(Path(loader.name).parent, rule_type, node.value))


for tag, rule_type in {
    '!latexcompile': LatexCompileRule,
    '!cppcompile': CppCompileRule,
    '!cpprun': CppRunRule,
    '!shell': ShellRule,
    '!pyrun': PyRunRule,
    '!pyinline': PyinlineRule,
    '!raw': RawRule,
}.items():
    register_tag(tag, rule_type)


def load_yaml(fname):
    return _load_yaml_internal(fname)


def _load_yaml_internal(fname):
    content = Path(fname).read_text()
    loader = yaml.SafeLoader(content)
    loader.name = fname
    try:
        return loader.get_single_data() or {}
    finally:
        loader.dispose()
