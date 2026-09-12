#!/usr/bin/env python3
"""Call Hermes' native X tool with Grok OAuth only, without an agent turn."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


def search(query, *, tool, oauth):
    try:
        credentials = oauth()
        token = str(credentials.get('api_key') or '').strip()
        endpoint = str(credentials.get('base_url') or 'https://api.x.ai/v1').rstrip('/')
        if not token:
            raise ValueError('OAuth unavailable')
    except Exception:
        return {'success': False, 'error_kind': 'auth_error',
                'error': 'Existing Grok OAuth credentials are unavailable; no API-key fallback was attempted.'}
    if endpoint != 'https://api.x.ai/v1':
        return {'success': False, 'error_kind': 'route_policy', 'error': 'Unexpected Grok OAuth endpoint'}
    # This dedicated process never calls the tool's OAuth-to-API-key resolver.
    # A configured XAI_API_KEY therefore cannot become the charge source.
    tool._resolve_xai_bearer = lambda: (token, endpoint, 'xai-oauth')
    try:
        value = json.loads(tool.x_search_tool(query=query))
        if not isinstance(value, dict):
            raise ValueError('Invalid tool result')
    except Exception:
        return {'success': False, 'error_kind': 'process_failure', 'error': 'Hermes X tool did not return a valid result'}
    # Native tool output contains no credentials. Still redact the exact token
    # in case an upstream exception includes request headers.
    value = json.loads(json.dumps(value, ensure_ascii=False).replace(token, '[REDACTED]'))
    value['schema'] = 'hermes-x-search/v1'
    value['credential_source'] = 'xai-oauth'
    if value.get('success') is not True:
        error = str(value.get('error', ''))
        if re.search(r'\b(?:401|403)\b|unauthorized|authentication', error, re.I):
            kind = 'auth_error'
        elif re.search(r'\b429\b|rate.?limit|quota|usage.?limit', error, re.I):
            kind = 'usage_limit'
        else:
            kind = 'process_failure'
        value['error_kind'] = kind
        if kind == 'usage_limit':
            value['codex_handoff'] = {
                'query': query, 'x_access_available': False,
                'instruction': 'Continue the remaining research with Codex using available public sources; report that X search was unavailable. Do not fabricate X results.',
            }
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--query', required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.hermes_root.resolve()))
    try:
        from hermes_cli.auth import resolve_xai_oauth_runtime_credentials
        from tools import x_search_tool
        result = search(args.query, tool=x_search_tool, oauth=resolve_xai_oauth_runtime_credentials)
    except Exception:
        result = {'success': False, 'error_kind': 'configuration_error',
                  'error': 'Installed Hermes native X search runtime is unavailable'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('success') is True else 75 if result.get('error_kind') == 'usage_limit' else 2


if __name__ == '__main__':
    raise SystemExit(main())
