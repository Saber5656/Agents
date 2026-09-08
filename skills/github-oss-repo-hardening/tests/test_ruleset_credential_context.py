"""Exercise actual helper with fake gh and synthetic environment only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/apply-default-branch-ruleset.py'


@pytest.fixture
def cli(tmp_path):
    executable = tmp_path / 'gh'
    executable.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
 print('gh version 2.80.0 (fixture)'); sys.exit(0)
with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if os.environ.get('FAIL'):
 print('HTTP 401 SECRET_MARKER Authorization: SECRET_MARKER', file=sys.stderr);sys.exit(1)
method = sys.argv[sys.argv.index('--method')+1] if '--method' in sys.argv else 'GET'
endpoint = next((arg for arg in sys.argv if arg.startswith('repos/')), '')
state = Path(os.environ['CALLS']).with_name('ruleset-state.json')
if method in ('POST', 'PUT'):
 payload = json.loads(Path(sys.argv[sys.argv.index('--input')+1]).read_text())
 payload['id'] = 99
 state.write_text(json.dumps(payload))
 print(json.dumps(payload))
elif '/rulesets/' in endpoint and state.exists():
 print(state.read_text())
elif state.exists():
 print('[' + state.read_text() + ']')
else:
 print('[]')
''')
    executable.chmod(0o755)
    env = {'PATH': str(tmp_path)+os.pathsep+os.path.dirname(sys.executable)+os.pathsep+'/usr/bin:/bin',
           'HOME': str(tmp_path), 'GH_TOKEN': 'SECRET_MARKER', 'CALLS': str(tmp_path/'calls')}
    def run(*args, changes=None):
        return subprocess.run([sys.executable, str(SCRIPT), '--repo', 'fixture/repo', *args],
                              env=env | (changes or {}), capture_output=True, text=True)
    return run, tmp_path, env


def prepare(cli):
    run, root, _ = cli
    payload, binding = root/'payload.json', root/'context.private.json'
    result = run('--operation', 'create', '--payload-out', str(payload), '--context-out', str(binding),
                 '--executor-surface', 'codex-app')
    assert result.returncode == 0, result.stderr
    return payload, binding


def apply(cli, payload, binding, **kwargs):
    run, _, _ = cli
    return run('--mode', 'apply', '--yes', '--operation', 'create', '--payload-in', str(payload),
               '--context-in', str(binding), '--executor-surface', 'codex-app', **kwargs)


def test_github_token_label(cli):
    run, root, _ = cli
    result = run('--operation','create','--payload-out',str(root/'p.json'),
                 changes={'GH_TOKEN':'', 'GITHUB_TOKEN':'SECRET_MARKER'})
    assert result.returncode == 0
    assert 'Auth source: GITHUB_TOKEN environment variable' in result.stdout
    assert 'SECRET_MARKER' not in result.stdout + result.stderr


def test_valid_reviewed_context_and_existing_confirmation(cli):
    payload, binding = prepare(cli)
    assert binding.stat().st_mode & 0o777 == 0o600
    assert 'SECRET_MARKER' not in binding.read_text()
    result = apply(cli, payload, binding)
    assert result.returncode == 0, result.stderr
    assert 'Applied ruleset' in result.stdout
    assert 'POST' in (cli[1]/'calls').read_text()


@pytest.mark.parametrize('changes', [{'GH_CONFIG_DIR':'/fixture/other'}, {'GH_HOST':'other.test'},
                                   {'GH_TOKEN':'', 'GITHUB_TOKEN':'SECRET_MARKER'}])
def test_context_drift_stops_before_mutation(cli, changes):
    payload, binding = prepare(cli)
    result = apply(cli, payload, binding, changes=changes)
    assert result.returncode != 0
    assert 'context_drift' in result.stderr
    assert not (cli[1]/'calls').exists()


def test_payload_drift_stops(cli):
    payload, binding = prepare(cli)
    p = json.loads(payload.read_text());p['name']='changed';payload.write_text(json.dumps(p))
    result = apply(cli, payload, binding)
    assert result.returncode != 0
    assert not (cli[1]/'calls').exists()


def test_existing_ruleset_drift_stops_before_write(cli):
    run, root, _ = cli
    state = root / 'ruleset-state.json'
    state.write_text(json.dumps({
        'id': 99,
        'name': 'protect-main-branch-of-OSS',
        'target': 'branch',
        'enforcement': 'active',
        'bypass_actors': [],
        'conditions': {'ref_name': {'include': ['~DEFAULT_BRANCH'], 'exclude': []}},
        'rules': [{'type': 'deletion'}],
    }))
    payload, binding = root / 'payload.json', root / 'context.private.json'
    reviewed = run('--payload-out', str(payload), '--context-out', str(binding))
    assert reviewed.returncode == 0, reviewed.stderr
    changed = json.loads(state.read_text())
    changed['rules'].append({'type': 'unrelated_future_rule'})
    state.write_text(json.dumps(changed))

    result = apply(cli, payload, binding)
    assert result.returncode != 0
    assert 'context_drift' in result.stderr
    calls = (root / 'calls').read_text().splitlines()
    assert not any('POST' in call or 'PUT' in call for call in calls)


def test_selected_invalid_credential_never_falls_back_or_leaks(cli):
    run, root, _ = cli
    result = run('--payload-out',str(root/'p.json'),changes={'FAIL':'1'})
    assert result.returncode != 0
    assert 'authentication_failed' in result.stderr
    assert 'SECRET_MARKER' not in result.stdout+result.stderr
    calls = (root/'calls').read_text().splitlines()
    assert len(calls) == 1
    assert not any('auth' in c or 'POST' in c for c in calls)


def test_read_success_does_not_allow_stored_mutation(cli):
    run, root, _ = cli
    payload,binding = root/'p.json',root/'context.private.json'
    r=run('--payload-out',str(payload),'--context-out',str(binding), changes={'GH_TOKEN':''})
    assert r.returncode == 0
    result=run('--mode','apply','--yes','--payload-in',str(payload),'--context-in',str(binding),changes={'GH_TOKEN':''})
    assert result.returncode != 0
    assert 'stored' in result.stderr
    assert 'POST' not in (root/'calls').read_text()


def test_context_output_refuses_existing_symlink(cli):
    run,root,_=cli
    target=root/'keep';target.write_text('keep')
    binding=root/'context';binding.symlink_to(target)
    result=run('--operation','create','--payload-out',str(root/'p.json'),'--context-out',str(binding))
    assert result.returncode != 0
    assert target.read_text() == 'keep'


def test_success_response_is_not_forwarded(cli):
    payload,binding=prepare(cli)
    executable=cli[1]/'gh'
    executable.write_text(executable.read_text().replace("print(json.dumps(payload))", "print('SECRET_MARKER')"))
    result=apply(cli,payload,binding)
    assert result.returncode == 0
    assert 'reconciled as applied' in result.stdout
    assert 'SECRET_MARKER' not in result.stdout+result.stderr


def test_lost_response_with_mismatched_readback_is_ambiguous_without_retry(cli):
    payload, binding = prepare(cli)
    executable = cli[1] / 'gh'
    contents = executable.read_text()
    contents = contents.replace(
        "state.write_text(json.dumps(payload))",
        "state.write_text(json.dumps({'id': 99, 'name': 'other', 'rules': []}))",
    ).replace("print(json.dumps(payload))", "print('SECRET_MARKER')")
    executable.write_text(contents)
    result = apply(cli, payload, binding)
    assert result.returncode != 0
    assert 'ambiguous_mutation' in result.stderr
    assert 'SECRET_MARKER' not in result.stdout + result.stderr
    calls = (cli[1] / 'calls').read_text().splitlines()
    assert sum('POST' in call or 'PUT' in call for call in calls) == 1


def test_last_moment_config_symlink_drift_stops_mutation(cli):
    run,root,env=cli
    first,second=root/'first',root/'second'
    first.mkdir();second.mkdir()
    link=root/'config';link.symlink_to(first)
    env['GH_CONFIG_DIR']=str(link)
    payload,binding=prepare(cli)
    executable=root/'gh'
    # Change a non-secret config-directory identity during discovery, after the
    # initial snapshot has matched. The next observation must stop the POST.
    executable.write_text(executable.read_text().replace("else:\n print('[]')", "else:\n Path(os.environ['GH_CONFIG_DIR']).unlink()\n Path(os.environ['GH_CONFIG_DIR']).symlink_to('"+str(second)+"')\n print('[]')"))
    # Use upsert for discovery at both preparation and apply.
    data=json.loads(binding.read_text());data['operation']='upsert';binding.write_text(json.dumps(data))
    result=run('--mode','apply','--yes','--payload-in',str(payload),'--context-in',str(binding),'--executor-surface','codex-app')
    assert result.returncode != 0
    assert 'context_drift' in result.stderr
    assert 'POST' not in (root/'calls').read_text()


@pytest.mark.parametrize('selectors', [
    {'GH_TOKEN':'', 'GITHUB_TOKEN':'SECRET_MARKER'},
    {'GH_HOST':'git.example.test', 'GH_ENTERPRISE_TOKEN':'SECRET_MARKER'},
    {'GH_HOST':'git.example.test', 'GH_TOKEN':'SECRET_MARKER'},
])
def test_diagnosis_does_not_expand_apply_eligibility(cli, selectors):
    cli[2].update(selectors)
    payload,binding=prepare(cli)
    result=apply(cli,payload,binding)
    assert result.returncode != 0
    assert 'explicit' in result.stderr
    assert not (cli[1]/'calls').exists()


def test_legacy_override_does_not_switch_selected_github_token(cli):
    run,root,env=cli
    env.update(GH_TOKEN='', GITHUB_TOKEN='SECRET_MARKER')
    payload,binding=root/'p.json',root/'context.private.json'
    result=run('--operation','create','--payload-out',str(payload),'--context-out',str(binding),'--allow-stored-gh-auth')
    assert result.returncode == 0
    result=run('--operation','create','--mode','apply','--yes','--payload-in',str(payload),'--context-in',str(binding),'--allow-stored-gh-auth')
    assert result.returncode == 0
    assert 'Auth source: GITHUB_TOKEN environment variable' in result.stdout
    assert 'SECRET_MARKER' not in result.stdout + result.stderr
    calls=(root/'calls').read_text().splitlines()
    assert len(calls)==1 and 'POST' in calls[0]


@pytest.mark.parametrize('root_value', [None, [], 'invalid', 1, True])
def test_malformed_context_root_is_bounded_failure(cli, root_value):
    payload, binding = prepare(cli)
    binding.write_text(json.dumps(root_value))
    result = apply(cli, payload, binding)
    assert result.returncode == 2
    assert 'context_drift' in result.stderr
    assert 'Traceback' not in result.stderr
    assert not (cli[1] / 'calls').exists()


def test_token_only_without_home_initializes_dry_apply(cli):
    cli[2]['HOME'] = ''
    payload, binding = prepare(cli)
    context = json.loads(binding.read_text())['context']
    assert context['config_source'] == 'unavailable'
    assert context['config_dir'] is None
    result = apply(cli, payload, binding)
    assert result.returncode == 0, result.stderr
    assert 'SECRET_MARKER' not in result.stdout + result.stderr
    assert 'POST' in (cli[1] / 'calls').read_text()


def test_token_only_root_change_blocks_actual_apply(cli):
    cli[2]['HOME'] = ''
    payload, binding = prepare(cli)
    result = apply(cli, payload, binding, changes={'HOME': str(cli[1])})
    assert result.returncode == 2
    assert 'context_drift' in result.stderr
    assert not (cli[1] / 'calls').exists()


def test_token_only_invalid_credential_no_fallback(cli):
    run, root, env = cli
    env['HOME'] = ''
    result = run('--payload-out', str(root/'payload.json'), changes={'FAIL': '1'})
    assert result.returncode == 2
    assert 'authentication_failed' in result.stderr
    assert 'SECRET_MARKER' not in result.stdout + result.stderr
    assert len((root/'calls').read_text().splitlines()) == 1
