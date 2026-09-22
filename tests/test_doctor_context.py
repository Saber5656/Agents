"""Regression tests for the scope and reliability of authentication diagnostics."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import runner as h


class DoctorStartupProvenanceTests(unittest.TestCase):
    def test_startup_declares_capture_method_for_terminal_mode(self):
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor({'PATH': ''}, {}, False, environment_mode='terminal')
        startup = report['startup']
        self.assertEqual(startup['environment_mode'], 'terminal')
        self.assertIn('login shell', startup['capture_method'])
        self.assertFalse(startup['launched_terminal_app'])
        self.assertEqual(startup['cross_launcher_auth_verification'], 'not_performed')

    def test_startup_declares_capture_method_for_current_mode(self):
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor({'PATH': ''}, {}, False, environment_mode='current')
        startup = report['startup']
        self.assertEqual(startup['environment_mode'], 'current')
        self.assertIn('current process', startup['capture_method'])
        self.assertFalse(startup['launched_terminal_app'])
        self.assertEqual(startup['cross_launcher_auth_verification'], 'not_performed')


class DoctorContextLabelTests(unittest.TestCase):
    def test_default_label_is_none_and_not_verified(self):
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor({'PATH': ''}, {}, False)
        self.assertIsNone(report['startup']['context_label'])
        self.assertNotIn('context_label_verified', report['startup'])

    def test_supplied_label_is_stored_but_self_reported_only(self):
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor({'PATH': ''}, {}, False, context_label='cursor-native')
        self.assertEqual(report['startup']['context_label'], 'cursor-native')
        self.assertEqual(report['startup']['context_label_source'], 'self_reported')
        self.assertNotIn('context_label_verified', report['startup'])
        self.assertEqual(report['startup']['cross_launcher_auth_verification'], 'not_performed')

    def test_cli_accepts_context_label_flag(self):
        report = {'tools': {'claude': {'auth_status_exit': 0, 'loggedIn': True}}}
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'AGENTS_VAULT_ROOT': directory}), \
                 patch.object(h, 'doctor', return_value=report) as doctor, \
                 contextlib.redirect_stdout(io.StringIO()):
                code = h.main(['--environment', 'current', '--env-file',
                               str(Path(directory)/'absent'), 'doctor',
                               '--context-label', 'codex-cli'])
        self.assertEqual(code, 0)
        self.assertEqual(doctor.call_args.kwargs['context_label'], 'codex-cli')


class DoctorHomeConfigComparisonTests(unittest.TestCase):
    def test_matching_home_and_config_dir_is_reported_same(self):
        current = {'HOME': '/Users/x', 'CLAUDE_CONFIG_DIR': '/Users/x/.claude-alt'}
        selected = {'HOME': '/Users/x', 'CLAUDE_CONFIG_DIR': '/Users/x/.claude-alt'}
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor(selected, current, False)
        ctx = report['claude_context']
        self.assertEqual(ctx['current']['home'], '/Users/x')
        self.assertEqual(ctx['selected']['home'], '/Users/x')
        self.assertTrue(ctx['same_home'])
        self.assertEqual(ctx['current']['claude_config_dir'], '/Users/x/.claude-alt')
        self.assertEqual(ctx['selected']['claude_config_dir'], '/Users/x/.claude-alt')
        self.assertTrue(ctx['same_claude_config_dir'])
        self.assertTrue(ctx['same_expected_config_path'])

    def test_differing_home_is_reported_not_same(self):
        current = {'HOME': '/Users/a'}
        selected = {'HOME': '/Users/b'}
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor(selected, current, False)
        ctx = report['claude_context']
        self.assertFalse(ctx['same_home'])
        self.assertEqual(ctx['current']['expected_config_path'], '/Users/a/.claude')
        self.assertEqual(ctx['selected']['expected_config_path'], '/Users/b/.claude')
        self.assertFalse(ctx['same_expected_config_path'])

    def test_missing_home_is_unknown_not_equal(self):
        current = {}
        selected = {'HOME': '/Users/b'}
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor(selected, current, False)
        ctx = report['claude_context']
        self.assertIsNone(ctx['current']['home'])
        self.assertIsNone(ctx['current']['expected_config_path'])
        self.assertIsNone(ctx['same_home'])
        self.assertIsNone(ctx['same_expected_config_path'])

    def test_explicit_claude_config_dir_absence_is_reported(self):
        current = {'HOME': '/Users/x'}
        selected = {'HOME': '/Users/x'}
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor(selected, current, False)
        ctx = report['claude_context']
        self.assertFalse(ctx['current']['claude_config_dir_set'])
        self.assertIsNone(ctx['current']['claude_config_dir'])
        self.assertFalse(ctx['selected']['claude_config_dir_set'])
        self.assertIsNone(ctx['same_claude_config_dir'])
        self.assertTrue(ctx['same_expected_config_path'])

    def test_config_override_changes_expected_location_even_with_matching_home(self):
        current = {'HOME': '/Users/example'}
        selected = {'HOME': '/Users/example', 'CLAUDE_CONFIG_DIR': '/custom/claude'}
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            context = h.doctor(selected, current)['claude_context']
        self.assertTrue(context['same_home'])
        self.assertEqual(context['selected']['expected_config_path'], '/custom/claude')
        self.assertEqual(context['selected']['config_path_source'], 'CLAUDE_CONFIG_DIR')
        self.assertFalse(context['same_expected_config_path'])

    def test_config_override_remains_known_without_home_and_is_not_expanded(self):
        env = {'CLAUDE_CONFIG_DIR': '~/custom-claude'}
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            context = h.doctor(env, env)['claude_context']
        self.assertIsNone(context['same_home'])
        self.assertEqual(context['selected']['expected_config_path'], '~/custom-claude')
        self.assertTrue(context['same_expected_config_path'])

    def test_no_path_home_expansion_from_caller_process(self):
        # HOME missing in selected env must not be silently filled from the
        # runner process's own Path.home(); it stays unknown.
        current = {'HOME': '/Users/a'}
        selected = {}
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor(selected, current, False)
        ctx = report['claude_context']
        self.assertIsNone(ctx['selected']['home'])
        self.assertIsNone(ctx['selected']['expected_config_path'])


class DoctorToolExecutableComparisonTests(unittest.TestCase):
    def test_missing_path_does_not_borrow_the_runners_search_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root/'claude'
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o700)
            empty = root/'empty'
            empty.mkdir()
            with patch.dict(os.environ, {'PATH': directory}), \
                 patch.object(h.os, 'defpath', str(empty)), \
                 patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
                info = h.doctor({}, {'PATH': directory})['tools']['claude']
        self.assertIsNone(info['executable'])
        self.assertEqual(info['current_executable'], str(binary))
        self.assertIsNone(info['executables_match'])

    def test_same_resolved_executable_is_reported_matching(self):
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')), \
             patch.object(h.shutil, 'which', return_value='/usr/local/bin/claude'), \
             patch.object(h.Path, 'resolve', return_value=Path('/usr/local/bin/claude')):
            report = h.doctor({'PATH': '/x'}, {'PATH': '/x'}, False)
        claude_info = report['tools']['claude']
        self.assertIn('current_resolved_executable', claude_info)
        self.assertIn('executables_match', claude_info)
        self.assertTrue(claude_info['executables_match'])

    def test_different_resolved_executables_reported_not_matching(self):
        def which(name, path=None):
            if path == '/current':
                return '/opt/current/bin/' + name
            return '/opt/selected/bin/' + name
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')), \
             patch.object(h.shutil, 'which', side_effect=which), \
             patch.object(h.Path, 'resolve', lambda self: self):
            report = h.doctor({'PATH': '/selected'}, {'PATH': '/current'}, False)
        claude_info = report['tools']['claude']
        self.assertFalse(claude_info['executables_match'])

    def test_both_missing_executables_are_not_a_match(self):
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')), \
             patch.object(h.shutil, 'which', return_value=None):
            report = h.doctor({'PATH': ''}, {}, False)
        claude_info = report['tools']['claude']
        self.assertIsNone(claude_info['executable'])
        self.assertIsNone(claude_info['current_executable'])
        self.assertIsNone(claude_info['executables_match'])


class DoctorAuthStatusParsingTests(unittest.TestCase):
    def test_unknown_or_logged_out_auth_is_not_a_healthy_diagnostic(self):
        for logged_in in (None, False):
            with self.subTest(logged_in=logged_in), tempfile.TemporaryDirectory() as directory:
                report = {'tools': {'claude': {'auth_status_exit': 0, 'loggedIn': logged_in}}}
                with patch.dict(os.environ, {'AGENTS_VAULT_ROOT': directory}), \
                     patch.object(h, 'doctor', return_value=report), \
                     contextlib.redirect_stdout(io.StringIO()):
                    code = h.main(['--environment', 'current', '--env-file',
                                   str(Path(directory)/'absent'), 'doctor'])
                self.assertEqual(code, 2)

    def _run_with_claude_stdout(self, stdout, code=0):
        def execute(argv, *args, **kwargs):
            if argv[:3] == ['claude', 'auth', 'status']:
                return h.ProcessResult(code, stdout)
            return h.ProcessResult(127, stderr='missing')
        with patch.object(h, 'execute', side_effect=execute):
            return h.doctor({'PATH': ''}, {}, False)

    def test_non_json_stdout_is_unknown_not_logged_out(self):
        report = self._run_with_claude_stdout('not json at all')
        claude_info = report['tools']['claude']
        self.assertIsNone(claude_info['loggedIn'])
        self.assertEqual(claude_info['auth_status_parse'], 'unparseable')

    def test_json_array_instead_of_object_is_unknown(self):
        report = self._run_with_claude_stdout(json.dumps(['not', 'an', 'object']))
        claude_info = report['tools']['claude']
        self.assertIsNone(claude_info['loggedIn'])
        self.assertEqual(claude_info['auth_status_parse'], 'unexpected_type')

    def test_json_object_with_non_bool_loggedin_is_unknown(self):
        report = self._run_with_claude_stdout(json.dumps({'loggedIn': 'yes'}))
        claude_info = report['tools']['claude']
        self.assertIsNone(claude_info['loggedIn'])
        self.assertEqual(claude_info['auth_status_parse'], 'unexpected_type')

    def test_explicit_logged_out_is_distinguished_from_unknown(self):
        report = self._run_with_claude_stdout(json.dumps({'loggedIn': False}))
        claude_info = report['tools']['claude']
        self.assertFalse(claude_info['loggedIn'])
        self.assertEqual(claude_info['auth_status_parse'], 'ok')

    def test_valid_json_logged_in_is_parsed(self):
        report = self._run_with_claude_stdout(json.dumps({'loggedIn': True, 'authMethod': 'oauth'}))
        claude_info = report['tools']['claude']
        self.assertTrue(claude_info['loggedIn'])
        self.assertEqual(claude_info['auth_status_parse'], 'ok')

    def test_secret_like_fields_are_not_copied_into_report(self):
        report = self._run_with_claude_stdout(json.dumps({
            'loggedIn': True, 'email': 'user@example.com', 'organization': 'Acme',
        }))
        serialized = json.dumps(report)
        self.assertNotIn('user@example.com', serialized)
        self.assertNotIn('Acme', serialized)

    def test_malformed_json_does_not_raise_and_diagnostic_continues(self):
        # doctor() must not raise even with garbage stdout; other tool entries
        # must still be present.
        report = self._run_with_claude_stdout('{"loggedIn": tru')
        self.assertIn('gh', report['tools'])
        self.assertIn('codex', report['tools'])
        self.assertEqual(report['tools']['claude']['auth_status_parse'], 'unparseable')


class DoctorNotesTests(unittest.TestCase):
    def test_notes_distinguish_path_match_from_shared_auth(self):
        with patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
            report = h.doctor({'PATH': ''}, {}, False)
        notes = ' '.join(report['notes'])
        self.assertIn('path', notes.lower())
        self.assertIn('does not', notes.lower())
