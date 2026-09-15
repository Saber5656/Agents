"""Read-only aggregation of actual provider delegation usage from runner records.

Unknown or unreported usage is surfaced as such; it is never treated as zero
consumption and never implies quota exhaustion or disables a provider.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys


_STATUS_BUCKETS = {
    'running': 'running',
    'usage_limit': 'quota',
    'auth_error': 'auth',
    'permission_denied': 'permission',
    'completed': 'completed',
    'review_findings': 'completed',
    'review_incomplete': 'incomplete',
    'timeout': 'failed',
    'interrupted': 'failed',
    'executable_missing': 'failed',
    'incomplete': 'failed',
    'failed': 'failed',
    'budget_exhausted': 'failed',
}

# Real provider-reported token counters only. Never widen this to arbitrary
# numeric keys: providers also report unrelated percentages (e.g. subscription
# quota remaining) that must never be summed as if they were token usage.
_TOKEN_METRIC_KEYS = frozenset({
    'input_tokens', 'output_tokens',
    'cache_creation_input_tokens', 'cache_read_input_tokens',
    'cached_input_tokens', 'cached_tokens', 'total_tokens',
    'reasoning_output_tokens',
})

# macOS SF_DATALESS: file content lives only in iCloud and is not locally
# materialized. Touching such files (even via read_text) can block for
# minutes while the OS tries to download them; skip them outright.
_SF_DATALESS = 0x40000000


def _status_bucket(status):
    return _STATUS_BUCKETS.get(status, 'unknown')


def _parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _parse_since(value):
    parsed = _parse_time(value)
    if parsed is None:
        raise ValueError(
            f'since must be a timezone-aware ISO 8601 timestamp, got {value!r}'
        )
    return parsed


def _is_dataless(path):
    if sys.platform != 'darwin':
        return False
    try:
        flags = path.lstat().st_flags
    except (OSError, AttributeError):
        return False
    return bool(flags & _SF_DATALESS)


def _whitelisted_token_metrics(values):
    if not isinstance(values, dict):
        return {}
    result = {}
    for key, value in values.items():
        if key not in _TOKEN_METRIC_KEYS:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        result[key] = value
    return result


def _usage_state(attempt):
    """Read provider usage, preferring the raw envelope over the derived one.

    ``usage`` is the raw dict the provider returned. ``usage_info`` is a
    derived summary the runner may have written; older records only have
    ``usage_info`` and no raw ``usage``, so it is kept as a fallback for
    historical records.
    """
    raw = attempt.get('usage')
    if isinstance(raw, dict):
        values = _whitelisted_token_metrics(raw)
        return {'available': bool(values), 'values': values}
    info = attempt.get('usage_info')
    if isinstance(info, dict):
        values = _whitelisted_token_metrics(info.get('values'))
        return {'available': bool(info.get('available')) and bool(values), 'values': values}
    return {'available': False, 'values': {}}


def _attempt_identity(run_dir, record, ordinal):
    identity = record.get('attempt_id')
    if isinstance(identity, str) and identity:
        return (str(run_dir), identity)
    number = record.get('attempt_number')
    if number is None:
        number = ordinal
    return f'{run_dir}:{number}'


def _attempt_score(attempt):
    """Rank candidate records for the same identity: prefer a terminal
    (non-running) status, then richer usage, then the most recent timestamp,
    so a later completed record replaces an earlier in-flight snapshot."""
    status = attempt.get('status') if isinstance(attempt.get('status'), str) else None
    is_terminal = _status_bucket(status) != 'running'
    usage = _usage_state(attempt)
    time = _parse_time(attempt.get('finished_at')) or _parse_time(attempt.get('started_at'))
    time_key = time.timestamp() if time is not None else float('-inf')
    return (is_terminal, usage['available'], len(usage['values']), time_key)


def _stdout_path(run_dir, attempt):
    """Only Claude stdout is ever read for rate-limit events: Devin ATIF
    trajectories can be very large, and non-Claude providers do not emit
    ``rate_limit_event`` records at all."""
    provider = attempt.get('provider')
    number = attempt.get('attempt_number')
    if provider != 'claude':
        return None
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        return None
    path = run_dir / f'{number}-{provider}-stdout.jsonl'
    if path.parent != run_dir:
        return None
    return path


def _rate_limit_events(run_dir, attempt, skipped=None):
    path = _stdout_path(run_dir, attempt)
    if path is None or not path.is_file():
        return []
    if _is_dataless(path):
        if skipped is not None:
            skipped.append({'path': str(path), 'reason': 'dataless_icloud_stdout_skipped'})
        return []
    events = []
    try:
        with path.open(errors='replace') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get('type') != 'rate_limit_event':
                    continue
                info = record.get('rate_limit_info')
                if not isinstance(info, dict):
                    continue
                timestamp_source = 'provider'
                timestamp = record.get('timestamp')
                if not isinstance(timestamp, str) or not timestamp:
                    timestamp = attempt.get('finished_at') or attempt.get('started_at')
                    timestamp_source = 'attempt_finished_at' if attempt.get('finished_at') else 'attempt_started_at'
                events.append({
                    'run_dir': str(run_dir), 'attempt_id': attempt.get('attempt_id'),
                    'provider': attempt.get('provider'), 'timestamp': timestamp,
                    'source': 'stdout_rate_limit_event', 'rate_limit_info': info,
                    'timestamp_source': timestamp_source,
                })
    except OSError:
        return []
    return events


def _iter_result_paths(root, skipped):
    if not root.is_dir():
        return
    for path in sorted(root.rglob('result.json')):
        if _is_dataless(path):
            skipped.append({'path': str(path), 'reason': 'dataless_icloud_file_skipped'})
            continue
        yield path


def _load_record(path):
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        return None, str(exc)
    if not isinstance(value, dict) or not isinstance(value.get('attempts'), list):
        return None, 'result.json is not a valid runner record'
    return value, None


def _resolve_scan_root(vault, run_dir):
    root = vault / '01-Projects' / 'agent-runs'
    if run_dir is None:
        return root
    run_dir = Path(run_dir)
    try:
        resolved_root = root.resolve()
        resolved_run_dir = run_dir.resolve()
    except OSError as exc:
        raise ValueError(f'run_dir could not be resolved: {exc}') from exc
    if resolved_run_dir != resolved_root and resolved_root not in resolved_run_dir.parents:
        raise ValueError('run_dir must be the Vault agent-runs root or a directory under it')
    return resolved_run_dir


def build_usage_report(vault, *, since=None, run_dir=None):
    """Aggregate actual delegation usage from Vault runner records, read-only.

    Recursively inspects every ``result.json`` under
    ``vault/01-Projects/agent-runs`` (including nested service attempt
    directories), grouping by the provider and model the provider actually
    reported. Requested model/provider aliases are preserved separately.
    Missing usage is reported as missing, never as zero or as quota
    exhaustion.

    ``run_dir``, if given, narrows the scan to that directory (which must be
    the agent-runs root or a directory under it) instead of the whole Vault,
    so a caller can point at a specific record for concrete verification.
    """
    vault = Path(vault)
    since_dt = _parse_since(since) if since is not None else None
    scan_root = _resolve_scan_root(vault, run_dir)

    runs_scanned = 0
    runs_skipped = []
    attempts_total = 0
    candidates = {}
    identity_order = []

    for path in _iter_result_paths(scan_root, runs_skipped):
        record, error = _load_record(path)
        if record is None:
            runs_skipped.append({'path': str(path), 'reason': error})
            continue
        runs_scanned += 1
        this_run_dir = path.parent
        identity_dir = this_run_dir.resolve()
        recorded_dir = record.get('run_dir')
        if isinstance(recorded_dir, str) and Path(recorded_dir).is_absolute():
            canonical = Path(recorded_dir).resolve()
            if (vault/'01-Projects'/'agent-runs').resolve() in canonical.parents:
                identity_dir = canonical
        for ordinal, attempt in enumerate(record.get('attempts', [])):
            if not isinstance(attempt, dict):
                runs_skipped.append({'path': str(path), 'reason': 'attempt entry is not an object'})
                continue
            attempts_total += 1
            identity = _attempt_identity(identity_dir, attempt, ordinal)
            candidate = (attempt, this_run_dir)
            existing = candidates.get(identity)
            if existing is None:
                candidates[identity] = candidate
                identity_order.append(identity)
            elif _attempt_score(attempt) > _attempt_score(existing[0]):
                candidates[identity] = candidate

    groups = {}
    group_order = []
    rate_limit_events = []
    attempts_deduplicated = 0
    attempts_selected = 0

    for identity in identity_order:
        attempt, this_run_dir = candidates[identity]
        attempts_deduplicated += 1

        started = _parse_time(attempt.get('started_at'))
        if since_dt is not None and (started is None or started < since_dt):
            continue

        attempts_selected += 1
        provider = attempt.get('provider') if isinstance(attempt.get('provider'), str) else None
        requested_model = attempt.get('requested_model') if isinstance(attempt.get('requested_model'), str) else None
        actual_model = attempt.get('actual_model') if isinstance(attempt.get('actual_model'), str) else None
        status = attempt.get('status') if isinstance(attempt.get('status'), str) else None
        bucket = _status_bucket(status)
        usage = _usage_state(attempt)

        key = (provider, actual_model)
        if key not in groups:
            groups[key] = {
                'provider': provider, 'actual_model': actual_model,
                'requested_models': [], 'attempts': 0,
                'status_counts': {}, 'usage_reported_attempts': 0,
                'usage_missing_attempts': 0, 'token_totals': {},
            }
            group_order.append(key)
        group = groups[key]
        group['attempts'] += 1
        if requested_model is not None and requested_model not in group['requested_models']:
            group['requested_models'].append(requested_model)
        group['status_counts'][bucket] = group['status_counts'].get(bucket, 0) + 1
        if usage['available']:
            group['usage_reported_attempts'] += 1
            for token_key, value in usage['values'].items():
                group['token_totals'][token_key] = group['token_totals'].get(token_key, 0) + value
        else:
            group['usage_missing_attempts'] += 1

        rate_limit_events.extend(_rate_limit_events(this_run_dir, attempt, runs_skipped))

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'vault': str(vault), 'since': since,
        'runs_scanned': runs_scanned, 'runs_skipped': runs_skipped,
        'attempts_total': attempts_total, 'attempts_deduplicated': attempts_deduplicated,
        'attempts_selected': attempts_selected,
        'usage_by_provider_model': [groups[key] for key in group_order],
        'rate_limit_events': rate_limit_events,
    }


def emit_usage(report, *, as_json=False):
    if as_json:
        return json.dumps(report, ensure_ascii=False, indent=2, default=str)
    lines = [f'usage: {report["runs_scanned"]} run(s) scanned, '
             f'{report["attempts_selected"]} attempt(s)']
    for group in report['usage_by_provider_model']:
        provider = group['provider'] or 'unknown'
        model = group['actual_model'] or 'unverified'
        aliases = ', '.join(group['requested_models']) or 'unknown'
        totals = ', '.join(f'{key}={value}' for key, value in group['token_totals'].items()) or 'none reported'
        lines.append(f'{provider}  {model}  (requested: {aliases})  attempts={group["attempts"]}  '
                     f'usage_reported={group["usage_reported_attempts"]} '
                     f'usage_missing={group["usage_missing_attempts"]}  status={group["status_counts"]}  tokens: {totals}')
    for event in report['rate_limit_events']:
        lines.append(f'rate_limit: {event["provider"]} {event["timestamp"]} {event["rate_limit_info"]}')
    if report['runs_skipped']:
        lines.append(f'skipped {len(report["runs_skipped"])} unavailable/malformed record(s)')
        for skipped in report['runs_skipped']:
            lines.append(f"  {skipped['path']}: {skipped['reason']}")
    lines.append('Token counts are observed usage, not subscription remaining percentages. Rate limits are historical observations.')
    return '\n'.join(lines)
