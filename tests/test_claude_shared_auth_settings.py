import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import claude_shared_auth as auth


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.cwd = self.home / "project"
        self.cwd.mkdir()
        self.settings = self.home / ".claude/settings.json"
        self.settings.parent.mkdir()

    def check(self, args=(), env=None):
        auth.check_settings(self.home, env or {}, args, cwd=self.cwd)

    def test_standard_settings_cannot_replace_shared_auth(self):
        for settings in ({"apiKeyHelper": "printf TEST_SECRET"},
                         {"env": {"ANTHROPIC_API_KEY": "TEST_SECRET"}},
                         {"env": {"CLAUDE_CODE_OAUTH_TOKEN": "TEST_SECRET"}},
                         {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
                         {"env": {"DISABLE_LOGIN_COMMAND": "0"}},
                         {"forceLoginMethod": "gateway"}):
            with self.subTest(settings=settings):
                self.settings.write_text(json.dumps(settings))
                with self.assertRaises(auth.AuthError) as err:
                    self.check()
                self.assertNotIn("TEST_SECRET", str(err.exception))

    def test_local_project_and_explicit_settings_checked(self):
        p = self.cwd / ".claude/settings.local.json"
        p.parent.mkdir()
        p.write_text('{"apiKeyHelper":"secret-helper"}')
        with self.assertRaises(auth.AuthError):
            self.check()
        p.unlink()
        for args in (["--settings", '{"apiKeyHelper":"secret-helper"}'],
                     ['--settings={"env":{"ANTHROPIC_BASE_URL":"secret"}}']):
            with self.assertRaises(auth.AuthError):
                self.check(args)
        p.write_text('{"env":{"CLAUDE_CODE_OAUTH_TOKEN":"secret"}}')
        with self.assertRaises(auth.AuthError):
            self.check(["--settings", str(p)])

    def test_custom_config_and_worktree_main_local_settings(self):
        custom = self.home / "custom"
        custom.mkdir()
        (custom / "settings.json").write_text('{"apiKeyHelper":"secret"}')
        with self.assertRaises(auth.AuthError):
            self.check(env={"CLAUDE_CONFIG_DIR": str(custom)})
        main = self.home / "main"
        (main / ".claude").mkdir(parents=True)
        (main / ".claude/settings.local.json").write_text('{"apiKeyHelper":"secret"}')
        with mock.patch.object(auth, "_git_setting_roots", return_value=[main]):
            with self.assertRaises(auth.AuthError):
                self.check()

    def test_normal_customizations_preserved_and_invalid_json_rejected(self):
        self.settings.write_text(json.dumps({"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
                                            "permissions": {"allow": ["Read"]}, "model": "sonnet"}))
        self.check(["--settings", '{"permissions":{"allow":["Read"]}}'])
        self.settings.write_text('{"env":')
        with self.assertRaises(auth.AuthError):
            self.check()


if __name__ == "__main__":
    unittest.main()
