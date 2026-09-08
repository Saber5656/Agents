import subprocess
import sys
import zipfile
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "skill-creator" / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


@pytest.mark.parametrize("value", ["true", "null", "123", "[a, b]", '"unterminated', "'bad'quote'"])
def test_quick_validate_rejects_non_string_description_scalars(tmp_path, value):
    skill = make_skill(tmp_path)
    skill.joinpath("SKILL.md").write_text(
        "---\nname: demo\ndescription: " + value + "\n---\n# Demo\n"
    )

    result = run(SCRIPTS / "quick_validate.py", str(skill))

    assert result.returncode != 0


def test_quick_validate_keeps_declared_boolean_and_list_types(tmp_path):
    skill = make_skill(tmp_path)
    skill.joinpath("SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: A small portable demo skill.\n"
        "user-invocable: true\n"
        "fallback_models: [gpt-5.6-luna, gpt-5.4-mini]\n"
        "---\n# Demo\n"
    )

    result = run(SCRIPTS / "quick_validate.py", str(skill))

    assert result.returncode == 0, result.stderr + result.stdout


def test_quick_validate_parser_has_stable_types_without_optional_yaml():
    module = load_script("quick_validate")
    parsed = module.parse_frontmatter(
        "name: demo\n"
        "description: >-\n"
        "  A portable skill\n"
        "user-invocable: true\n"
        "fallback_models: [gpt-5.6-luna, gpt-5.4-mini]\n"
    )

    assert parsed == {
        "name": "demo",
        "description": "A portable skill",
        "user-invocable": True,
        "fallback_models": ["gpt-5.6-luna", "gpt-5.4-mini"],
    }


def test_quick_validate_rejects_duplicate_keys_without_optional_yaml(tmp_path):
    skill = make_skill(tmp_path)
    skill.joinpath("SKILL.md").write_text(
        "---\nname: demo\nname: duplicate\ndescription: desc\n---\n# Demo\n"
    )

    result = run(SCRIPTS / "quick_validate.py", str(skill))

    assert result.returncode != 0


def test_quick_validate_accepts_nested_metadata_with_yaml_or_reports_dependency(tmp_path):
    skill = make_skill(tmp_path)
    skill.joinpath("SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: desc\n"
        "metadata:\n"
        "  owner: team\n"
        "---\n# Demo\n"
    )

    result = run(SCRIPTS / "quick_validate.py", str(skill))

    yaml_available = importlib.util.find_spec("yaml") is not None
    assert result.returncode == (0 if yaml_available else 1)
    if not yaml_available:
        assert "PyYAML" in result.stdout or "PyYAML" in result.stderr


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
    module = load_script("run_eval")

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


def test_run_single_query_uses_subscription_safe_explicit_command(monkeypatch, tmp_path):
    module = load_script("run_eval")
    commands = []

    def fake_run(cmd, input=None, **kwargs):
        commands.append(cmd)
        assert input == module.build_routing_prompt("create a skill", "demo", "create skills")
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text('{"should_use_skill": true, "confidence": 1, "reason": "fixture"}')
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "ensure_chatgpt_subscription", lambda: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.run_single_query(
        "create a skill",
        "demo",
        "create skills",
        timeout=1,
        project_root=str(tmp_path),
        model="gpt-5.6-luna",
        reasoning_effort="low",
    )

    assert result["should_use_skill"] is True
    command = commands[0]
    assert command[-1] == "-"
    assert module.build_routing_prompt("create a skill", "demo", "create skills") not in command
    assert "--ignore-user-config" in command
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    effort_arg = next(item for item in command if item.startswith("model_reasoning_effort="))
    assert effort_arg == 'model_reasoning_effort="low"'
    for feature in ("apps", "plugins", "browser_use", "computer_use", "image_generation", "shell_tool", "multi_agent"):
        assert command[command.index(feature) - 1] == "--disable"


def test_run_single_query_requires_explicit_model_and_effort(monkeypatch, tmp_path):
    module = load_script("run_eval")
    monkeypatch.setattr(module, "ensure_chatgpt_subscription", lambda: None)

    with pytest.raises(ValueError, match="explicit model"):
        module.run_single_query("q", "demo", "desc", 1, str(tmp_path), None, "low")
    with pytest.raises(ValueError, match="reasoning effort"):
        module.run_single_query("q", "demo", "desc", 1, str(tmp_path), "gpt-5.6-luna", None)


def test_description_improvement_sends_prompt_over_stdin(monkeypatch):
    module = load_script("improve_description")
    commands = []

    def fake_run(cmd, input=None, **kwargs):
        commands.append((cmd, input))
        assert input and "Return JSON matching the provided schema" in input
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text('{"description": "improved description"}')
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "ensure_chatgpt_subscription", lambda: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.improve_description(
        skill_name="demo",
        skill_content="# Demo",
        current_description="demo",
        eval_results={"results": [], "summary": {"passed": 1, "total": 1}},
        history=[],
        model="gpt-5.6-luna",
        timeout=1,
    )

    assert result == "improved description"
    assert commands[0][0][-1] == "-"
    assert commands[0][1] not in commands[0][0]


def test_subscription_guard_rejects_api_key_environment(monkeypatch):
    module = load_script("run_eval")
    monkeypatch.setenv("OPENAI_API_KEY", "present-but-not-recorded")

    with pytest.raises(module.SubscriptionBoundaryError):
        module.ensure_chatgpt_subscription(status_runner=lambda *args, **kwargs: None)


def test_subscription_guard_accepts_only_chatgpt_login(monkeypatch):
    module = load_script("run_eval")
    calls = []

    def status(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    module.ensure_chatgpt_subscription(status_runner=status)

    assert calls == [((["codex", "login", "status"],), {
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": 20,
    })]


def test_subscription_guard_rejects_negative_chatgpt_status():
    module = load_script("run_eval")

    status = subprocess.CompletedProcess(
        ["codex", "login", "status"], 0,
        stdout="Not logged in using ChatGPT", stderr="",
    )
    with pytest.raises(module.AuthenticationError):
        module.ensure_chatgpt_subscription(status_runner=lambda *args, **kwargs: status)


def test_creator_instructions_match_routing_implementation():
    skill_text = (ROOT / "skills" / "skill-creator" / "SKILL.md").read_text()

    assert "Codex CLIまたはCodex CLI" not in skill_text
    assert "`codex exec` を使わず" not in skill_text
    assert "`run_eval.py` が明示したモデル・reasoning effortで `codex exec`" in skill_text
