import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from harness.installation import inventory, deploy_file, rollback, digest, roots


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source'; self.source.mkdir()
        self.install = self.root/'installed'; self.install.mkdir()
        self.vault = self.root/'vault'; self.vault.mkdir()
        for root, text in [(self.source,'new'),(self.install,'old')]:
            (root/'demo').mkdir(); (root/'demo/SKILL.md').write_text(text)
        (self.install/'demo/local.txt').write_text('preserve')

    def test_inventory_deduplicates_alias_and_marks_vendor(self):
        alias = self.root/'alias'; alias.symlink_to(self.install)
        vendor = self.root/'vendor'; vendor.mkdir()
        (vendor/'package').mkdir(); (vendor/'package/SKILL.md').write_text('vendor')
        rows = inventory(self.source, [self.install, alias], [vendor])
        self.assertEqual(2, len(rows))
        self.assertEqual(1, len(rows[0]['installed']))
        self.assertEqual('vendor', rows[1]['ownership'])

    def test_deploy_readback_rollback_preserves_unrelated(self):
        target = self.install/'demo/SKILL.md'
        receipt = deploy_file(self.source/'demo', self.install/'demo', 'SKILL.md', digest(target), self.vault)
        self.assertEqual('new', target.read_text())
        self.assertEqual(digest(target), receipt['effective_digest'])
        rollback(Path(receipt['record']))
        self.assertEqual('old', target.read_text())
        self.assertEqual('preserve', (self.install/'demo/local.txt').read_text())
        rollback(Path(receipt['record']))

    def test_stale_preimage_or_post_install_edit_not_overwritten(self):
        target = self.install/'demo/SKILL.md'
        with self.assertRaisesRegex(ValueError,'preimage'):
            deploy_file(self.source/'demo', self.install/'demo', 'SKILL.md', 'wrong', self.vault)
        receipt = deploy_file(self.source/'demo', self.install/'demo', 'SKILL.md', digest(target), self.vault)
        target.write_text('user edit')
        with self.assertRaisesRegex(ValueError,'changed'):
            rollback(Path(receipt['record']))
        self.assertEqual('user edit',target.read_text())

    def test_escape_symlink_and_missing_roots_rejected(self):
        with self.assertRaisesRegex(ValueError, 'AGENTS_ROOT'):
            roots({})
        (self.source/'demo/link').symlink_to(self.install/'demo/SKILL.md')
        with self.assertRaises(ValueError):
            deploy_file(self.source/'demo', self.install/'demo', 'link', None, self.vault)
        with self.assertRaises(ValueError):
            deploy_file(self.source/'demo', self.install/'demo', '../SKILL.md', None, self.vault)

    def test_prepared_receipt_recovers_crash_after_replacement(self):
        from harness.installation import atomic_write
        target = self.install/'demo/SKILL.md'
        def crash(path, data, mode=0o600):
            if Path(path).name == 'receipt.json' and b'"installed"' in data:
                raise OSError('simulated interruption')
            atomic_write(path, data, mode)
        with patch('harness.installation.atomic_write', side_effect=crash):
            with self.assertRaises(OSError):
                deploy_file(self.source/'demo',self.install/'demo','SKILL.md',digest(target),self.vault)
        record = next(self.vault.rglob('receipt.json'))
        rollback(record)
        self.assertEqual('old',target.read_text())

    def test_added_file_can_be_rolled_back(self):
        (self.source/'demo/new.md').write_text('new file')
        r = deploy_file(self.source/'demo',self.install/'demo','new.md',None,self.vault)
        rollback(Path(r['record']))
        self.assertFalse((self.install/'demo/new.md').exists())


if __name__ == '__main__':
    unittest.main()
