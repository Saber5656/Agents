import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from harness.dashboard import Dashboard, Handler
from harness.status import build_status
from harness.tasks import TaskStore


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / 'vault'
        self.ui = self.root / 'ui'
        self.vault.mkdir()
        self.ui.mkdir()
        (self.ui / 'index.html').write_text('<meta name="dashboard-token" content="__DASHBOARD_TOKEN__">')
        (self.ui / 'app.js').write_text('export const ok = true;')
        self.app = Dashboard(agents_root=self.root, vault_root=self.vault, ui_dir=self.ui)
        self.now = datetime.now(timezone.utc)

    def report(self, tasks=()):
        return {'generated_at': self.now.isoformat(), 'tasks': list(tasks),
                'sources': {'tasks': {'state': 'available'}, 'service': {'state': 'missing'}},
                'service': {'jobs': []}, 'uncertain': False, 'uncertainties': []}

    def task(self, **kwargs):
        return {'task_id': 'task_123', 'purpose': 'Progress dashboard', 'lifecycle': 'running',
                'updated_at': self.now.isoformat(), 'next_action': 'Check the result',
                'evidence_links': [], 'completion_evidence': [], **kwargs}

    def refresh(self, report):
        with patch('harness.dashboard.build_status', return_value=report):
            return self.app.refresh()

    def server(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.dashboard = self.app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, path, *, token=True, method='GET', headers=None):
        conn = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        self.addCleanup(conn.close)
        values = {'X-Dashboard-Token': self.app.token} if token else {}
        values.update(headers or {})
        conn.request(method, path, headers=values)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_config_requires_explicit_existing_roots(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises((ValueError, RuntimeError)):
                Dashboard()

    def test_category_and_age_do_not_infer_live_workers_or_user_decisions(self):
        tasks = [self.task(task_id='fresh'),
                 self.task(task_id='old', updated_at=(self.now-timedelta(days=2)).isoformat()),
                 self.task(task_id='unknown', updated_at='invalid'),
                 self.task(task_id='future', updated_at=(self.now+timedelta(days=1)).isoformat()),
                 self.task(task_id='decision', lifecycle='awaiting_user'),
                 self.task(task_id='repair', lifecycle='quality_failed'),
                 self.task(task_id='done', lifecycle='verified'),
                 self.task(task_id='cancelled', lifecycle='cancelled')]
        rows = {t['id']: t for t in self.refresh(self.report(tasks))['tasks']}
        self.assertEqual(rows['fresh']['category'], 'active')
        for key in ('old', 'unknown', 'future', 'repair'):
            self.assertEqual(rows[key]['category'], 'backlog')
        self.assertEqual(rows['future']['freshness'], 'future')
        self.assertEqual(rows['decision']['category'], 'attention')
        self.assertEqual(rows['done']['category'], 'recent')
        self.assertIsNone(rows['done']['next_action'])
        self.assertEqual(rows['cancelled']['category'], 'closed')

    def test_missing_sources_are_visible_not_false_empty_success(self):
        result = self.refresh(self.report())
        self.assertTrue(any('service' in str(w).lower() for w in result['warnings']))
        self.assertTrue(any(s['name'] == 'vault' for s in result['sources']))
        bad = self.report()
        bad['sources']['tasks']['state'] = 'missing'
        empty = Dashboard(agents_root=self.root, vault_root=self.vault, ui_dir=self.ui)
        with patch('harness.dashboard.build_status', return_value=bad):
            self.assertTrue(empty.refresh()['stale'])

    def test_failed_refresh_retains_original_data_time_and_evidence_without_warning_growth(self):
        (self.vault/'result.md').write_text('saved result')
        first = self.refresh(self.report([self.task(evidence_links=['vault://result.md'])]))
        bad = self.report()
        bad['sources']['tasks']['state'] = 'corrupt'
        with patch('harness.dashboard.build_status', return_value=bad):
            one, two = self.app.refresh(), self.app.refresh()
        self.assertEqual(two['tasks'], first['tasks'])
        self.assertEqual(two['generated_at'], first['generated_at'])
        self.assertEqual(one['warnings'], two['warnings'])
        self.assertTrue(two['stale'])
        self.assertEqual(self.app.evidence(first['tasks'][0]['evidence'][0]['id']), 'saved result')

    def test_sources_report_record_time_and_no_private_prompts(self):
        report = self.report([self.task()])
        report['service']['jobs'] = [{'updated_at': self.now.isoformat(), 'prompt': 'PRIVATE_PROMPT'}]
        data = self.refresh(report)
        self.assertEqual(next(s for s in data['sources'] if s['name']=='tasks')['last_record_at'], self.now.isoformat())
        self.assertNotIn('PRIVATE_PROMPT', json.dumps(data))
        self.assertNotIn(str(self.root), json.dumps(data))

    def test_english_display_labels_match_source_and_original_is_preserved(self):
        self.app.labels = {'task_123': {'source_title': '日本語', 'title': 'English summary'}}
        row = self.refresh(self.report([self.task(purpose='日本語')]))['tasks'][0]
        self.assertEqual(row['title'], 'English summary')
        self.assertEqual(row['original_title'], '日本語')
        row = self.refresh(self.report([self.task(purpose='変更済み')]))['tasks'][0]
        self.assertNotEqual(row['title'], 'English summary')
        self.assertEqual(self.refresh(self.report([self.task(purpose='Today’s work')]))['tasks'][0]['title'], 'Today’s work')

    def test_duplicate_records_and_links_do_not_duplicate_display(self):
        row = self.task(evidence_links=['https://example.com/a']*2)
        result = self.refresh(self.report([row, row]))
        self.assertEqual(len(result['tasks']), 1)
        self.assertEqual(len(result['tasks'][0]['evidence']), 1)

    def test_vault_links_reject_traversal_and_symlink_escape(self):
        outside = self.root/'outside.txt'
        outside.write_text('private outside')
        (self.vault/'escape').symlink_to(outside)
        links = ['vault://../outside.txt', 'vault://%2e%2e/outside.txt',
                 'vault://escape', str(self.vault/'escape'), 'javascript:alert(1)',
                 'http://example.com', 'https://user:secret@example.com']
        data = self.refresh(self.report([self.task(evidence_links=links)]))
        for item in data['tasks'][0]['evidence']:
            self.assertEqual(item['availability'], 'unsupported')
            self.assertIsNone(self.app.evidence(item['id']))

    def test_evidence_is_revalidated_before_reading_and_binary_is_not_returned(self):
        path = self.vault/'safe.md'
        path.write_text('safe')
        binary = self.vault/'binary.dat'
        binary.write_bytes(b'\x00\xff\x00')
        rows = self.refresh(self.report([self.task(evidence_links=[str(path), str(binary)])]))['tasks'][0]['evidence']
        outside = self.root/'private.txt'; outside.write_text('private')
        path.unlink(); path.symlink_to(outside)
        self.assertIsNone(self.app.evidence(rows[0]['id']))
        self.assertIsNone(self.app.evidence(rows[1]['id']))

    def test_real_store_reads_preserve_bytes_and_live_wal(self):
        with TaskStore(agents_root=self.root, vault_root=self.vault) as store:
            store.create_task(purpose='Live fixture', execution_status='running')
            files = list((self.root/'.local').iterdir())
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
            result = self.app.refresh()
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root/'.local').iterdir() if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(result['tasks'][0]['title'], 'Live fixture')

    def test_reads_are_serialized(self):
        active = 0
        maximum = 0
        lock = threading.Lock()
        def collect(**kwargs):
            nonlocal active, maximum
            with lock:
                active += 1; maximum = max(maximum, active)
            time.sleep(0.01)
            result = self.report([self.task()])
            with lock: active -= 1
            return result
        with patch('harness.dashboard.build_status', side_effect=collect):
            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                results = list(pool.map(lambda _: self.app.refresh(), range(8)))
        self.assertEqual(maximum, 1)
        self.assertTrue(all(len(r['tasks']) == 1 for r in results))

    def test_browser_can_load_html_then_authenticate_api(self):
        server = self.server()
        status, headers, body = self.request(server, '/', token=False)
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn(self.app.token.encode(), body)
        self.assertEqual(self.request(server, '/api/status', token=False)[0], 403)
        with patch('harness.dashboard.build_status', return_value=self.report()):
            self.assertEqual(self.request(server, '/api/status')[0], 200)
        self.assertIn('javascript', self.request(server, '/app.js', token=False)[1]['Content-Type'])

    def test_host_origin_mutation_and_static_traversal_rejected(self):
        server = self.server()
        for token in (True, False):
            self.assertEqual(self.request(server, '/', token=token, headers={'Host':'attacker.example'})[0], 403)
        self.assertEqual(self.request(server, '/api/status', headers={'Origin':'https://attacker.example'})[0], 403)
        self.assertIn(self.request(server, '/api/status', method='POST')[0], (405,501))
        self.assertEqual(self.request(server, '/%2e%2e/private.txt', token=False)[0], 404)

    def test_evidence_html_is_plain_text_and_head_has_no_body(self):
        (self.vault/'note.html').write_text('<script>alert(1)</script>')
        row = self.refresh(self.report([self.task(evidence_links=['vault://note.html'])]))['tasks'][0]
        server = self.server()
        status, headers, body = self.request(server, '/api/evidence/'+row['evidence'][0]['id'])
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/plain'))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.request(server, '/', method='HEAD', token=False)[2], b'')


if __name__ == '__main__':
    unittest.main()
