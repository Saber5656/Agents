"""Offline context and adapter contract fixtures. Never use real credentials."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import gh_credential_context as context


@pytest.mark.parametrize('host,selectors,expected', [
    ('github.com', {}, 'stored'),
    ('github.com', {'GITHUB_TOKEN': 'fixture'}, 'GITHUB_TOKEN'),
    ('github.com', {'GH_TOKEN': 'fixture'}, 'GH_TOKEN'),
    ('github.com', {'GH_TOKEN': 'fixture', 'GITHUB_TOKEN': 'other'}, 'GH_TOKEN'),
    ('github.com', {'GH_TOKEN': '', 'GITHUB_TOKEN': 'fixture'}, 'GITHUB_TOKEN'),
    ('tenant.ghe.com', {'GH_TOKEN': 'fixture'}, 'GH_TOKEN'),
    ('git.example.test', {'GH_TOKEN': 'ignored', 'GITHUB_ENTERPRISE_TOKEN': 'fixture'}, 'GITHUB_ENTERPRISE_TOKEN'),
    ('git.example.test', {'GH_ENTERPRISE_TOKEN': 'fixture', 'GITHUB_ENTERPRISE_TOKEN': 'other'}, 'GH_ENTERPRISE_TOKEN'),
])
def test_host_precedence(host, selectors, expected):
    assert context.selected_source(host, selectors) == expected


def snapshot(**overrides):
    args = dict(environ={'HOME': '/fixture/home', 'GITHUB_TOKEN': 'SECRET_MARKER'},
                surface='codex-app', target_host='github.com',
                effective_user='1000', executor_host='fixture-host',
                gh_path='/fixture/gh', gh_version='2.80.0')
    args.update(overrides)
    return context.describe_context(**args)


@pytest.mark.parametrize('env,provenance,path', [
    ({'HOME': '/fixture/home'}, 'HOME', '/fixture/home/.config/gh'),
    ({'HOME': '/fixture/home', 'XDG_CONFIG_HOME': '/fixture/xdg'}, 'XDG_CONFIG_HOME', '/fixture/xdg/gh'),
    ({'HOME': '/fixture/home', 'XDG_CONFIG_HOME': '/fixture/xdg', 'GH_CONFIG_DIR': '/fixture/config'}, 'GH_CONFIG_DIR', '/fixture/config'),
])
def test_config_provenance(env, provenance, path):
    result = snapshot(environ=env)
    assert result['config_source'] == provenance
    assert result['config_dir'] == path


def test_presence_only_and_separate_surfaces():
    result = snapshot()
    assert 'SECRET_MARKER' not in json.dumps(result)
    assert result['selectors']['GITHUB_TOKEN'] is True
    assert snapshot(surface='cli') != result
    assert snapshot(environ={'HOME': '/fixture/home', 'GITHUB_TOKEN': 'rotated'}) == result


@pytest.mark.parametrize('change', [dict(surface='cli'), dict(effective_user='1001'),
    dict(executor_host='other'), dict(gh_version='2.81.0'), dict(gh_path='/other/gh'),
    dict(environ={'HOME':'/fixture/home', 'GH_TOKEN':'fixture'}),
    dict(environ={'HOME':'/other', 'GITHUB_TOKEN':'fixture'})])
def test_drift_fails_closed(change):
    with pytest.raises(context.ContextError, match='context_drift'):
        context.require_same_context(snapshot(), snapshot(**change))


@pytest.mark.parametrize('observation,expected', [
    ({'historical':True, 'http_status':401}, 'historical_unresolved_401'),
    ({'http_status':401}, 'authentication_failed'),
    ({'http_status':200}, 'request_succeeded'),
    ({'http_status':200, 'authentication_probe':True}, 'authentication_succeeded'),
    ({'http_status':403}, 'permission_or_policy_denied'),
    ({'http_status':404}, 'not_found_or_inaccessible'),
    ({'runtime_allowed':False}, 'runtime_denied'),
    ({'feature_available':False}, 'feature_unavailable'),
    ({'transport_failed':True}, 'transport_failure'),
])
def test_classification_does_not_invent_root_cause(observation, expected):
    assert context.classify_observation(**observation) == expected


def test_adapter_context_equality_is_not_authority():
    context.require_same_context(snapshot(), snapshot())
    # Diagnostic carries no permission, token scope, grant, or expiry proof.
    assert not any(k in snapshot() for k in ('authorized', 'administration_write', 'grant'))


def test_permission_observation_is_separate_from_read():
    assert context.classify_observation(permission_sufficient=False) == 'insufficient_permissions'
    assert context.classify_observation(http_status=200) == 'request_succeeded'
    assert context.classify_observation(historical=True, http_status=401) == 'historical_unresolved_401'


def test_version_failure_discards_raw_output(monkeypatch):
    import subprocess
    monkeypatch.setattr(context.shutil, 'which', lambda _: '/fixture/gh')
    monkeypatch.setattr(context.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess([], 1, 'SECRET_MARKER', 'SECRET_MARKER'))
    with pytest.raises(context.ContextError, match='^gh_version_unavailable$'):
        context.observe_context(surface='cli', target_host='github.com')


@pytest.fixture
def windows_observer(monkeypatch, tmp_path):
    """Windows API contract stub on the host OS; not native Windows QA."""
    import ctypes
    import subprocess
    from types import SimpleNamespace

    state = {'user': 'observed-user', 'ok': True, 'calls': 0}

    class GetUserName:
        def __call__(self, buffer, size):
            state['calls'] += 1
            assert size._obj.value == 257
            if state['ok']:
                buffer.value = state['user']
            return int(state['ok'])

    def load_api(name, **kwargs):
        assert name == 'advapi32.dll'
        return SimpleNamespace(GetUserNameW=GetUserName())

    monkeypatch.setattr(ctypes, 'WinDLL', load_api, raising=False)
    monkeypatch.setattr(context, 'os', SimpleNamespace(name='nt', environ={
        'AppData': str(tmp_path), 'USERNAME': 'untrusted-name', 'GH_TOKEN': 'SECRET_MARKER'}))
    monkeypatch.setattr(context.shutil, 'which', lambda _: '/fixture/gh')
    monkeypatch.setattr(context.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, 'gh version 2.80.0 (fixture)', ''))
    return state


def test_windows_actual_observer_uses_os_identity(windows_observer):
    result = context.observe_context(surface='cli', target_host='github.com')
    assert result['effective_user'] == 'observed-user'
    assert result['config_source'] == 'AppData'
    assert windows_observer['calls'] == 1
    assert 'untrusted-name' not in json.dumps(result)
    assert 'SECRET_MARKER' not in json.dumps(result)


@pytest.mark.parametrize('state', [{'ok': False}, {'user': ''}])
def test_windows_identity_failure_is_typed(windows_observer, state):
    windows_observer.update(state)
    with pytest.raises(context.ContextError, match='^effective_user_unavailable$'):
        context.observe_context(surface='cli', target_host='github.com')


def test_windows_user_change_is_context_drift(windows_observer):
    previous = context.observe_context(surface='cli', target_host='github.com')
    windows_observer['user'] = 'another-user'
    with pytest.raises(context.ContextError, match='context_drift'):
        context.require_same_context(previous, context.observe_context(surface='cli', target_host='github.com'))


def test_windows_observer_initializes_actual_dry_apply(windows_observer, monkeypatch, tmp_path, capsys):
    import subprocess
    spec = importlib.util.spec_from_file_location('windows_helper', SCRIPTS/'apply-default-branch-ruleset.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    calls = []

    def transport(args, **kwargs):
        if args[1:] == ['--version']:
            return subprocess.CompletedProcess(args, 0, 'gh version 2.80.0 (fixture)', '')
        calls.append(args)
        if '--method' in args and args[args.index('--method') + 1] != 'GET':
            payload = json.loads(Path(args[args.index('--input') + 1]).read_text())
            payload['id'] = 99
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), '')
        return subprocess.CompletedProcess(args, 0, '[]', '')

    monkeypatch.setattr(context.subprocess, 'run', transport)
    # The helper's environment selection must use the same synthetic selectors.
    monkeypatch.setenv('GH_TOKEN', 'SECRET_MARKER')
    payload, binding = tmp_path/'payload.json', tmp_path/'context.json'
    common = ['helper', '--repo', 'fixture/repo', '--operation', 'create', '--hostname', 'github.com']
    monkeypatch.setattr(sys, 'argv', common+['--payload-out', str(payload), '--context-out', str(binding)])
    assert helper.main() == 0
    monkeypatch.setattr(sys, 'argv', common+['--mode', 'apply', '--yes', '--payload-in', str(payload), '--context-in', str(binding)])
    assert helper.main() == 0
    assert len(calls) == 1 and 'POST' in calls[0]
    assert windows_observer['calls'] >= 3
    assert 'SECRET_MARKER' not in str(capsys.readouterr())


@pytest.mark.parametrize('host,selector', [('github.com', 'GH_TOKEN'),
    ('github.com', 'GITHUB_TOKEN'), ('git.example.test', 'GH_ENTERPRISE_TOKEN'),
    ('git.example.test', 'GITHUB_ENTERPRISE_TOKEN')])
def test_token_only_missing_config_root_is_explicit(host, selector):
    result = snapshot(environ={selector: 'SECRET_MARKER'}, target_host=host)
    assert result['credential_source'] == selector
    assert result['config_source'] == 'unavailable'
    assert result['config_dir'] is None and result['config_resolved'] is None
    assert 'SECRET_MARKER' not in json.dumps(result)


@pytest.mark.parametrize('env', [{}, {'GH_TOKEN': ''}, {'GH_ENTERPRISE_TOKEN': 'fixture'}])
def test_missing_root_without_applicable_token_still_fails(env):
    with pytest.raises(context.ContextError, match='^config_home_unknown$'):
        snapshot(environ=env)


@pytest.mark.parametrize('root_key', ['HOME', 'XDG_CONFIG_HOME', 'GH_CONFIG_DIR'])
def test_token_only_root_appearing_is_context_drift(root_key):
    previous = snapshot(environ={'GH_TOKEN': 'fixture'})
    current = snapshot(environ={'GH_TOKEN': 'fixture', root_key: '/fixture/new'})
    with pytest.raises(context.ContextError, match='context_drift'):
        context.require_same_context(previous, current)
