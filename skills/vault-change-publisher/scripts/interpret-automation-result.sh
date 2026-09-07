#!/bin/zsh
set -u

PROCESS_STATUS="${1:-64}"
RESULT_FILE="${2:-}"
SCRIPT_DIR="${0:A:h}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"
CANONICAL_VALIDATOR="${CANONICAL_VALIDATOR:-$SCRIPT_DIR/validate-canonical-result.py}"
AUTOMATION_RESULT_SCHEMA="${AUTOMATION_RESULT_SCHEMA:-$SCRIPT_DIR/automation-result.schema.json}"
if [[ ! -f "$AUTOMATION_RESULT_SCHEMA" ]]; then
  AUTOMATION_RESULT_SCHEMA="$SCRIPT_DIR/../references/automation-result.schema.json"
fi

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" || ! -f "$CANONICAL_VALIDATOR" || ! -f "$AUTOMATION_RESULT_SCHEMA" ]]; then
  printf 'missing_tool\t69\n'
  exit 0
fi

if [[ -z "$RESULT_FILE" ]]; then
  if [[ "$PROCESS_STATUS" -ne 0 ]]; then
    printf 'process_error\t%s\n' "$PROCESS_STATUS"
  else
    printf 'invalid_result\t65\n'
  fi
  exit 0
fi

VALIDATED_RESULT="$(
  "$PYTHON_BIN" "$CANONICAL_VALIDATOR" --terminal-status \
    "$AUTOMATION_RESULT_SCHEMA" "$RESULT_FILE" 2>/dev/null
)"
VALIDATION_STATUS=$?
if [[ "$VALIDATION_STATUS" -ne 0 ]]; then
  if [[ "$PROCESS_STATUS" -ne 0 ]]; then
    printf 'process_error\t%s\n' "$PROCESS_STATUS"
  else
    printf 'invalid_result\t65\n'
  fi
  exit 0
fi
if [[ "$PROCESS_STATUS" -ne 0 ]]; then
  if [[ "$VALIDATED_RESULT" == $'partial_publication\t75' \
    || "$VALIDATED_RESULT" == $'blocked\t75' ]]; then
    printf '%s\n' "$VALIDATED_RESULT"
  else
    printf 'process_error\t%s\n' "$PROCESS_STATUS"
  fi
else
  printf '%s\n' "$VALIDATED_RESULT"
fi
