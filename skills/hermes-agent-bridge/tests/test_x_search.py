import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / 'scripts' / 'hermes_x_search.py'


def worker():
    spec = importlib.util.spec_from_file_location('native_x_search', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_search_uses_oauth_and_never_paid_key(monkeypatch):
    module = worker()
    monkeypatch.setenv('XAI_API_KEY', 'paid-key-must-not-be-used')
    observed = []
    tool = SimpleNamespace(_resolve_xai_bearer=lambda: ('paid', 'https://api.x.ai/v1', 'xai'))
    def search(**kwargs):
        observed.append(tool._resolve_xai_bearer())
        return json.dumps({'success': True, 'credential_source': 'xai-oauth',
                           'answer': 'Found posts', 'citations': ['https://x.com/example/status/1']})
    tool.x_search_tool = search
    result = module.search('query', tool=tool, oauth=lambda: {
        'api_key': 'private-oauth', 'base_url': 'https://api.x.ai/v1'})
    assert result['success'] is True
    assert observed == [('private-oauth', 'https://api.x.ai/v1', 'xai-oauth')]
    assert 'private-oauth' not in json.dumps(result)


def test_oauth_failure_never_invokes_search_or_paid_fallback():
    module = worker()
    def unavailable():
        raise RuntimeError('private error with token')
    def forbidden(**kwargs):
        pytest.fail('Search must not run without OAuth')
    result = module.search('query', tool=SimpleNamespace(x_search_tool=forbidden), oauth=unavailable)
    assert result['error_kind'] == 'auth_error'
    assert 'private error' not in json.dumps(result)


def test_tool_quota_is_not_inferred_from_successful_content():
    module = worker()
    tool = SimpleNamespace(x_search_tool=lambda **kwargs: json.dumps({
        'success': True, 'answer': 'Post says rate limit', 'credential_source': 'xai-oauth'}))
    result = module.search('query', tool=tool, oauth=lambda: {'api_key': 'oauth'})
    assert result['success'] is True
    assert result.get('error_kind') is None


def test_unexpected_oauth_endpoint_is_rejected():
    module = worker()
    result = module.search('query', tool=SimpleNamespace(), oauth=lambda: {
        'api_key': 'oauth', 'base_url': 'https://unexpected.invalid/v1'})
    assert result['error_kind'] == 'route_policy'


def test_tool_error_is_preserved_as_incomplete():
    module = worker()
    tool = SimpleNamespace(x_search_tool=lambda **kwargs: json.dumps({
        'success': False, 'error': '429 Too Many Requests'}))
    result = module.search('query', tool=tool, oauth=lambda: {'api_key': 'oauth'})
    assert result['success'] is False
    assert result['error_kind'] == 'usage_limit'
    assert result['codex_handoff']['x_access_available'] is False
