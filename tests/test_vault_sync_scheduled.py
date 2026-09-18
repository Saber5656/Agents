import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import vault_sync_scheduled as runner


class ScheduledRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agents = self.root / 'agents'
        self.state = self.agents / '.local/vault-context-sync'
        self.state.mkdir(parents=True)
        self.work = self.root / 'scheduled'
        self.env = self.root / '.env'
        self.env.write_text(f'AGENTS_ROOT="{self.agents}"\nAGENTS_VAULT_ROOT="{self.root / "vault"}"\n')
        self.before = self.receipt(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1))
        self.write('last-result.json', self.before)
        self.write('last-success.json', self.before)
        self.write('prepared.json', {'withheld': [{'path': 'known.md', 'reason': 'source_read_timeout'}]})
        self.publication = patch.object(runner, '_run_publication')
        self.publisher = self.publication.start()
        self.addCleanup(self.publication.stop)
        self.clean = patch.object(runner, '_git_clean', return_value=True)
        self.clean.start()
        self.addCleanup(self.clean.stop)

    @staticmethod
    def receipt(when=None):
        return {'status': 'published', 'checked_at': (when or dt.datetime.now(dt.timezone.utc)).isoformat(),
                'commit': 'a' * 40, 'observed_remote': 'a' * 40, 'withheld_count': 1}

    def write(self, name, value):
        (self.state / name).write_text(json.dumps(value))

    def execute(self, *, mutate=None, terminal=True, returncode=0, usage=None, stdout_suffix=''):
        def publish(argv, **kwargs):
            current = self.receipt()
            self.write('last-result.json', current)
            self.write('last-success.json', current)
            if mutate:
                mutate(current)
            return subprocess.CompletedProcess(argv, 0, '', '')
        self.publisher.side_effect = publish
        def fake(argv, **kwargs):
            events = [{'type': 'turn.started'}]
            if terminal:
                events.append({'type': 'turn.completed', 'usage': usage or {'input_tokens': 17, 'output_tokens': 5}})
            return subprocess.CompletedProcess(argv, returncode,
                '\n'.join(json.dumps(e) for e in events) + stdout_suffix, '')
        with patch.object(runner, 'run_command', side_effect=fake) as call:
            result = runner.run_once(self.env, self.work)
        return result, call

    def assert_failed(self, result):
        self.assertIn(result['status'], {'failed', 'blocked'})

    def test_verified_publication_and_fixed_luna_command(self):
        result, call = self.execute()
        self.assertEqual('published', result['status'])
        argv = call.call_args.args[0]
        self.assertEqual('gpt-5.6-luna', argv[argv.index('-m') + 1])
        for value in ['--ephemeral', '--ignore-user-config', 'model_reasoning_effort="max"',
                      'approval_policy="never"',
                      'project_doc_max_bytes=0']:
            self.assertIn(value, argv)
        self.assertEqual(self.work.resolve(), call.call_args.kwargs['cwd'])
        self.assertEqual('read-only', argv[argv.index('--sandbox') + 1])
        self.assertNotIn('--add-dir', argv)
        self.assertNotIn('sandbox_workspace_write.network_access=true', argv)
        self.assertEqual('max', result['requested_effort'])
        self.assertIsNone(result['observed_model'])
        self.assertIn('--publish', self.publisher.call_args.args[0])

    def test_prepared_is_not_published(self):
        def change(current):
            current['status'] = 'prepared'
            self.write('last-result.json', current)
            self.write('last-success.json', current)
        self.assert_failed(self.execute(mutate=change)[0])

    def test_stale_result_is_not_success(self):
        def change(current):
            self.write('last-result.json', self.before)
            self.write('last-success.json', self.before)
        self.assert_failed(self.execute(mutate=change)[0])

    def test_missing_or_mismatched_success_receipt_fails(self):
        for mode in ['missing', 'commit', 'time']:
            with self.subTest(mode=mode):
                def change(current):
                    if mode == 'missing':
                        (self.state / 'last-success.json').unlink()
                    else:
                        current['commit' if mode == 'commit' else 'checked_at'] = 'different'
                        self.write('last-success.json', current)
                self.assert_failed(self.execute(mutate=change)[0])

    def test_mismatched_remote_fails_even_when_both_receipts_match(self):
        def change(current):
            current['observed_remote'] = 'b' * 40
            self.write('last-result.json', current)
            self.write('last-success.json', current)
        self.assert_failed(self.execute(mutate=change)[0])

    def test_malformed_freshness_or_receipt_is_not_success(self):
        for bad in ['invalid', '2020-01-01T00:00:00+00:00']:
            with self.subTest(bad=bad):
                def change(current):
                    current['checked_at'] = bad
                    self.write('last-result.json', current)
                    self.write('last-success.json', current)
                self.assert_failed(self.execute(mutate=change)[0])
        def corrupt(current):
            (self.state / 'last-result.json').write_text('{broken')
        self.assert_failed(self.execute(mutate=corrupt)[0])

    def test_terminal_and_exit_success_are_required(self):
        self.assert_failed(self.execute(terminal=False)[0])
        self.assert_failed(self.execute(returncode=1)[0])

    def test_timeout_and_start_failure_are_recorded(self):
        for error in [subprocess.TimeoutExpired(['codex'], 1, output='partial'), FileNotFoundError('codex missing')]:
            with self.subTest(error=type(error).__name__), patch.object(runner, 'run_command', side_effect=error):
                self.assert_failed(runner.run_once(self.env, self.work))
                self.assertTrue((self.work / 'last-result.json').is_file())

    def test_overlapping_run_never_starts_model(self):
        self.work.mkdir()
        with (self.work / 'runner.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(runner, 'run_command') as call:
                self.assertEqual('already_running', runner.run_once(self.env, self.work)['status'])
                call.assert_not_called()
            self.publisher.assert_not_called()

    def test_dirty_code_does_not_start_model(self):
        with patch.object(runner, '_git_clean', return_value=False), patch.object(runner, 'run_command') as call:
            self.assert_failed(runner.run_once(self.env, self.work))
            call.assert_not_called()
            self.publisher.assert_not_called()

    def test_new_withheld_alerts_but_known_reason_oscillation_does_not(self):
        def oscillate(current):
            self.write('prepared.json', {'withheld': [{'path': 'known.md', 'reason': 'secret_scan_rejected'}]})
        self.execute(mutate=oscillate)
        self.assertFalse((self.work / 'alert.json').exists())
        def new(current):
            self.write('prepared.json', {'withheld': [{'path': 'new.md', 'reason': 'source_read_timeout'}]})
        self.execute(mutate=new)
        self.assertTrue((self.work / 'alert.json').exists())

    def test_failure_alert_dedup_and_recovery_allow_recurrence(self):
        self.execute(terminal=False)
        first = (self.work / 'alert.json').read_bytes()
        self.execute(terminal=False)
        self.assertEqual(first, (self.work / 'alert.json').read_bytes())
        self.execute()
        self.execute(terminal=False)
        self.assertNotEqual(first, (self.work / 'alert.json').read_bytes())

    def test_usage_survives_long_output_and_logs_are_private(self):
        usage = {'input_tokens': 23, 'output_tokens': 7, 'cached_input_tokens': 9}
        result, _ = self.execute(usage=usage, stdout_suffix='\n' + json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'x' * 25000}}))
        self.assertEqual(usage, result['usage'])
        self.assertNotIn('stdout', result)
        self.assertNotIn('stderr', result)
        logs = list(self.work.rglob('*.jsonl'))
        self.assertTrue(logs)
        self.assertTrue(any(p.stat().st_size > 25000 for p in logs))
        self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in logs))

    def test_publisher_failure_still_gets_readonly_ai_diagnosis(self):
        for error in [subprocess.TimeoutExpired(['publisher'], 1), FileNotFoundError('publisher missing')]:
            with self.subTest(error=type(error).__name__):
                self.publisher.side_effect = error
                with patch.object(runner, 'run_command', return_value=subprocess.CompletedProcess(
                    ['codex'], 0, json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1}}), '')) as model:
                    self.assert_failed(runner.run_once(self.env, self.work))
                    model.assert_called_once()
                    argv = model.call_args.args[0]
                    self.assertEqual('read-only', argv[argv.index('--sandbox') + 1])

    def test_readonly_diagnostic_cannot_forge_host_result(self):
        # Host observes failure before the model process begins. A bogus child
        # write in this mock cannot change that already captured evidence.
        def publish(argv, **kwargs):
            current = self.receipt()
            current.update(status='blocked', reason='fixture conflict')
            self.write('last-result.json', current)
            return subprocess.CompletedProcess(argv, 2, '', '')
        self.publisher.side_effect = publish
        def model(argv, **kwargs):
            current = self.receipt()
            self.write('last-result.json', current)
            self.write('last-success.json', current)
            return subprocess.CompletedProcess(argv, 0, json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1}}), '')
        with patch.object(runner, 'run_command', side_effect=model):
            self.assert_failed(runner.run_once(self.env, self.work))

    def test_model_environment_omits_publisher_credentials(self):
        with self.env.open('a') as handle:
            handle.write('GITHUB_TOKEN=fixture-value-for-test\nOPENAI_API_KEY=fixture-value-for-test\n')
        result, call = self.execute()
        for key in ['GITHUB_TOKEN', 'OPENAI_API_KEY', 'AGENTS_VAULT_ROOT']:
            self.assertNotIn(key, call.call_args.kwargs['env'])
        self.assertIn('HOME', call.call_args.kwargs['env'])
        self.assertEqual('published', result['status'])

    def test_prepared_failure_alerts_even_with_known_withheld(self):
        def prepared(current):
            current['status'] = 'prepared'
            self.write('last-result.json', current)
        self.assert_failed(self.execute(mutate=prepared)[0])
        self.assertTrue((self.work / 'alert.json').exists())

    def test_resolved_withheld_recurrence_alerts(self):
        def pending(current):
            self.write('prepared.json', {'withheld': [{'path': 'new.md', 'reason': 'source_read_timeout'}]})
        self.execute(mutate=pending)
        original = (self.work / 'alert.json').read_bytes()
        self.execute(mutate=lambda current: self.write('prepared.json', {'withheld': []}))
        self.execute(mutate=pending)
        alert = json.loads((self.work / 'alert.json').read_text())
        self.assertTrue(alert['active'])
        self.assertNotEqual(original, (self.work / 'alert.json').read_bytes())

    def test_changed_publication_failure_reason_is_new_alert(self):
        def blocked(reason):
            def change(current):
                current.update(status='blocked', error_type='RuntimeError', reason=reason)
                self.write('last-result.json', current)
            return change
        self.execute(mutate=blocked('connection unavailable'))
        original = (self.work / 'alert.json').read_bytes()
        self.execute(mutate=blocked('remote conflict in selected document'))
        self.assertNotEqual(original, (self.work / 'alert.json').read_bytes())

    def test_known_secret_is_redacted_from_durable_logs(self):
        import uuid
        secret = uuid.uuid4().hex
        with self.env.open('a') as handle:
            handle.write(f'TEST_SECRET={secret}\n')
        self.execute(stdout_suffix='\n' + json.dumps({'type': 'item.completed', 'item': {'text': secret}}))
        logs = list(self.work.rglob('*.jsonl'))
        self.assertTrue(logs)
        self.assertTrue(all(secret not in path.read_text() for path in logs))

    def test_git_clean_requires_tracked_existing_unmodified_files(self):
        self.clean.stop()
        subprocess.run(['git', 'init', str(self.agents)], capture_output=True, check=True)
        script = self.agents / 'script.py'; script.write_text('print(1)\n')
        self.assertFalse(runner._git_clean(self.agents, [script]))
        subprocess.run(['git', '-C', str(self.agents), 'add', 'script.py'], check=True)
        subprocess.run(['git', '-C', str(self.agents), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture'], capture_output=True, check=True)
        self.assertTrue(runner._git_clean(self.agents, [script]))
        script.write_text('print(2)\n')
        self.assertFalse(runner._git_clean(self.agents, [script]))
        script.unlink()
        self.assertFalse(runner._git_clean(self.agents, [script]))


if __name__ == '__main__':
    unittest.main()
