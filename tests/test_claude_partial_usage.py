import json
import os
from pathlib import Path
import tempfile
import unittest

from harness import runner as h
from harness.usage import build_usage_report, emit_usage


def event(**data):
    return json.dumps(data) + '\n'


def message(identity='msg-1', **overrides):
    usage = dict(input_tokens=2, cache_creation_input_tokens=15100,
                 cache_read_input_tokens=0, output_tokens=2)
    usage.update(overrides)
    return event(type='assistant', message={'id': identity, 'model': 'claude-sonnet-5',
                                            'usage': usage, 'content': []})


def partial_stream():
    return (message() + message() + message('msg-2', cache_creation_input_tokens=124,
                                            cache_read_input_tokens=15100, output_tokens=16))


PARTIAL = {'input_tokens': 4, 'cache_creation_input_tokens': 15224,
           'cache_read_input_tokens': 15100}


class ClaudeUsageTests(unittest.TestCase):
    def test_partial_deduplicates_message_ids_and_omits_placeholder_outputs(self):
        result = h.classify('claude', partial_stream(), '', 124)
        self.assertEqual(result.usage, PARTIAL)
        self.assertEqual(result.actual_model, 'claude-sonnet-5')
        self.assertEqual(result.usage_info['completeness'], 'partial')
        self.assertEqual(result.usage_info['source'], 'assistant_messages')
        self.assertEqual(result.usage_info['message_count'], 2)
        self.assertIn('output_tokens', result.usage_info['unavailable_fields'])
        self.assertNotEqual(result.status, 'completed')

    def test_result_replaces_messages_without_double_counting(self):
        total = dict(PARTIAL, output_tokens=98)
        output = partial_stream() + event(type='result', subtype='success', result='OK', usage=total)
        result = h.classify('claude', output, '', 0)
        self.assertEqual(result.usage, total)
        self.assertEqual(result.usage_info['completeness'], 'complete')
        self.assertEqual(result.usage_info['source'], 'result')

    def test_zeroed_crash_result_does_not_erase_observed_inputs(self):
        output = partial_stream() + event(type='result', subtype='error_during_execution',
                                          is_error=True, usage={'input_tokens': 0, 'output_tokens': 0})
        result = h.classify('claude', output, '', 1)
        self.assertEqual(result.usage, PARTIAL)
        self.assertEqual(result.usage_info['completeness'], 'partial')

    def test_zeroed_crash_without_step_observations_is_missing_not_zero(self):
        output=event(type='result', subtype='error_during_execution', is_error=True,
                     usage={'input_tokens':0,'output_tokens':0})
        result=h.classify('claude',output,'',1)
        self.assertIsNone(result.usage)
        self.assertEqual(result.usage_info['completeness'], 'missing')
        self.assertFalse(result.usage_info['available'])

    def test_error_result_retains_reported_usage_without_claiming_completeness(self):
        output = event(type='result', subtype='error_max_budget_usd', is_error=True,
                       usage={'input_tokens': 14, 'output_tokens': 8})
        result = h.classify('claude', output, '', 1)
        self.assertEqual(result.usage, {'input_tokens': 14, 'output_tokens': 8})
        self.assertEqual(result.usage_info['completeness'], 'partial')

    def test_unkeyed_nested_and_conflicting_messages_are_not_guessed(self):
        output = (message(None) + message('conflict') + message('conflict', input_tokens=99)
                  + event(type='assistant', parent_tool_use_id='agent',
                          message={'id':'child', 'usage': {'input_tokens': 100}})
                  + event(type='user', message={'usage': {'input_tokens': 700}}))
        result = h.classify('claude', output, '', 1)
        self.assertIsNone(result.usage)
        self.assertEqual(result.usage_info['completeness'], 'missing')
        self.assertEqual(result.usage_info['conflicting_message_ids'], ['conflict'])

    def test_timeout_interrupt_and_pending_collection_preserve_observations(self):
        for response, status in [(h.ProcessResult(124, partial_stream(), timed_out=True), 'timeout'),
                                 (h.ProcessResult(130, partial_stream()), 'interrupted'),
                                 (h.ProcessResult(0, partial_stream(), output_pending=True), 'incomplete')]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); (root/'work').mkdir(); (root/'vault').mkdir()
                job=h.Job(root/'work', root/'vault', 'Review', fallback=False)
                result=h.run_job(job, {'PATH':os.environ['PATH']}, lambda *a:response)
                saved=json.loads((Path(result['run_dir'])/'result.json').read_text())
                self.assertEqual(saved['status'], status)
                self.assertEqual(saved['attempts'][0]['usage'], PARTIAL)
                self.assertEqual(saved['attempts'][0]['usage_info']['completeness'], 'partial')
                self.assertEqual(saved['usage']['attempts_partial'], 1)
                self.assertEqual(saved['usage']['totals'], PARTIAL)
                report=build_usage_report(root/'vault')
                group=report['usage_by_provider_model'][0]
                self.assertEqual(group['usage_partial_attempts'], 1)
                self.assertEqual(group['usage_complete_attempts'], 0)
                self.assertEqual(group['token_totals_by_completeness']['partial'], PARTIAL)
                self.assertIn('usage_partial=1', emit_usage(report))

    def test_complete_observation_wins_over_richer_partial_snapshot(self):
        partial={'attempt_id':'same','status':'timeout','usage':PARTIAL,
                 'usage_info':{'completeness':'partial'}}
        complete={'attempt_id':'same','status':'completed','usage':{'input_tokens':9},
                  'usage_info':{'completeness':'complete'}}
        summary=h._usage_summary([complete, partial])
        self.assertEqual(summary['totals'], {'input_tokens':9})
        self.assertEqual(summary['attempts_complete'], 1)

    def test_dead_attempt_recovery_retains_partial_usage_before_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'0-claude-stdout.jsonl').write_text(partial_stream())
            summary={'mode':'review','status':'running','attempts':[
                {'attempt_number':0,'provider':'claude','requested_model':'sonnet','status':'running'}]}
            finished=h._reconcile_captured_attempt(root, summary,
                {'attempt':0,'provider':'claude','status':'running'}, {})
            self.assertFalse(finished)
            self.assertEqual(summary['attempts'][0]['usage'], PARTIAL)
            self.assertEqual(summary['attempts'][0]['status'], 'interrupted')
            self.assertEqual(json.loads((root/'result.json').read_text())['usage']['attempts_partial'], 1)

    def test_report_recovers_old_missing_usage_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temp:
            vault=Path(temp); run=vault/'01-Projects/agent-runs/old';run.mkdir(parents=True)
            record={'attempts':[{'attempt_id':'old:0','attempt_number':0,'provider':'claude',
                                 'status':'timeout','usage':None}]}
            original=json.dumps(record)
            (run/'result.json').write_text(original)
            (run/'0-claude-stdout.jsonl').write_text(partial_stream())
            report=build_usage_report(vault)
            group=report['usage_by_provider_model'][0]
            self.assertEqual(group['token_totals'], PARTIAL)
            self.assertEqual(group['usage_partial_attempts'], 1)
            self.assertEqual((run/'result.json').read_text(), original)

    def test_partial_stream_cannot_replace_existing_legacy_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            vault=Path(temp); run=vault/'01-Projects/agent-runs/legacy';run.mkdir(parents=True)
            legacy={'input_tokens':50, 'output_tokens':80}
            (run/'result.json').write_text(json.dumps({'attempts':[
                {'attempt_number':0,'provider':'claude','status':'completed','usage':legacy}]}))
            (run/'0-claude-stdout.jsonl').write_text(partial_stream())
            group=build_usage_report(vault)['usage_by_provider_model'][0]
            self.assertEqual(group['token_totals'], legacy)
            self.assertEqual(group['usage_unknown_attempts'], 1)

    def test_stream_deltas_are_not_added_to_assistant_snapshot(self):
        output=message() + event(type='stream_event',event={
            'type':'message_delta','usage':{'output_tokens':40}})
        result=h.classify('claude',output,'',124)
        self.assertEqual(result.usage, {
            'input_tokens':2,'cache_creation_input_tokens':15100,'cache_read_input_tokens':0})
