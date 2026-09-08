import subprocess
import sys
import zipfile
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "skill-creator" / "scripts"


def make_skill(tmp_path: Path) -> Path:
    skill = tmp_path / "demo"
    (skill / "evals").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: A small portable demo skill.\n"
        "---\n"
        "# Demo\n\nUse the bundled reference when needed.\n"
    )
    (skill / "references" / "guide.md").write_text("guide\n")
    (skill / "evals" / "evals.json").write_text("{}\n")
    return skill


def run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_quick_validate_is_portable_without_yaml_package(tmp_path):
    result = run(SCRIPTS / "quick_validate.py", str(make_skill(tmp_path)))
    assert result.returncode == 0, result.stderr + result.stdout
    assert "valid" in result.stdout.lower()


def test_package_script_runs_from_repository_root_and_excludes_evals(tmp_path):
    skill = make_skill(tmp_path)
    output = tmp_path / "dist"
    result = run(SCRIPTS / "package_skill.py", str(skill), str(output))
    assert result.returncode == 0, result.stderr + result.stdout

    package = output / "demo.skill"
    assert package.is_file()
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
    assert "demo/SKILL.md" in names
    assert "demo/references/guide.md" in names
    assert not any(name.startswith("demo/evals/") for name in names)


def test_routing_eval_records_requested_execution_settings():
    spec = importlib.util.spec_from_file_location("run_eval", SCRIPTS / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.run_single_query = lambda *args, **kwargs: {
        "should_use_skill": True,
        "confidence": 1.0,
        "reason": "fixture",
    }
    result = module.run_eval(
        eval_set=[{"query": "create a skill", "should_trigger": True}],
        skill_name="demo",
        description="create skills",
        num_workers=1,
        timeout=1,
        project_root=ROOT,
        model="gpt-5.6-luna",
        reasoning_effort="low",
    )
    assert result["execution"] == {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "timeout_seconds": 1,
        "runs_per_query": 1,
        "num_workers": 1,
    }
