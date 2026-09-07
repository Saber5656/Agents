from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from cli import (  # noqa: E402
    GENERATED_MARKER_NAME,
    ScopeDeployment,
    convert_scope,
    migration_skill_artifacts,
    write_artifact,
)
from migrate.common import (  # noqa: E402
    CODEX_CONFIG_PATH,
    CODEX_SKILLS_ROOT,
    GeneratedText,
    PlannedArtifact,
    ScopePaths,
)
from migrate.hooks import report_hooks  # noqa: E402


class MigrateToCodexReviewFixesTest(unittest.TestCase):
    def test_replace_orphans_require_generated_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target_root = Path(tmp)
            manual_skill = target_root / CODEX_SKILLS_ROOT / "manual"
            manual_skill.mkdir(parents=True)
            (manual_skill / "SKILL.md").write_text("---\nname: manual\n---\n", encoding="utf-8")
            generated_skill = target_root / CODEX_SKILLS_ROOT / "generated"
            generated_skill.mkdir()
            (generated_skill / "SKILL.md").write_text("---\nname: generated\n---\n", encoding="utf-8")
            (generated_skill / GENERATED_MARKER_NAME).write_text("generated\n", encoding="utf-8")

            plan = ScopeDeployment((), target_root, frozenset({"skills"})).plan()

            self.assertEqual(plan.orphaned_skill_dirs, (generated_skill,))

    def test_write_config_merges_existing_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target_root = Path(tmp)
            config_path = target_root / CODEX_CONFIG_PATH
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                textwrap.dedent(
                    """
                    notify = ["terminal-notifier"]

                    [mcp_servers.existing]
                    command = "existing-mcp"
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            artifact = PlannedArtifact(
                relative_path=CODEX_CONFIG_PATH,
                payload=GeneratedText(
                    textwrap.dedent(
                        """
                        personality = "friendly"

                        [mcp_servers.generated]
                        command = "generated-mcp"
                        """
                    ).lstrip()
                ),
            )

            write_artifact(artifact, target_root)

            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["notify"], ["terminal-notifier"])
            self.assertEqual(parsed["personality"], "friendly")
            self.assertEqual(parsed["mcp_servers"]["existing"]["command"], "existing-mcp")
            self.assertEqual(parsed["mcp_servers"]["generated"]["command"], "generated-mcp")

    def test_component_specific_mcp_does_not_write_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp)
            (source_root / "CLAUDE.md").write_text("Claude instructions\n", encoding="utf-8")

            result = convert_scope(ScopePaths(source_root, False), frozenset({"mcp"}))

            self.assertNotIn(Path("AGENTS.md"), {artifact.relative_path for artifact in result.artifacts})

    def test_unsupported_only_hooks_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp)
            claude_dir = source_root / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {
                                    "matcher": "ignored",
                                    "hooks": [{"type": "prompt", "prompt": "summarize"}],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = report_hooks(ScopePaths(source_root, False))

            self.assertFalse(result.artifacts)
            self.assertEqual(result.report_items[0].status, "manual_fix_required")

    def test_migration_helper_skill_includes_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp)
            (source_root / "global").mkdir()

            paths = {artifact.relative_path.as_posix() for artifact in migration_skill_artifacts(source_root)}

            self.assertIn(
                "global/.agents/skills/migrate-to-codex/scripts/migrate-to-codex.py",
                paths,
            )


if __name__ == "__main__":
    unittest.main()
