#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
RUNNER="$SCRIPT_DIR/../assets/run-daily-it-news-vulnerability-check.sh"
REPO_ROOT="$SCRIPT_DIR/../.."

/usr/bin/grep -F -- 'automation.local.env' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.collect.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.review.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'commit-reviewed-publication.py' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.evidence-review.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'prepare-publication-review-context.py' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'PINNED_REVIEW_RUNNER="$WORKDIR/run-pinned-review.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'NOTIFICATION_SENDER="$WORKDIR/send-it-news-discord-notification.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--add-dir "$STAGING_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'COLLECTION_FETCHER="$WORKDIR/collect-public-sources.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'SOURCE_CATALOG="$WORKDIR/it-news-sources.json"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'RUNTIME_RELEASE_MANIFEST="$WORKDIR/runtime-release-manifest.json"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'RUNTIME_RELEASE_VERIFIER="$WORKDIR/verify-runtime-release.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'for executable in "$COLLECTION_FETCHER" "$PINNED_REVIEW_RUNNER" "$NOTIFICATION_SENDER" "$RUNTIME_RELEASE_VERIFIER"; do' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '[[ ! -x "$executable" ]]' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$PINNED_REVIEW_RUNNER"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'required daily automation asset is not executable' "$RUNNER" >/dev/null
COLLECTOR_SOURCE="$REPO_ROOT/summarize-it-news/scripts/collect-public-sources.py"
TRACKED_COLLECTOR_MODE="$(/usr/bin/git -C "$REPO_ROOT" ls-files -s \
  summarize-it-news/scripts/collect-public-sources.py \
  | /usr/bin/awk '{print $1}')"
if [[ ! -x "$COLLECTOR_SOURCE" ]] || {
  [[ "$TRACKED_COLLECTOR_MODE" != "100755" ]] \
    && ! /usr/bin/git -C "$REPO_ROOT" diff --summary HEAD -- \
      summarize-it-news/scripts/collect-public-sources.py \
      | /usr/bin/grep -F -- 'mode change 100644 => 100755' >/dev/null
}; then
  echo "collector must be tracked as executable" >&2
  exit 1
fi

MODE_FIXTURE_ROOT="$(mktemp -d)"
FAIL_RUN_ROOT=""
SNAPSHOT_FIXTURE_ROOT="$(mktemp -d)"
cleanup() {
  [[ -z "$MODE_FIXTURE_ROOT" ]] || rm -rf "$MODE_FIXTURE_ROOT"
  [[ -z "$FAIL_RUN_ROOT" ]] || rm -rf "$FAIL_RUN_ROOT"
  [[ -z "$SNAPSHOT_FIXTURE_ROOT" ]] || rm -rf "$SNAPSHOT_FIXTURE_ROOT"
}
trap cleanup EXIT
/bin/cp "$RUNNER" "$MODE_FIXTURE_ROOT/run-daily-it-news-vulnerability-check.sh"
for required_name in \
  automation.local.env resolve-runtime-context.py fetch-vault-main.py \
  capture-vault-state.py determine-publication-modes.py \
  validate-collection-result.py collect-public-sources.py \
  it-news-sources.json install-verified-artifacts.py \
  commit-reviewed-publication.py validate-publication-review.py \
  push-committed-heads.py send-it-news-discord-notification.py prepare-publication-evidence.py \
  commit-push-publication-evidence.py evidence_hunk.py git_diff_digest.py \
  isolated_git_transport.py atomic_file_ops.py trusted_gitleaks.py gitleaks-default.toml \
  prepare-codex-output-schema.py validate-canonical-result.py \
  stage-standing-task.py stage-dirty-review-inputs.py prepare-publication-review-context.py run-pinned-review.py \
  daily-it-news.collect.prompt.md daily-it-news.review.prompt.md \
  daily-it-news.evidence-review.prompt.md collection-result.schema.json \
  publication-review-result.schema.json publication-commit-result.schema.json \
  evidence-review-result.schema.json automation-result.schema.json \
  interpret-automation-result.sh runtime-release-manifest.json verify-runtime-release.py; do
  /usr/bin/touch "$MODE_FIXTURE_ROOT/$required_name"
done
/bin/chmod 0755 "$MODE_FIXTURE_ROOT"/*.py "$MODE_FIXTURE_ROOT"/*.sh
/bin/chmod 0644 "$MODE_FIXTURE_ROOT/collect-public-sources.py"
set +e
/bin/zsh "$MODE_FIXTURE_ROOT/run-daily-it-news-vulnerability-check.sh" \
  >"$MODE_FIXTURE_ROOT/stdout.log" 2>"$MODE_FIXTURE_ROOT/stderr.log"
MODE_FIXTURE_STATUS=$?
set -e
if [[ "$MODE_FIXTURE_STATUS" -ne 66 ]]; then
  echo "non-executable collector must fail preflight with status 66" >&2
  exit 1
fi
if ! /usr/bin/grep -F -- 'required daily automation asset is not executable:' \
  "$MODE_FIXTURE_ROOT/stderr.log" >/dev/null \
  || ! /usr/bin/grep -F -- "$MODE_FIXTURE_ROOT/collect-public-sources.py" \
  "$MODE_FIXTURE_ROOT/stderr.log" >/dev/null \
  || [[ -d "$MODE_FIXTURE_ROOT/logs" ]]; then
  echo "non-executable collector must fail preflight with status 66" >&2
  exit 1
fi
/bin/chmod 0755 "$MODE_FIXTURE_ROOT/collect-public-sources.py"
/bin/chmod 0644 "$MODE_FIXTURE_ROOT/run-pinned-review.py"
set +e
/bin/zsh "$MODE_FIXTURE_ROOT/run-daily-it-news-vulnerability-check.sh" \
  >"$MODE_FIXTURE_ROOT/pinned-stdout.log" 2>"$MODE_FIXTURE_ROOT/pinned-stderr.log"
PINNED_MODE_FIXTURE_STATUS=$?
set -e
if [[ "$PINNED_MODE_FIXTURE_STATUS" -ne 66 ]] \
  || ! /usr/bin/grep -F -- 'required daily automation asset is not executable:' \
    "$MODE_FIXTURE_ROOT/pinned-stderr.log" >/dev/null \
  || ! /usr/bin/grep -F -- "$MODE_FIXTURE_ROOT/run-pinned-review.py" \
    "$MODE_FIXTURE_ROOT/pinned-stderr.log" >/dev/null \
  || [[ -d "$MODE_FIXTURE_ROOT/logs" ]]; then
  echo "non-executable pinned review runner must fail preflight with status 66" >&2
  exit 1
fi
/bin/chmod 0755 "$MODE_FIXTURE_ROOT/run-pinned-review.py"

COLLECTION_BLOCK="$(sed -n '/CODEX_BIN.*--search/,/COLLECTION_STATUS=/p' "$RUNNER")"
REVIEW_BLOCK="$(sed -n '/^"\$PINNED_REVIEW_RUNNER"/,/REVIEW_STATUS=/p' "$RUNNER" | sed -n '1,/REVIEW_STATUS=/p')"
PUBLICATION_BLOCK="$(sed -n '/^"\$LOCAL_COMMITTER"/,/PUBLICATION_STATUS=/p' "$RUNNER")"
EVIDENCE_REVIEW_BLOCK="$(sed -n '/EVIDENCE_REVIEW_ENVELOPE=/,/^  "\$EVIDENCE_FINALIZER"/p' "$RUNNER")"

if print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -E 'AGENTS_GIT_DIR|USER_GIT_DIR|AGENTS_ROOT|USER_ROOT' >/dev/null; then
  echo "collection block contains Vault publication privileges" >&2
  exit 1
fi
if ! print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -F -- '-C "$STAGING_ROOT"' >/dev/null; then
  echo "collection cwd must be the run staging root" >&2
  exit 1
fi
if ! print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -F -- '-m gpt-5.6-luna' >/dev/null; then
  echo "collection must pin the requested GPT-5.6 Luna model" >&2
  exit 1
fi
if ! print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -F -- 'model_reasoning_effort="medium"' >/dev/null; then
  echo "collection must pin Luna reasoning effort" >&2
  exit 1
fi
if ! print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -F -- '<<< "$COLLECTION_PROMPT_CONTENT"' >/dev/null; then
  echo "collection prompt must use stdin instead of argv" >&2
  exit 1
fi
if ! print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -F -- '--output-last-message "$COLLECTION_AGENT_RESULT"' >/dev/null; then
  echo "collection must preserve the raw agent result" >&2
  exit 1
fi
if print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -F -- '-C "$WORKDIR"' >/dev/null; then
  echo "collection must not make the automation root writable" >&2
  exit 1
fi
if ! print -r -- "$REVIEW_BLOCK" | /usr/bin/grep -F -- '--sandbox read-only' >/dev/null; then
  echo "publication review must be read-only" >&2
  exit 1
fi
if ! print -r -- "$REVIEW_BLOCK" | /usr/bin/grep -F -- 'PINNED_REVIEW_RUNNER' >/dev/null; then
  echo "publication review must use the pinned stdin executor" >&2
  exit 1
fi
if ! print -r -- "$REVIEW_BLOCK" | /usr/bin/grep -F -- '--output-last-message "$REVIEW_AGENT_RESULT"' >/dev/null; then
  echo "publication review must preserve the raw agent result" >&2
  exit 1
fi
if print -r -- "$PUBLICATION_BLOCK" | /usr/bin/grep -F -- 'CODEX_BIN' >/dev/null; then
  echo "local publication must not delegate Vault mutation to Codex" >&2
  exit 1
fi
if ! print -r -- "$PUBLICATION_BLOCK" | /usr/bin/grep -F -- '"$REVIEW_RESULT"' >/dev/null; then
  echo "local publication must consume the approved review" >&2
  exit 1
fi
if ! print -r -- "$EVIDENCE_REVIEW_BLOCK" | /usr/bin/grep -F -- '--sandbox read-only' >/dev/null; then
  echo "evidence finalization review must be read-only" >&2
  exit 1
fi
if ! print -r -- "$EVIDENCE_REVIEW_BLOCK" | /usr/bin/grep -F -- 'PINNED_REVIEW_RUNNER' >/dev/null; then
  echo "evidence review must use the pinned stdin executor" >&2
  exit 1
fi
if /usr/bin/grep -F -- '< "$REVIEW_REQUEST_FILE"' "$RUNNER" >/dev/null \
  || /usr/bin/grep -F -- '< "$EVIDENCE_REVIEW_REQUEST_FILE"' "$RUNNER" >/dev/null; then
  echo "review requests must not be reopened by pathname" >&2
  exit 1
fi
/usr/bin/grep -F -- '"$REVIEW_RESULT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$REVIEW_RESULT_SHA256"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$REVIEW_AGENT_RESULT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$REVIEW_NORMALIZATION_RECEIPT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'REVIEW_INPUT_METRICS_FILE' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'REVIEW_INPUT_METRICS_SHA256' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'EVIDENCE_REVIEW_INPUT_METRICS_SHA256' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'EVIDENCE_REVIEW_REASON=' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'EVIDENCE_REVIEW_REASON_CODE=' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'EVIDENCE_REVIEW_STDERR_SHA256=' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'write_evidence_review_diagnostic() {' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'evidence-review-diagnostic.json' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'EVIDENCE_REVIEW_PROCESS_STATUS="$process_status"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'classify_evidence_review() {' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'classify_evidence_review \' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$EVIDENCE_REVIEW_REASON"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$EVIDENCE_REVIEW_DIAGNOSTIC_FILE"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'evidence_review' "$SCRIPT_DIR/../references/automation-result.schema.json" >/dev/null
/usr/bin/grep -F -- '"$REVIEW_VALIDATOR" --canonicalize-own-only' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$COLLECTION_VALIDATOR" --canonicalize-constraints' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$PUBLICATION_CONTEXT_FILE"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$ARTIFACT_PLAN"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'mkdir "$RUN_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'release_integrity' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'runtime release manifest verification failed' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'COLLECTION_START_STATE="$RUN_ROOT/collection-start-state.json"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$STATE_CAPTURE" --index-only "$RUNTIME_CONTEXT_FILE" > "$COLLECTION_START_STATE"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$STATE_CAPTURE" --index-only --include-local-history "$RUNTIME_CONTEXT_FILE"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$EVIDENCE_FINALIZER"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'git_diff_digest.py' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'isolated_git_transport.py' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'atomic_file_ops.py' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'trusted_gitleaks.py' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'gitleaks-default.toml' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'fail_run 75 artifact_plan' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '/bin/cp "$EFFECTIVE_PUSH_RESULT" "$PUBLICATION_RESULT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'notification_status=$NOTIFICATION_STATUS' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'SEMANTIC_STATUS="notification_failed"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'NOTIFICATION_FALLBACK_PUSH_RESULT=' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'error_code=sender_failed_closed' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '[[ "$NOTIFICATION_RESULT_VALID" == "true" ]]' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '`already_delivered`' \
  "$SCRIPT_DIR/../assets/daily-it-news.evidence-review.prompt.md" >/dev/null
/usr/bin/grep -F -- 'raw Hermes' \
  "$SCRIPT_DIR/../assets/daily-it-news.evidence-review.prompt.md" >/dev/null
/usr/bin/grep -F -- '"$NOTIFICATION_SENDER" \' "$RUNNER" >/dev/null
NOTIFICATION_TERMINAL_BLOCK="$(sed -n '/^if \[\[ "\$NOTIFICATION_DISPOSITION" == "attempted" \]\]/,/^fi$/p' "$RUNNER")"
if print -r -- "$NOTIFICATION_TERMINAL_BLOCK" \
  | /usr/bin/grep -F -- '[[ "$STATUS" -eq 0 ]]' >/dev/null; then
  echo "notification failure must override a prior terminal status" >&2
  exit 1
fi
PUSH_LINE="$(/usr/bin/grep -n -F -- '"$FIXED_PUSHER" \' "$RUNNER" | /usr/bin/tail -n 1 | /usr/bin/cut -d: -f1)"
NOTIFICATION_LINE="$(/usr/bin/grep -n -F -- '"$NOTIFICATION_SENDER" \' "$RUNNER" | /usr/bin/tail -n 1 | /usr/bin/cut -d: -f1)"
EVIDENCE_LINE="$(/usr/bin/grep -n -F -- '"$EVIDENCE_PREPARER" \' "$RUNNER" | /usr/bin/cut -d: -f1)"
if [[ "$PUSH_LINE" -ge "$NOTIFICATION_LINE" || "$NOTIFICATION_LINE" -ge "$EVIDENCE_LINE" ]]; then
  echo "Discord notification must run after fixed push and before evidence preparation" >&2
  exit 1
fi
EVIDENCE_PREPARATION_BLOCK="$(sed -n '/^if \[\[ "\$EVIDENCE_PREPARE_STATUS" -eq 0 \]\]; then$/,/^fi$/p' "$RUNNER")"
EVIDENCE_FALLBACK_BLOCK="$(print -r -- "$EVIDENCE_PREPARATION_BLOCK" | sed -n '/^[[:space:]]*else[[:space:]]*$/,$p')"
if [[ -z "$EVIDENCE_FALLBACK_BLOCK" ]]; then
  echo "evidence preparation fallback block not found" >&2
  exit 1
fi
if print -r -- "$EVIDENCE_FALLBACK_BLOCK" | /usr/bin/grep -F -- '"$EVIDENCE_PLAN"' >/dev/null; then
  echo "failed evidence preparation must not invoke finalizer with a missing plan" >&2
  exit 1
fi
/usr/bin/grep -F -- 'COLLECTION_OUTPUT_ROOT="$STAGING_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$COLLECTION_FETCHER" "$SOURCE_CATALOG" "$SOURCE_INPUT_ROOT" "$STARTED_AT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'local exit_code="$1"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'FIXED_FETCHER="$WORKDIR/fetch-vault-main.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$FIXED_FETCHER" "$RUNTIME_CONTEXT_FILE"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$LOCAL_COMMITTER" --recover "$RUNTIME_CONTEXT_FILE"' "$RUNNER" >/dev/null
RECOVERY_LINE="$(/usr/bin/grep -n -F -- '"$LOCAL_COMMITTER" --recover "$RUNTIME_CONTEXT_FILE"' "$RUNNER" | /usr/bin/cut -d: -f1)"
FETCH_LINE="$(/usr/bin/grep -n -F -- '"$FIXED_FETCHER" "$RUNTIME_CONTEXT_FILE"' "$RUNNER" | /usr/bin/cut -d: -f1)"
CAPTURE_LINE="$(/usr/bin/grep -n -F -- '"$STATE_CAPTURE" --index-only "$RUNTIME_CONTEXT_FILE" > "$COLLECTION_START_STATE"' "$RUNNER" | /usr/bin/cut -d: -f1)"
if [[ "$RECOVERY_LINE" -ge "$FETCH_LINE" || "$RECOVERY_LINE" -ge "$CAPTURE_LINE" ]]; then
  echo "durable transaction recovery must precede fetch and collection-state capture" >&2
  exit 1
fi
/usr/bin/grep -F -- 'SCHEMA_PROJECTOR="$WORKDIR/prepare-codex-output-schema.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'CANONICAL_VALIDATOR="$WORKDIR/validate-canonical-result.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'STANDING_TASK_STAGER="$WORKDIR/stage-standing-task.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'DIRTY_REVIEW_STAGER="$WORKDIR/stage-dirty-review-inputs.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'MODE_DETERMINER="$WORKDIR/determine-publication-modes.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'PUBLICATION_MODE_HINT="$ATTEMPT_ROOT/publication-mode-hint.json"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$MODE_DETERMINER" --apply-residual-guards' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$DIRTY_SNAPSHOT_MANIFEST"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '/usr/bin/shlock -f "$PUBLICATION_LOCK" -p $$' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '[[ "$observed_owner" == "$$" ]] || return 1' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'vault_state_snapshot_unstable' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'artifact_target_replan_exhausted' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'AGENTS_SELECTED_STATE=' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'USER_SELECTED_STATE=' "$RUNNER" >/dev/null
if [[ "$(/usr/bin/grep -c -F -- 'AGENTS_STABLE_STATE=0' "$RUNNER")" -ne 1 \
  || "$(/usr/bin/grep -c -F -- 'USER_STABLE_STATE=0' "$RUNNER")" -ne 1 ]]; then
  echo "per-Vault stabilization flags must survive later snapshot attempts" >&2
  exit 1
fi
/usr/bin/grep -F -- 'if [[ "$AGENTS_STABLE_STATE" -ne 1 ]]; then' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'if [[ "$USER_STABLE_STATE" -ne 1 ]]; then' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$STATE_CAPTURE" --index-only --include-local-history' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'The interpreter performs one stable descriptor read' "$RUNNER" >/dev/null
NORMALIZATION_FAILURE_BLOCK="$(sed -n \
  '/^if ! "\$REVIEW_VALIDATOR" --canonicalize-own-only/,/^fi$/p' "$RUNNER")"
for audit_pointer in \
  'review_agent_result=$REVIEW_AGENT_RESULT' \
  'review_result=$REVIEW_RESULT' \
  'review_normalization_receipt=$REVIEW_NORMALIZATION_RECEIPT'; do
  if ! print -r -- "$NORMALIZATION_FAILURE_BLOCK" \
    | /usr/bin/grep -F -- "$audit_pointer" >/dev/null; then
    echo "normalization failure status must identify all review audit files" >&2
    exit 1
  fi
done
COLLECTION_NORMALIZATION_FAILURE_BLOCK="$(sed -n \
  '/^if ! "\$COLLECTION_VALIDATOR" --canonicalize-constraints/,/^fi$/p' "$RUNNER")"
for audit_pointer in \
  'collection_agent_result=$COLLECTION_AGENT_RESULT' \
  'collection_result=$COLLECTION_RESULT' \
  'collection_normalization_receipt=$COLLECTION_NORMALIZATION_RECEIPT'; do
  if ! print -r -- "$COLLECTION_NORMALIZATION_FAILURE_BLOCK" \
    | /usr/bin/grep -F -- "$audit_pointer" >/dev/null; then
    echo "normalization failure status must identify all collection audit files" >&2
    exit 1
  fi
done
TERMINAL_STATUS_BLOCK="$(sed -n '/^{$/,/^} > "\$STATUS_FILE"$/p' "$RUNNER" | tail -n 32)"
for audit_pointer in \
  'collection_agent_result=$COLLECTION_AGENT_RESULT' \
  'collection_result=$COLLECTION_RESULT' \
  'collection_normalization_receipt=$COLLECTION_NORMALIZATION_RECEIPT' \
  'review_agent_result=$REVIEW_AGENT_RESULT' \
  'review_result=$REVIEW_RESULT' \
  'review_normalization_receipt=$REVIEW_NORMALIZATION_RECEIPT'; do
  if ! print -r -- "$TERMINAL_STATUS_BLOCK" \
    | /usr/bin/grep -F -- "$audit_pointer" >/dev/null; then
    echo "terminal status must identify all review audit files" >&2
    exit 1
  fi
done
SNAPSHOT_BLOCK="$(sed -n \
  '/^AGENTS_STABLE_STATE=0$/,/^if ! "\$MODE_DETERMINER"/p' "$RUNNER" \
  | sed '$d')"
[[ -n "$SNAPSHOT_BLOCK" ]] || {
  echo "snapshot stabilization block extraction was empty" >&2
  exit 1
}
/usr/bin/grep -F -- 'vault_state_snapshot_unstable' <<<"$SNAPSHOT_BLOCK" >/dev/null || {
  echo "snapshot stabilization block extraction missed its failure marker" >&2
  exit 1
}
FAKE_STATE_CAPTURE="$SNAPSHOT_FIXTURE_ROOT/capture-vault-state.py"
cat > "$FAKE_STATE_CAPTURE" <<'ZSH'
#!/bin/zsh
set -eu
include_history=0
index_only=0
for argument in "$@"; do
  [[ "$argument" == "--include-local-history" ]] && include_history=1
  [[ "$argument" == "--index-only" ]] && index_only=1
done
[[ "$index_only" -eq 1 ]] || {
  echo "snapshot capture omitted index-only mode" >&2
  exit 1
}
attempt="$(<"$SNAPSHOT_COUNTER")"
if [[ "$include_history" -eq 1 ]]; then
  attempt=$((attempt + 1))
  print -r -- "$attempt" > "$SNAPSHOT_COUNTER"
fi
case "$SNAPSHOT_SCENARIO:$attempt:$include_history" in
  cross:1:1) agents=A1; user=U1 ;;
  cross:1:0) agents=A1; user=U2 ;;
  cross:2:1) agents=A2; user=U2 ;;
  cross:2:0) agents=A3; user=U2 ;;
  unstable_agents:*:1) agents="A$attempt"; user=U1 ;;
  unstable_agents:*:0) agents="AX$attempt"; user=U1 ;;
  unstable_user:*:1) agents=A1; user="U$attempt" ;;
  unstable_user:*:0) agents=A1; user="UX$attempt" ;;
  both_unstable:*:1) agents="A$attempt"; user="U$attempt" ;;
  both_unstable:*:0) agents="AX$attempt"; user="UX$attempt" ;;
  *) exit 65 ;;
esac
if [[ "$include_history" -eq 1 ]]; then
  /usr/bin/jq -n --arg agents "$agents" --arg user "$user" '{
    agents_vault:{marker:$agents,local_commits:[],history_capture_status:"available",history_capture_reason:null,history_snapshot_sha256:("a"*64)},
    user_vault:{marker:$user,local_commits:[],history_capture_status:"available",history_capture_reason:null,history_snapshot_sha256:("b"*64)}
  }'
else
  /usr/bin/jq -n --arg agents "$agents" --arg user "$user" \
    '{agents_vault:{marker:$agents},user_vault:{marker:$user}}'
fi
ZSH
/bin/chmod 0755 "$FAKE_STATE_CAPTURE"
run_snapshot_case() {
  local scenario="$1"
  local case_root="$SNAPSHOT_FIXTURE_ROOT/$scenario"
  /bin/mkdir "$case_root"
  print -r -- 0 > "$case_root/counter"
  (
    export SNAPSHOT_SCENARIO="$scenario"
    export SNAPSHOT_COUNTER="$case_root/counter"
    ATTEMPT_ROOT="$case_root"
    STATE_CAPTURE="$FAKE_STATE_CAPTURE"
    RUNTIME_CONTEXT_FILE="$case_root/runtime.json"
    REVIEWED_PUBLICATION_STATE="$case_root/reviewed.json"
    fail_run() {
      exit "$1"
    }
    eval "$SNAPSHOT_BLOCK"
  )
}
run_snapshot_case cross
/usr/bin/jq -e '
  .agents_vault.marker == "A1"
  and .user_vault.marker == "U2"
' "$SNAPSHOT_FIXTURE_ROOT/cross/reviewed.json" >/dev/null || {
  echo "Vaults that stabilize on different attempts were not preserved independently" >&2
  exit 1
}
run_snapshot_case unstable_agents
/usr/bin/jq -e '
  .agents_vault.marker == "A3"
  and .agents_vault.capture_status == "blocked"
  and .agents_vault.capture_reason == "vault_state_snapshot_unstable"
  and .user_vault.marker == "U1"
  and (.user_vault.capture_status // "available") == "available"
' "$SNAPSHOT_FIXTURE_ROOT/unstable_agents/reviewed.json" >/dev/null || {
  echo "persistent instability did not block only the affected Vault" >&2
  exit 1
}
run_snapshot_case unstable_user
/usr/bin/jq -e '
  .agents_vault.marker == "A1"
  and (.agents_vault.capture_status // "available") == "available"
  and .user_vault.marker == "U3"
  and .user_vault.capture_status == "blocked"
  and .user_vault.capture_reason == "vault_state_snapshot_unstable"
' "$SNAPSHOT_FIXTURE_ROOT/unstable_user/reviewed.json" >/dev/null || {
  echo "persistent User instability changed its stable peer" >&2
  exit 1
}
run_snapshot_case both_unstable
/usr/bin/jq -e '
  .agents_vault.marker == "A3"
  and .agents_vault.capture_status == "blocked"
  and .agents_vault.capture_reason == "vault_state_snapshot_unstable"
  and .user_vault.marker == "U3"
  and .user_vault.capture_status == "blocked"
  and .user_vault.capture_reason == "vault_state_snapshot_unstable"
' "$SNAPSHOT_FIXTURE_ROOT/both_unstable/reviewed.json" >/dev/null || {
  echo "persistent two-Vault instability did not block both independently" >&2
  exit 1
}
REPLAN_BLOCK="$(sed -n \
  '/^  EXHAUSTED_MODE_HINT=/,/^    || fail_run 75 artifact_plan "could not seal exhausted artifact target re-plan"$/p' \
  "$RUNNER")"
[[ -n "$REPLAN_BLOCK" ]] || {
  echo "artifact target replan block extraction was empty" >&2
  exit 1
}
/usr/bin/grep -F -- 'artifact_target_replan_exhausted' <<<"$REPLAN_BLOCK" >/dev/null || {
  echo "artifact target replan block extraction missed its failure marker" >&2
  exit 1
}
REPLAN_FIXTURE_ROOT="$SNAPSHOT_FIXTURE_ROOT/replan"
/bin/mkdir "$REPLAN_FIXTURE_ROOT"
/usr/bin/jq -n '{
  agents_vault:{required_mode:"own_only",retry_disposition:"replan",reasons:["artifact_target_conflict"]},
  user_vault:{required_mode:"sweep",retry_disposition:"none",reasons:["stable_sweep_candidate"]}
}' > "$REPLAN_FIXTURE_ROOT/publication-mode-hint.json"
USER_MODE_BEFORE="$(/usr/bin/jq -cS '.user_vault' "$REPLAN_FIXTURE_ROOT/publication-mode-hint.json")"
(
  ATTEMPT_ROOT="$REPLAN_FIXTURE_ROOT"
  PUBLICATION_MODE_HINT="$REPLAN_FIXTURE_ROOT/publication-mode-hint.json"
  fail_run() {
    exit "$1"
  }
  eval "$REPLAN_BLOCK"
)
/usr/bin/jq -e '
  .agents_vault.required_mode == "blocked"
  and .agents_vault.retry_disposition == "none"
  and (.agents_vault.reasons | index("artifact_target_replan_exhausted")) != null
' "$REPLAN_FIXTURE_ROOT/publication-mode-hint.json" >/dev/null || {
  echo "exhausted target replan did not block the affected Vault" >&2
  exit 1
}
if [[ "$(/usr/bin/jq -cS '.user_vault' "$REPLAN_FIXTURE_ROOT/publication-mode-hint.json")" \
  != "$USER_MODE_BEFORE" ]]; then
  echo "exhausted target replan changed the unaffected Vault" >&2
  exit 1
fi
CARRY_SELECTOR="$(sed -n '/^select_carried_commit_result() {$/,/^}$/p' "$RUNNER")"
[[ -n "$CARRY_SELECTOR" ]] || {
  echo "carried-result selector extraction was empty" >&2
  exit 1
}
CARRY_FIXTURE_ROOT="$SNAPSHOT_FIXTURE_ROOT/carried-result"
/bin/mkdir "$CARRY_FIXTURE_ROOT"
print -r -- 'null' > "$CARRY_FIXTURE_ROOT/none.json"
print -r -- '{"outcome":"blocked"}' > "$CARRY_FIXTURE_ROOT/blocked.json"
print -r -- '{"outcome":"partial_publication"}' \
  > "$CARRY_FIXTURE_ROOT/partial.json"
(
  NO_CARRIED_COMMIT_RESULT="$CARRY_FIXTURE_ROOT/none.json"
  CARRIED_COMMIT_RESULT="$NO_CARRIED_COMMIT_RESULT"
  eval "$CARRY_SELECTOR"
  select_carried_commit_result "$CARRY_FIXTURE_ROOT/blocked.json"
  [[ "$CARRIED_COMMIT_RESULT" == "$NO_CARRIED_COMMIT_RESULT" ]] || {
    echo "no-progress blocked result was promoted to carried progress" >&2
    exit 1
  }
  select_carried_commit_result "$CARRY_FIXTURE_ROOT/partial.json"
  [[ "$CARRIED_COMMIT_RESULT" == "$CARRY_FIXTURE_ROOT/partial.json" ]] || {
    echo "partial same-run progress was not retained for re-plan" >&2
    exit 1
  }
)
collection_line="$(/usr/bin/grep -n -F -- 'COLLECTION_STATUS=$?' "$RUNNER" | /usr/bin/cut -d: -f1)"
history_line="$(/usr/bin/grep -n -F -- '"$STATE_CAPTURE" --index-only --include-local-history' "$RUNNER" | /usr/bin/head -n 1 | /usr/bin/cut -d: -f1)"
if [[ -z "$collection_line" || -z "$history_line" || "$history_line" -le "$collection_line" ]]; then
  echo "local-only history materialization must happen after collection" >&2
  exit 1
fi
/usr/bin/grep -F -- '--arg standing_task "$STANDING_TASK_SNAPSHOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--arg source_catalog "$SOURCE_CATALOG"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'COLLECTION_OUTPUT_ROOT="$RUN_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$COLLECTION_FETCHER" "$SOURCE_CATALOG" "$SOURCE_INPUT_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--arg source_manifest "$SOURCE_MANIFEST"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--arg resolution_verifier "$COLLECTION_FETCHER"' "$RUNNER" >/dev/null
if /usr/bin/grep -F -- 'resolution_verification_root' "$RUNNER" >/dev/null; then
  echo "agent-visible context must not select the verifier run root" >&2
  exit 1
fi
/usr/bin/grep -F -- 'chmod -R a-w "$SOURCE_INPUT_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$COLLECTION_FETCHER" --verify-resolutions' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$RESOLUTION_REQUEST" "$VERIFIED_RESOLUTIONS"' "$RUNNER" >/dev/null
RAW_SCHEMA_LINE="$(/usr/bin/grep -n -F -- '"$CANONICAL_VALIDATOR" "$COLLECTION_SCHEMA" "$COLLECTION_AGENT_RESULT"' "$RUNNER" | /usr/bin/head -n 1 | /usr/bin/cut -d: -f1)"
RESOLUTION_VERIFY_LINE="$(/usr/bin/grep -n -F -- '"$COLLECTION_FETCHER" --verify-resolutions' "$RUNNER" | /usr/bin/head -n 1 | /usr/bin/cut -d: -f1)"
COLLECTION_PROJECTION_LINE="$(/usr/bin/grep -n -F -- '"$COLLECTION_VALIDATOR" --canonicalize-constraints' "$RUNNER" | /usr/bin/head -n 1 | /usr/bin/cut -d: -f1)"
if [[ -z "$RAW_SCHEMA_LINE" || -z "$RESOLUTION_VERIFY_LINE" || -z "$COLLECTION_PROJECTION_LINE" ]] \
  || [[ "$RAW_SCHEMA_LINE" -ge "$RESOLUTION_VERIFY_LINE" ]] \
  || [[ "$RESOLUTION_VERIFY_LINE" -ge "$COLLECTION_PROJECTION_LINE" ]]; then
  echo "collection authority order must be raw schema, resolution verification, projection" >&2
  exit 1
fi
if /usr/bin/grep -F -- '"$SOURCE_CATALOG" "$SOURCE_MANIFEST" "$RESOLUTION_REQUEST"' "$RUNNER" >/dev/null; then
  echo "runner must not expose arbitrary verifier catalog or manifest arguments" >&2
  exit 1
fi
/usr/bin/grep -F -- 'AUTHORIZATION_TASK_SNAPSHOT="$REVIEW_INPUT_ROOT/authorization-task.md"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$RUNTIME_CONTEXT_FILE" "$REVIEW_INPUT_ROOT" authorization' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--arg authorization_task "$AUTHORIZATION_TASK_SNAPSHOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$RUNTIME_CONTEXT_FILE" "$REVIEWED_PUBLICATION_STATE" "$SEALED_REVIEW_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'while [[ "$PUBLICATION_ATTEMPT" -le 4 ]]' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '.retry_disposition == "replan"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'chmod 700 "$SEALED_REVIEW_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--arg dirty_snapshot_manifest_file "$DIRTY_SNAPSHOT_MANIFEST"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--output-schema "$CODEX_COLLECTION_SCHEMA"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'INTERPRETER_PROCESS_STATUS="$FINALIZATION_STATUS"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'evidence_review_reason=$EVIDENCE_REVIEW_REASON' "$RUNNER" >/dev/null
if /usr/bin/grep -F -- 'INTERPRETER_PROCESS_STATUS=0' "$RUNNER" >/dev/null; then
  echo "runner must not erase a nonzero finalizer status when a result file exists" >&2
  exit 1
fi
if /usr/bin/grep -F -- 'fetch origin main' "$RUNNER" >/dev/null; then
  echo "runner must not trust a mutable remote name or ambiguous refspec" >&2
  exit 1
fi
if /usr/bin/grep -F -- 'local status="$1"' "$RUNNER" >/dev/null; then
  echo "fail_run must not shadow zsh's read-only status parameter" >&2
  exit 1
fi
FAIL_RUN_FUNCTION="$(sed -n '/^fail_run() {/,/^}/p' "$RUNNER")"
CLASSIFY_EVIDENCE_FUNCTION="$(sed -n '/^classify_evidence_review() {/,/^}/p' "$RUNNER")"
WRITE_EVIDENCE_DIAGNOSTIC_FUNCTION="$(sed -n '/^write_evidence_review_diagnostic() {/,/^}/p' "$RUNNER")"
LOCK_RELEASE_FUNCTION="$(sed -n '/^release_publication_lock() {/,/^}/p' "$RUNNER")"
[[ -n "$CLASSIFY_EVIDENCE_FUNCTION" ]] || {
  echo "evidence review classification function extraction was empty" >&2
  exit 1
}
[[ -n "$WRITE_EVIDENCE_DIAGNOSTIC_FUNCTION" ]] || {
  echo "evidence review diagnostic function extraction was empty" >&2
  exit 1
}
for audit_pointer in \
  'collection_agent_result=' \
  'collection_result=' \
  'collection_normalization_receipt='; do
  if ! print -r -- "$FAIL_RUN_FUNCTION" | /usr/bin/grep -F -- "$audit_pointer" >/dev/null; then
    echo "every common failure status must retain collection audit pointers" >&2
    exit 1
  fi
done
FAIL_RUN_ROOT="$(mktemp -d)"
if /bin/zsh -c '
  set -u
  RUN_ID=fixture-run
  STARTED_AT=2026-08-01T04:00:00+09:00
  STATUS_FILE="$1"
  eval "$2"
  fail_run 75 preflight fixture-reason
' -- "$FAIL_RUN_ROOT/status.txt" "$FAIL_RUN_FUNCTION"; then
  echo "fail_run unexpectedly returned success" >&2
  exit 1
else
  FAIL_RUN_STATUS=$?
fi
if [[ "$FAIL_RUN_STATUS" -ne 75 ]] \
  || ! /usr/bin/grep -Fx -- 'status=75' "$FAIL_RUN_ROOT/status.txt" >/dev/null \
  || ! /usr/bin/grep -Fx -- 'phase=preflight' "$FAIL_RUN_ROOT/status.txt" >/dev/null; then
  echo "fail_run did not preserve the structured status contract" >&2
  exit 1
fi
print -r -- foreign-owner > "$FAIL_RUN_ROOT/publication.lock"
if /bin/zsh -c '
  set -u
  PUBLICATION_LOCK="$1"
  PUBLICATION_LOCK_OWNED=1
  eval "$2"
  release_publication_lock
' -- "$FAIL_RUN_ROOT/publication.lock" "$LOCK_RELEASE_FUNCTION"; then
  echo "foreign publication lock was treated as owned" >&2
  exit 1
fi
if [[ ! -f "$FAIL_RUN_ROOT/publication.lock" ]]; then
  echo "foreign publication lock was removed" >&2
  exit 1
fi
/bin/rm -f "$FAIL_RUN_ROOT/publication.lock"
if /bin/zsh -c '
  set -u
  PUBLICATION_LOCK="$1"
  PUBLICATION_LOCK_OWNED=1
  eval "$2"
  release_publication_lock
' -- "$FAIL_RUN_ROOT/publication.lock" "$LOCK_RELEASE_FUNCTION"; then
  echo "missing owned publication lock was treated as released" >&2
  exit 1
fi
/bin/zsh -c '
  set -u
  PUBLICATION_LOCK="$1"
  PUBLICATION_LOCK_OWNED=1
  print -r -- "$$" > "$PUBLICATION_LOCK"
  eval "$2"
  release_publication_lock
  [[ ! -e "$PUBLICATION_LOCK" ]]
' -- "$FAIL_RUN_ROOT/publication.lock" "$LOCK_RELEASE_FUNCTION" || {
  echo "owned publication lock was not released" >&2
  exit 1
}
EVIDENCE_DIAGNOSTIC_ROOT="$FAIL_RUN_ROOT/evidence-diagnostic"
/bin/mkdir "$EVIDENCE_DIAGNOSTIC_ROOT"
print -r -- $'\033[31msecret-token\033[0m input exceeds maximum length' > "$EVIDENCE_DIAGNOSTIC_ROOT/stderr.log"
/bin/zsh -c '
  set -u
  CANONICAL_VALIDATOR=/usr/bin/false
  eval "$1"
  classify_evidence_review 17 "$2/missing-result.json" "$2/stderr.log" "$2/schema.json"
  [[ "$EVIDENCE_REVIEW_PROCESS_STATUS" -eq 17 ]]
  [[ "$EVIDENCE_REVIEW_STATUS" -eq 75 ]]
  [[ "$EVIDENCE_REVIEW_REASON_CODE" == "input_too_large" ]]
  [[ "$EVIDENCE_REVIEW_REASON" == "reason_code=input_too_large;process_status=17;status=75;stderr_sha256="* ]]
  [[ "$EVIDENCE_REVIEW_REASON" != *secret-token* ]]
' -- "$CLASSIFY_EVIDENCE_FUNCTION" "$EVIDENCE_DIAGNOSTIC_ROOT" || {
  echo "evidence input-limit diagnostics were not preserved before finalization" >&2
  exit 1
}
/bin/zsh -c '
  set -u
  CANONICAL_VALIDATOR=/usr/bin/false
  eval "$1"
  : > "$2/empty.stderr"
  classify_evidence_review 0 "$2/missing-result.json" "$2/empty.stderr" "$2/schema.json"
  [[ "$EVIDENCE_REVIEW_STATUS" -eq 75 ]]
  [[ "$EVIDENCE_REVIEW_REASON_CODE" == "result_missing" ]]
' -- "$CLASSIFY_EVIDENCE_FUNCTION" "$EVIDENCE_DIAGNOSTIC_ROOT" || {
  echo "missing evidence result was misclassified as a process error" >&2
  exit 1
}
/bin/zsh -c '
  set -u
  EVIDENCE_REVIEW_PROCESS_STATUS=17
  EVIDENCE_REVIEW_STATUS=75
  EVIDENCE_REVIEW_REASON_CODE=input_too_large
  EVIDENCE_REVIEW_RESULT_PRESENT=false
  EVIDENCE_REVIEW_RESULT_SHA256=""
  EVIDENCE_REVIEW_STDERR_SHA256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  eval "$1"
  EVIDENCE_REVIEW_DIAGNOSTIC_FILE="$2/diagnostic.json"
  write_evidence_review_diagnostic "$EVIDENCE_REVIEW_DIAGNOSTIC_FILE"
  /usr/bin/grep -F -- "\"reason_code\": \"input_too_large\"" "$EVIDENCE_REVIEW_DIAGNOSTIC_FILE" >/dev/null
  /usr/bin/grep -F -- "\"process_status\": 17" "$EVIDENCE_REVIEW_DIAGNOSTIC_FILE" >/dev/null
  /usr/bin/grep -F -- "\"status\": 75" "$EVIDENCE_REVIEW_DIAGNOSTIC_FILE" >/dev/null
  /usr/bin/grep -F -- "\"result_present\": false" "$EVIDENCE_REVIEW_DIAGNOSTIC_FILE" >/dev/null
  /usr/bin/grep -F -- "\"result_sha256\": null" "$EVIDENCE_REVIEW_DIAGNOSTIC_FILE" >/dev/null
' -- "$WRITE_EVIDENCE_DIAGNOSTIC_FUNCTION" "$EVIDENCE_DIAGNOSTIC_ROOT" || {
  echo "evidence diagnostics leaked raw stderr or lost structured fields" >&2
  exit 1
}
rm -rf "$FAIL_RUN_ROOT"
for forbidden in "/""Users/" "Library/Mobile"" Documents" "Yasu""'s Vault"; do
  if /usr/bin/grep -F -- "$forbidden" "$RUNNER" >/dev/null; then
    echo "tracked runner contains a personal path" >&2
    exit 1
  fi
done

echo "runner isolation contract: 62/62 passed"
