"""Provider token observations; partial counters are never complete totals."""
from __future__ import annotations

import math

TOKEN_METRIC_KEYS = frozenset({
    'input_tokens', 'output_tokens', 'cache_creation_input_tokens',
    'cache_read_input_tokens', 'cached_input_tokens', 'cached_tokens',
    'total_tokens', 'reasoning_output_tokens',
})
CLAUDE_INPUT_KEYS = frozenset({
    'input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens',
})
# Preserve historical counters of unknown scope over a newly recovered partial
# stream. Only an explicit complete result may supersede that existing record.
COMPLETENESS_RANK = {'missing': 0, 'partial': 1, 'unknown': 2, 'complete': 3}


def token_metrics(usage):
    if not isinstance(usage, dict):
        return {}
    return {key: value for key, value in usage.items()
            if key in TOKEN_METRIC_KEYS and isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value) and value >= 0}


def usage_record(usage, *, completeness='unknown', source=None):
    values = token_metrics(usage)
    reason = ('provider_did_not_report' if usage is None else
              'provider_reported_unstructured_usage' if not isinstance(usage, dict) else
              'provider_reported_no_numeric_usage' if not values else 'provider_reported')
    record = {'available': bool(values), 'reason': reason,
              'completeness': completeness if values else 'missing'}
    if values:
        record['values'] = values
    if source:
        record['source'] = source
    return record


def attempt_usage(attempt):
    """Prefer raw counters, but retain their recorded scope. Old scope is unknown."""
    info = attempt.get('usage_info')
    info = info if isinstance(info, dict) else {}
    raw = attempt.get('usage')
    values = token_metrics(raw if isinstance(raw, dict) else
                           info.get('values') if info.get('available') else None)
    completeness = info.get('completeness', 'unknown')
    if completeness not in ('partial', 'complete', 'unknown'):
        completeness = 'unknown'
    return {'available': bool(values), 'values': values,
            'completeness': completeness if values else 'missing'}


def claude_usage(events):
    """Single-shot CLI usage: result replaces per-step input/cache observations.

    Assistant usage repeats for each content block of the same API message.
    Its output_tokens is a message-start placeholder, NOT a final output count.
    Stream deltas, tool text and nested agent messages are deliberately excluded.
    See https://code.claude.com/docs/en/agent-sdk/cost-tracking .
    """
    messages = {}
    conflicts = set()
    unkeyed = 0
    for event in events:
        if event.get('type') != 'assistant' or event.get('parent_tool_use_id'):
            continue
        message = event.get('message')
        if not isinstance(message, dict):
            continue
        values = {key: value for key, value in token_metrics(message.get('usage')).items()
                  if key in CLAUDE_INPUT_KEYS}
        if not values:
            continue
        identity = message.get('id')
        if not isinstance(identity, str) or not identity:
            unkeyed += 1
            continue
        if identity in messages and messages[identity] != values:
            # Conflicting snapshots have no documented delta semantics. Retain
            # the raw stream but exclude this message rather than invent a sum.
            conflicts.add(identity)
        messages[identity] = values
    totals = {}
    for identity, values in messages.items():
        if identity not in conflicts:
            for key, value in values.items():
                totals[key] = totals.get(key, 0) + value

    terminal = next((e for e in reversed(events) if e.get('type') == 'result'), None)
    raw = terminal.get('usage') if terminal else None
    terminal_values = token_metrics(raw)
    success = terminal and terminal.get('subtype') == 'success' and not terminal.get('is_error')
    # Crashed sessions can return zeroed usage. Prefer observed step inputs in
    # that case; never add the result and the steps or assert zero consumption.
    zeroed_error = terminal and not success and terminal_values and not any(terminal_values.values())
    if terminal_values and not zeroed_error:
        info = usage_record(raw, completeness='complete' if success else 'partial', source='result')
        if not success:
            info['limitations'] = ['Error result usage may omit the final response.']
        return raw, info

    info = usage_record(totals or None, completeness='partial', source='assistant_messages')
    info.update(message_count=len(messages) - len(conflicts),
                conflicting_message_ids=sorted(conflicts), unkeyed_messages=unkeyed,
                unavailable_fields=['output_tokens', 'total_tokens'],
                limitations=['Main-loop input/cache observations only; output placeholders are excluded.'])
    return totals or None, info
