from pathlib import Path

import yaml

from cmscontrib.aoi.rule import LatexCompileRule, CppCompileRule, CppRunRule, ShellRule, PyRunRule, PyinlineRule, \
    RawRule, MakeRule, ZipRule, GunzipRule, UnzipRule


class AOITag:
    def __init__(self, base_directory, tag, rule_type, value):
        self.tag = tag
        self.rule_type = rule_type
        self.value = value
        self.base_directory = base_directory


def register_tag(tag, rule_type):
    def on_tag(loader, node):
        return AOITag(Path(loader.name).parent, tag, rule_type, node.value)
    yaml.SafeLoader.add_constructor(tag, on_tag)


for tag, rule_type in {
    '!latexcompile': LatexCompileRule,
    '!cppcompile': CppCompileRule,
    '!cpprun': CppRunRule,
    '!shell': ShellRule,
    '!pyrun': PyRunRule,
    '!pyinline': PyinlineRule,
    '!raw': RawRule,
    '!make': MakeRule,
    '!zip': ZipRule,
    '!gunzip': GunzipRule,
    '!unzip': UnzipRule,
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
