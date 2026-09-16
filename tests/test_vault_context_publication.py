import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import vault_context_publication as vault  # noqa: E402
from daily_it_news_delivery import DeliveryError  # noqa: E402


def git(cwd, *args):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


class VaultContextPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        work = root / "seed"
        subprocess.run(["git", "clone", str(self.remote), str(work)], check=True, capture_output=True)
        git(work, "switch", "-c", "main")
        git(work, "config", "user.name", "Fixture")
        git(work, "config", "user.email", "fixture@example.invalid")
        (work / "README").write_text("seed")
        git(work, "add", "README")
        git(work, "commit", "-m", "seed")
        git(work, "push", "origin", "main")
        self.snapshot = root / "snapshot"
        self.snapshot.mkdir()
        self.run = root / "run"
        self.gitleaks = root / "gitleaks"
        self.gitleaks.write_text("#!/bin/sh\nexit 0\n")
        self.gitleaks.chmod(0o755)

    def _write(self, relative, text):
        target = self.snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_publishes_selected_files_in_one_commit(self):
        self._write("notes/a.md", "Alpha note\n")
        self._write("notes/b.md", "Beta note\n")
        result = vault.publish_snapshot(
            str(self.remote), self.snapshot, ["notes/a.md", "notes/b.md"], self.run,
            "Fixture", "fixture@example.invalid", str(self.gitleaks),
            {"notes/a.md": None, "notes/b.md": None})
        self.assertEqual("published", result["status"])
        log = git(self.remote, "log", "main", "--oneline")
        self.assertEqual(2, len(log.splitlines()))
        self.assertEqual("Alpha note\n", git(self.remote, "show", "main:notes/a.md") + "\n")
        self.assertEqual("Beta note\n", git(self.remote, "show", "main:notes/b.md") + "\n")

    def test_identical_rerun_is_a_no_op(self):
        self._write("notes/a.md", "Alpha note\n")
        first = vault.publish_snapshot(
            str(self.remote), self.snapshot, ["notes/a.md"], self.run,
            "Fixture", "fixture@example.invalid", str(self.gitleaks), {"notes/a.md": None})
        before = git(self.remote, "rev-parse", "main")
        second = vault.publish_snapshot(
            str(self.remote), self.snapshot, ["notes/a.md"], self.run,
            "Fixture", "fixture@example.invalid", str(self.gitleaks),
            {"notes/a.md": first["selected_blob"]["notes/a.md"]})
        self.assertEqual("no_op", second["status"])
        self.assertEqual(before, git(self.remote, "rev-parse", "main"))

    def test_rerun_after_success_before_ledger_save_is_noop(self):
        self._write('notes/a.md', 'Alpha\n')
        args = (str(self.remote), self.snapshot, ['notes/a.md'], self.run,
                'Fixture', 'fixture@example.invalid', str(self.gitleaks), {'notes/a.md': None})
        first = vault.publish_snapshot(*args)
        second = vault.publish_snapshot(*args)
        self.assertEqual('no_op', second['status'])
        self.assertEqual(first['commit'], second['commit'])

    def test_only_changed_paths_are_committed_in_mixed_snapshot(self):
        self._write('README', 'seed')
        self._write('notes/a.md', 'new\n')
        result = vault.publish_snapshot(str(self.remote), self.snapshot,
            ['README', 'notes/a.md'], self.run, 'Fixture', 'fixture@example.invalid',
            str(self.gitleaks), {'README': git(self.remote, 'rev-parse', 'main:README'), 'notes/a.md': None})
        self.assertEqual('published', result['status'])
        self.assertEqual('notes/a.md', git(self.remote, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'main'))

    def test_non_ascii_and_literal_glob_path_is_preserved(self):
        path = '記録/[notes] #1.md'
        self._write(path, '完全な本文\n')
        result = vault.publish_snapshot(str(self.remote), self.snapshot, [path], self.run,
            'Fixture', 'fixture@example.invalid', str(self.gitleaks), {path: None})
        self.assertEqual('published', result['status'])
        self.assertEqual('完全な本文', git(self.remote, 'show', 'main:' + path))

    def test_unrelated_remote_update_is_preserved(self):
        self._write("notes/a.md", "Alpha note\n")
        vault.publish_snapshot(
            str(self.remote), self.snapshot, ["notes/a.md"], self.run,
            "Fixture", "fixture@example.invalid", str(self.gitleaks), {"notes/a.md": None})
        other = Path(self.tmp.name) / "other-clone"
        subprocess.run(["git", "clone", str(self.remote), str(other)], check=True, capture_output=True)
        git(other, "config", "user.name", "Other")
        git(other, "config", "user.email", "other@example.invalid")
        (other / "unrelated.md").write_text("unrelated\n")
        git(other, "add", "unrelated.md")
        git(other, "commit", "-m", "unrelated update")
        git(other, "push", "origin", "main")

        self._write("notes/c.md", "Gamma note\n")
        result = vault.publish_snapshot(
            str(self.remote), self.snapshot, ["notes/c.md"], self.run,
            "Fixture", "fixture@example.invalid", str(self.gitleaks), {"notes/c.md": None})
        self.assertEqual("published", result["status"])
        self.assertEqual("unrelated\n", git(self.remote, "show", "main:unrelated.md") + "\n")
        self.assertEqual("Gamma note\n", git(self.remote, "show", "main:notes/c.md") + "\n")

    def test_conflicting_expected_blob_is_rejected_without_overwrite(self):
        self._write("notes/a.md", "Alpha note\n")
        vault.publish_snapshot(
            str(self.remote), self.snapshot, ["notes/a.md"], self.run,
            "Fixture", "fixture@example.invalid", str(self.gitleaks), {"notes/a.md": None})
        before = git(self.remote, "rev-parse", "main")

        self._write("notes/a.md", "Alpha note, changed\n")
        with self.assertRaisesRegex(DeliveryError, "remote content changed"):
            vault.publish_snapshot(
                str(self.remote), self.snapshot, ["notes/a.md"], self.run,
                "Fixture", "fixture@example.invalid", str(self.gitleaks),
                {"notes/a.md": None})
        self.assertEqual(before, git(self.remote, "rev-parse", "main"))
        self.assertEqual("Alpha note\n", git(self.remote, "show", "main:notes/a.md") + "\n")

    def test_uncertain_push_result_is_reconciled_via_readback(self):
        self._write("notes/a.md", "Alpha note\n")
        calls = {"n": 0}
        real_git = vault._git

        def flaky_git(args, cwd=None, **kwargs):
            if args and args[0] == "push":
                calls["n"] += 1
                real_git(args, cwd, **kwargs)
                raise vault.RetryableDeliveryError("simulated ambiguous network response")
            return real_git(args, cwd, **kwargs)

        vault._git = flaky_git
        try:
            result = vault.publish_snapshot(
                str(self.remote), self.snapshot, ["notes/a.md"], self.run,
                "Fixture", "fixture@example.invalid", str(self.gitleaks), {"notes/a.md": None})
        finally:
            vault._git = real_git
        self.assertEqual("published", result["status"])
        self.assertEqual(1, calls["n"])
        self.assertEqual("Alpha note\n", git(self.remote, "show", "main:notes/a.md") + "\n")

    def test_unsafe_path_is_rejected(self):
        self._write("notes/a.md", "Alpha note\n")
        with self.assertRaises(DeliveryError):
            vault.publish_snapshot(
                str(self.remote), self.snapshot, ["../escape.md"], self.run,
                "Fixture", "fixture@example.invalid", str(self.gitleaks), {"../escape.md": None})

    def test_symlinked_snapshot_ancestor_is_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("secret\n")
        (self.snapshot / "linked").symlink_to(outside)
        with self.assertRaisesRegex(DeliveryError, "symlink"):
            vault.publish_snapshot(
                str(self.remote), self.snapshot, ["linked/secret.md"], self.run,
                "Fixture", "fixture@example.invalid", str(self.gitleaks),
                {"linked/secret.md": None})

    def test_gitleaks_failure_blocks_publication(self):
        self._write("notes/a.md", "Alpha note\n")
        failing_gitleaks = Path(self.tmp.name) / "failing-gitleaks"
        failing_gitleaks.write_text("#!/bin/sh\nexit 1\n")
        failing_gitleaks.chmod(0o755)
        before = git(self.remote, "rev-parse", "main")
        with self.assertRaisesRegex(DeliveryError, "gitleaks"):
            vault.publish_snapshot(
                str(self.remote), self.snapshot, ["notes/a.md"], self.run,
                "Fixture", "fixture@example.invalid", str(failing_gitleaks), {"notes/a.md": None})
        self.assertEqual(before, git(self.remote, "rev-parse", "main"))

    def test_missing_expected_blob_entry_is_rejected(self):
        self._write("notes/a.md", "Alpha note\n")
        self._write("notes/b.md", "Beta note\n")
        with self.assertRaisesRegex(DeliveryError, "expected_blobs"):
            vault.publish_snapshot(
                str(self.remote), self.snapshot, ["notes/a.md", "notes/b.md"], self.run,
                "Fixture", "fixture@example.invalid", str(self.gitleaks), {"notes/a.md": None})

    def test_immutable_snapshot_bytes_are_used_even_if_file_changes_later(self):
        self._write("notes/a.md", "Original\n")
        real = vault._publish_snapshot_once
        def mutate(*args, **kwargs):
            self._write('notes/a.md', 'later mutation\n')
            return real(*args, **kwargs)
        from unittest.mock import patch
        with patch.object(vault, '_publish_snapshot_once', side_effect=mutate):
            vault.publish_snapshot(str(self.remote), self.snapshot, ['notes/a.md'], self.run,
                'Fixture', 'fixture@example.invalid', str(self.gitleaks), {'notes/a.md': None})
        self.assertEqual('Original', git(self.remote, 'show', 'main:notes/a.md'))

    def test_unauthorized_repository_is_rejected(self):
        with self.assertRaisesRegex(DeliveryError, "authorized"):
            vault.publish_snapshot(
                "https://github.com/someone-else/other-repo", self.snapshot, ["notes/a.md"],
                self.run, "Fixture", "fixture@example.invalid", str(self.gitleaks),
                {"notes/a.md": None})

    def test_commit_blob_mutation_is_blocked_before_push(self):
        self._write('notes/a.md', 'verified\n')
        before = git(self.remote, 'rev-parse', 'main')
        real_git = vault._git
        def mutate(args, cwd=None, **kwargs):
            result = real_git(args, cwd, **kwargs)
            if args[:2] == ['--literal-pathspecs', 'add']:
                (cwd / 'notes/a.md').write_text('modified by filter\n')
                real_git(['add', '--', 'notes/a.md'], cwd)
            return result
        from unittest.mock import patch
        with patch.object(vault, '_git', side_effect=mutate):
            with self.assertRaisesRegex(DeliveryError, 'committed snapshot'):
                vault.publish_snapshot(str(self.remote), self.snapshot, ['notes/a.md'], self.run,
                    'Fixture', 'fixture@example.invalid', str(self.gitleaks), {'notes/a.md': None})
        self.assertEqual(before, git(self.remote, 'rev-parse', 'main'))

    def test_rewritten_push_destination_is_rejected(self):
        self._write('notes/a.md', 'verified\n')
        real_git = vault._git
        def rewritten(args, cwd=None, **kwargs):
            if args == ['remote', 'get-url', '--push', 'origin']:
                return 'https://example.invalid/unexpected.git'
            return real_git(args, cwd, **kwargs)
        from unittest.mock import patch
        with patch.object(vault, '_git', side_effect=rewritten):
            with self.assertRaisesRegex(DeliveryError, 'remote identity'):
                vault.publish_snapshot(str(self.remote), self.snapshot, ['notes/a.md'], self.run,
                    'Fixture', 'fixture@example.invalid', str(self.gitleaks), {'notes/a.md': None})


if __name__ == "__main__":
    unittest.main()
