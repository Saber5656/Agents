import tempfile
import unittest
from pathlib import Path
from harness.instructions import merge_block, configure, bootstrap, InstructionError

class InstructionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name); self.root=self.home/'repo'; self.root.mkdir()
        (self.root/'skills').mkdir(); self.vault=self.home/'vault'; self.vault.mkdir()
        (self.root/'COMMON-AGENTS.md').write_text('policy')
        self.env=self.root/'.env'
        self.env.write_text(f'AGENTS_ROOT="{self.root}"\nSKILLS_ROOT="{self.root}/skills"\nAGENTS_VAULT_ROOT="{self.vault}"\n')
    def test_preserves_original_and_is_idempotent(self):
        original='Keep my identity.\n'
        once=merge_block(original,bootstrap())
        self.assertTrue(once.startswith(original)); self.assertEqual(once,merge_block(once,bootstrap()))
        updated=merge_block(once,bootstrap()+'\nupdated')
        self.assertIn('updated',updated); self.assertEqual(1,updated.count('<!-- agents:begin -->'))
    def test_rejects_broken_or_duplicated_markers(self):
        for s in ('<!-- agents:begin -->', '<!-- agents:end -->', '<!-- agents:end -->\n<!-- agents:begin -->', merge_block('',bootstrap())*2):
            with self.assertRaises(InstructionError): merge_block(s,bootstrap())
    def test_install_preserves_native_files_and_links_environment(self):
        soul=self.home/'.hermes/SOUL.md'; soul.parent.mkdir(); soul.write_text('identity'); soul.chmod(0o640)
        plan=configure(self.env,self.home,['hermes','devin'],apply=False)
        self.assertEqual(soul.read_text(),'identity'); self.assertFalse((self.home/'.config/agents/environment.env').exists())
        result=configure(self.env,self.home,['hermes','devin'],apply=True)
        self.assertTrue(soul.read_text().startswith('identity')); self.assertEqual(soul.stat().st_mode & 0o777,0o640); self.assertTrue((self.home/'.config/devin/AGENTS.md').is_file())
        self.assertEqual(self.env.resolve(),(self.home/'.config/agents/environment.env').resolve())
        before=soul.read_bytes(); configure(self.env,self.home,['hermes','devin'],apply=True)
        self.assertEqual(before,soul.read_bytes())
        backups=list((self.root/'.local/agent-instructions/backups').glob('*'))
        self.assertTrue(any(p.read_text()=='identity' for p in backups))
    def test_common_policy_symlink_is_not_overwritten(self):
        path=self.home/'.codex/AGENTS.md';path.parent.mkdir();path.symlink_to(self.root/'COMMON-AGENTS.md')
        configure(self.env,self.home,['codex'],apply=True)
        self.assertTrue(path.is_symlink());self.assertEqual((self.root/'COMMON-AGENTS.md').read_text(),'policy')
    def test_unknown_symlink_and_missing_vault_fail_before_writes(self):
        p=self.home/'.hermes/SOUL.md';p.parent.mkdir();p.symlink_to(self.env)
        with self.assertRaises(InstructionError):configure(self.env,self.home,['hermes'],apply=True)
        self.assertFalse((self.home/'.config/agents/environment.env').exists())
        p.unlink();self.vault.rmdir()
        with self.assertRaises(InstructionError):configure(self.env,self.home,['hermes'],apply=True)
    def test_conflicting_env_link_is_preserved(self):
        p=self.home/'.config/agents/environment.env';p.parent.mkdir(parents=True);p.write_text('keep')
        with self.assertRaises(InstructionError):configure(self.env,self.home,['devin'],apply=True)
        self.assertEqual(p.read_text(),'keep')
    def test_cursor_frontmatter_and_cascade_size(self):
        configure(self.env,self.home,['cursor','devin'],apply=True)
        text=(self.home/'.cursor/rules/agents.mdc').read_text()
        self.assertTrue(text.startswith('---\n'));self.assertIn('alwaysApply: true',text)
        self.assertLess(len((self.home/'.codeium/windsurf/memories/global_rules.md').read_text()),6000)
        p=self.home/'.codeium/windsurf/memories/global_rules.md';p.write_text('x'*6000)
        with self.assertRaises(InstructionError):configure(self.env,self.home,['devin'],apply=True)
    def test_bootstrap_is_provider_neutral_and_vault_complete(self):
        text=bootstrap()
        for term in ('COMMON-AGENTS.md','AGENTS_VAULT_ROOT','environment.env','主担当','全文','再開'):
            self.assertIn(term,text)
        self.assertNotIn('/Users/',text)

if __name__=='__main__':unittest.main()
