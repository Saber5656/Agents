import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from harness.hermes_context import HermesExporter

SCHEMA = '''CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, model TEXT, model_config TEXT, system_prompt TEXT, parent_session_id TEXT, started_at REAL NOT NULL, ended_at REAL, title TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL, reasoning TEXT, reasoning_content TEXT, reasoning_details TEXT, codex_reasoning_items TEXT, codex_message_items TEXT);'''

class HermesContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name); self.home=self.root/'hermes'; self.vault=self.root/'vault'; self.home.mkdir(); self.vault.mkdir()
        db=sqlite3.connect(self.home/'state.db'); db.executescript(SCHEMA)
        db.executemany('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)', [('alpha','discord','u','m','{}','SYSTEM SECRET',None,1,None,'Alpha'),('a/b','cli','u','m','{}','',None,1,None,'slash'),('a_b','cli','u','m','{}','',None,1,None,'underscore')])
        db.executemany('INSERT INTO messages (session_id,role,content,tool_call_id,tool_calls,tool_name,timestamp,reasoning,reasoning_content) VALUES (?,?,?,?,?,?,?,?,?)', [('alpha','system','do not export',None,None,None,1,'private chain','private'),('alpha','user',json.dumps([{'type':'text','text':'hello'},{'type':'thinking','thinking':'hidden'}]),None,None,None,2,'hidden','hidden'),('alpha','assistant','answer api_key=KNOWN_SECRET','tc1','{"name":"run","arguments":{"x":1}}','run',3,'reason',None),('alpha','tool','result','tc1',None,'run',4,None,None),('a/b','user','one',None,None,None,1,None,None),('a_b','user','two',None,None,None,1,None,None)])
        db.commit(); db.close()
    def exporter(self, **kw): return HermesExporter(self.home,self.vault,env={'KNOWN_SECRET':'KNOWN_SECRET',**kw.pop('env',{})},**kw)
    def records(self): return list((self.vault/'01-Projects/hermes-context').glob('sessions/**/revisions/*/records.jsonl'))
    def test_native_records_privacy_json_content_and_tool_calls(self):
        self.assertEqual(self.exporter().export(),3); rows=[json.loads(x) for p in self.records() for x in p.read_text().splitlines()]; joined=json.dumps(rows)
        self.assertEqual({r['role'] for r in rows},{'user','assistant','tool'}); self.assertIn('hello',joined); self.assertIn('arguments',joined); self.assertNotIn('thinking',joined); self.assertNotIn('private chain',joined); self.assertNotIn('SYSTEM SECRET',joined); self.assertNotIn('KNOWN_SECRET',joined); self.assertIn('REDACTED',joined)
    def test_incremental_dedup_and_revision_on_message_or_metadata_change(self):
        e=self.exporter(); self.assertEqual(e.export('alpha'),1); self.assertEqual(e.export('alpha'),0); db=sqlite3.connect(self.home/'state.db'); db.execute("UPDATE sessions SET title='changed' WHERE id='alpha'"); db.execute("UPDATE messages SET content='new answer' WHERE session_id='alpha' AND role='tool'"); db.commit(); db.close(); self.assertEqual(e.export('alpha'),1); self.assertGreaterEqual(len(self.records()),2)
    def test_traversal_and_safe_id_collision_have_distinct_session_directories(self):
        self.exporter().export(); dirs=[p.parent.parent for p in self.records()]; self.assertEqual(len({str(p) for p in dirs}),3); self.assertFalse(any('..' in p.parts for p in dirs))
    def test_missing_or_corrupt_state_and_vault_fail(self):
        with self.assertRaises(FileNotFoundError): HermesExporter(self.home/'missing',self.vault).export()
        (self.home/'state.db').write_bytes(b'not sqlite')
        with self.assertRaises(Exception): HermesExporter(self.home,self.vault).export()
        with self.assertRaises(FileNotFoundError): HermesExporter(self.home,self.root/'gone').export()
    def test_env_file_expands_home_and_known_secret(self):
        env_home=self.root/'env-hermes'; env_home.mkdir(); import shutil; shutil.copy2(self.home/'state.db',env_home/'state.db'); env_file=self.root/'run.env'; env_file.write_text('HERMES_HOME="${TEST_HERMES_HOME}"\nAGENTS_VAULT_ROOT="${TEST_VAULT}"\n'); old=os.environ.copy(); os.environ.update(TEST_HERMES_HOME=str(env_home),TEST_VAULT=str(self.vault))
        try:
            from harness.hermes_context import main
            self.assertEqual(main(['--env-file',str(env_file),'--session-id','alpha']),0)
        finally: os.environ.clear(); os.environ.update(old)
    def test_metadata_only_revision_and_db_unchanged_by_export(self):
        import hashlib
        original=hashlib.sha256((self.home/'state.db').read_bytes()).hexdigest()
        e=self.exporter();e.export('alpha')
        self.assertEqual(original,hashlib.sha256((self.home/'state.db').read_bytes()).hexdigest())
        with sqlite3.connect(self.home/'state.db') as db: db.execute("UPDATE sessions SET title='new title' WHERE id='alpha'")
        self.assertEqual(e.export('alpha'),1)
    def test_corrupt_manifest_preserved_and_unknown_session_fails(self):
        e=self.exporter();e.export();e.state.write_text('{broken')
        with self.assertRaises(ValueError):e.export()
        self.assertEqual(e.state.read_text(),'{broken')
        e.state.unlink()
        with self.assertRaises(ValueError):e.export('missing')
    def test_private_files_and_lock_deadline(self):
        import fcntl,time
        e=self.exporter(timeout=0.1);e.export()
        for p in e.out.rglob('*'):
            if p.is_file():self.assertEqual(p.stat().st_mode & 0o777,0o600)
        with (e.out/'.export.lock').open('a+') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            start=time.monotonic()
            with self.assertRaises(TimeoutError):e.export()
            self.assertLess(time.monotonic()-start,1)
    def test_arbitrary_tool_json_type_is_visible_data(self):
        with sqlite3.connect(self.home/'state.db') as db:
            db.execute("UPDATE messages SET content=? WHERE session_id='alpha' AND role='tool'",(json.dumps({'type':['a','b'],'value':2}),))
        self.assertEqual(self.exporter().export('alpha'),1)
    def test_symlink_output_is_rejected(self):
        outside=self.root/'outside';outside.mkdir()
        (self.vault/'01-Projects').mkdir();(self.vault/'01-Projects/hermes-context').symlink_to(outside)
        with self.assertRaises(ValueError):self.exporter().export()
        self.assertEqual(list(outside.iterdir()),[])
if __name__=='__main__': unittest.main()
