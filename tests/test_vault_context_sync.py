import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import vault_context_sync as sync


class ExportTests(unittest.TestCase):
    def test_full_document_is_preserved_except_private_values(self):
        fake_home = '/' + 'Users' + '/' + 'example'
        fake_linux = '/' + 'home' + '/' + 'example'
        text = '# Context\nFirst\n' + fake_home + '/dev/agents/a.py\n' + fake_linux + '/notes\n' + 'end\n' * 200
        cleaned = sync.sanitize(text, {'AGENTS_ROOT': fake_home + '/dev/agents', 'HOME': fake_home})
        self.assertIn('$AGENTS_ROOT/a.py', cleaned)
        self.assertIn('$HOME/notes', cleaned)
        self.assertTrue(cleaned.endswith('end\n' * 200))
        self.assertNotIn('/' + 'Users' + '/', cleaned)

    def test_provider_reasoning_markdown_is_withheld(self):
        with self.assertRaises(ValueError):
            sync.sanitize('```json\n{"channel": "analysis", "text": "private"}\n```', {})

    def test_secrets_are_redacted(self):
        value = 'ghp_' + 'a' * 30
        self.assertNotIn(value, sync.sanitize('token=' + value, {}))

    def test_home_path_examples_and_bare_prefixes_pass_public_filter(self):
        from harness.delivery import public_text
        prefix = '/' + 'Users' + '/'
        text = 'example ' + prefix + '<name>/notes and `' + prefix + '`'
        cleaned = sync.sanitize(text, {})
        public_text(cleaned)
        self.assertNotIn(prefix, cleaned)

    def test_discovery_excludes_copies_binaries_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ['01-Projects/demo/task.md', '01-Projects/demo/snapshots/old.md',
                         '01-Projects/demo/sources/README.md', '01-Projects/demo/image.png',
                         '.obsidian/config.md', '03-Contexts/Reports/result.md']:
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('record')
            (root / '01-Projects/demo/link.md').symlink_to(root / '03-Contexts/Reports/result.md')
            files, excluded = sync.discover(root)
            self.assertEqual({'01-Projects/demo/task.md', '03-Contexts/Reports/result.md'}, set(files))
            self.assertTrue(excluded)

    def test_changed_during_read_is_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'record.md'; p.write_text('before')
            before = sync.fingerprint(p)
            p.write_text('after and longer')
            with self.assertRaises(ValueError):
                sync.read_document(p, before, timeout=2)

    def test_index_uses_source_time_and_links_not_poll_time(self):
        entries = {'01-Projects/demo/task.md': {'mtime_ns': 1_700_000_000_000_000_000, 'sha256': 'a'}}
        first = sync.index_document(entries, [])
        self.assertEqual(first, sync.index_document(entries, []))
        self.assertIn('01-Projects/demo/task.md', first)
        self.assertIn('2023-', first)

    def test_scanner_multiple_findings_withholds_one_file_and_keeps_full_other_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'vault'; runtime = Path(tmp) / 'runtime'
            (root / '01-Projects').mkdir(parents=True)
            (root / '01-Projects/safe.md').write_text('full document\n' * 100)
            (root / '01-Projects/private.md').write_text('two scanner findings')
            scanner = Path(tmp) / 'scanner'
            scanner.write_text('#!/usr/bin/env python3\nimport json,sys\np=sys.argv[sys.argv.index("--report-path")+1]\nopen(p,"w").write(json.dumps([{"File":"01-Projects/private.md"},{"File":"01-Projects/private.md"}]))\nsys.exit(1)\n')
            scanner.chmod(0o755)
            files, manifest = sync.prepare(root, runtime, {}, str(scanner))
            self.assertNotIn('01-Projects/private.md', files)
            self.assertEqual(1, len(manifest['withheld']))
            self.assertEqual('full document\n' * 100, (runtime / 'snapshot/01-Projects/safe.md').read_text())

    def test_interrupted_publication_is_reconciled_before_new_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            sync.atomic_json(runtime / 'pending-publication.json',
                {'selected_blob': {'note.md': 'published-first-version'}, 'expected_blobs': {'note.md': None}})
            with patch.object(sync, 'remote_tree', return_value=('commit', {'note.md': 'published-first-version'})):
                sync.reconcile_pending(runtime)
            import json
            ledger = json.loads((runtime / 'published-blobs.json').read_text())
            self.assertEqual('published-first-version', ledger['note.md'])
            self.assertFalse((runtime / 'pending-publication.json').exists())

    def test_recovery_does_not_adopt_unexpected_remote_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            sync.atomic_json(runtime / 'pending-publication.json',
                {'selected_blob': {'note.md': 'ours'}, 'expected_blobs': {'note.md': 'original'}})
            with patch.object(sync, 'remote_tree', return_value=('commit', {'note.md': 'someone-else'})):
                with self.assertRaisesRegex(RuntimeError, 'conflict'):
                    sync.reconcile_pending(runtime)
            self.assertFalse((runtime / 'published-blobs.json').exists())


if __name__ == '__main__':
    unittest.main()
