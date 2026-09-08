import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
CREATOR_SCRIPTS = ROOT / "skills" / "skill-creator" / "scripts"
MIGRATION_UTILS = ROOT / "skills" / "migrate-to-codex" / "scripts" / "utils" / "__init__.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wrong_utils_module():
    spec = importlib.util.spec_from_file_location("utils", MIGRATION_UTILS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_skill(tmp_path: Path) -> Path:
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: a demo skill\n---\n# Demo\n"
    )
    return skill


@pytest.mark.parametrize("script_name", ["run_eval", "improve_description", "run_loop"])
def test_skill_creator_reads_its_adjacent_utils_after_another_utils_is_loaded(
    monkeypatch, tmp_path, script_name
):
    wrong_utils = wrong_utils_module()
    monkeypatch.setitem(sys.modules, "utils", wrong_utils)
    module = load_module(script_name, CREATOR_SCRIPTS / f"{script_name}.py")

    name, description, _ = module.parse_skill_md(make_skill(tmp_path))

    assert (name, description) == ("demo", "a demo skill")
    assert sys.modules["utils"] is wrong_utils


def test_package_skill_load_does_not_prepend_a_generic_scripts_path():
    before = list(sys.path)
    load_module("package_skill_isolated", CREATOR_SCRIPTS / "package_skill.py")
    assert sys.path == before
