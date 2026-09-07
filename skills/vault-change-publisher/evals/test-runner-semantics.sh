#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
INTERPRETER="$SCRIPT_DIR/../scripts/interpret-automation-result.sh"
FIXTURE_ROOT="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_ROOT"' EXIT
LOCAL_HEAD="1111111111111111111111111111111111111111"
REMOTE_HEAD="2222222222222222222222222222222222222222"
AGENTS_COMMIT="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
USER_COMMIT="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
EVIDENCE_COMMIT="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

write_result() {
  local outcome="$1"
  local agents_commits="$2"
  local user_commits="$3"
  local agents_push="$4"
  local user_push="$5"
  local same_heads="$6"
  local next_action="${7:-repair state}"
  local remote="$LOCAL_HEAD"
  [[ "$same_heads" == "true" ]] || remote="$REMOTE_HEAD"
  /usr/bin/jq -n \
    --arg outcome "$outcome" \
    --argjson ac "$agents_commits" \
    --argjson uc "$user_commits" \
    --arg ap "$agents_push" \
    --arg up "$user_push" \
    --arg local_head "$LOCAL_HEAD" \
    --arg remote "$remote" \
    --arg next "$next_action" '{
      outcome:$outcome,phase:"publication",daily_pipeline_status:"complete",
      summary_path:"/staging/summary.md",advisory_path:"/staging/advisory.md",
      notification_result:"none",
      agents_vault:{commit_status:(if ($ac|length)>0 then "complete" else "not_started" end),commit_hashes:$ac,push_status:$ap,local_head:$local_head,remote_head:$remote,clean:true,publication_mode:(if $outcome == "blocked" then "blocked" else "sweep" end),deferred_cleanup:[]},
      user_vault:{commit_status:(if ($uc|length)>0 then "complete" else "not_started" end),commit_hashes:$uc,push_status:$up,local_head:$local_head,remote_head:$remote,clean:true,publication_mode:(if $outcome == "blocked" then "blocked" else "sweep" end),deferred_cleanup:[]},
      publication_mode:{agents_vault:(if $outcome == "blocked" then "blocked" else "sweep" end),user_vault:(if $outcome == "blocked" then "blocked" else "sweep" end)},
      deferred_cleanup:{agents_vault:[],user_vault:[]},
      evidence_finalization_commit:null,next_action:$next
    }' > "$FIXTURE_ROOT/result.json"
}

assert_result() {
  local expected="$1"
  local actual
  actual="$("$INTERPRETER" "${2:-0}" "$FIXTURE_ROOT/result.json")"
  [[ "$actual" == "$expected" ]] || {
    printf 'expected=%q actual=%q\n' "$expected" "$actual" >&2
    exit 1
  }
}

missing_tool="$(CANONICAL_VALIDATOR=/definitely/missing "$INTERPRETER" 0 "$FIXTURE_ROOT/result.json")"
[[ "$missing_tool" == $'missing_tool\t69' ]] || {
  printf 'expected missing_tool status, got %q\n' "$missing_tool" >&2
  exit 1
}

write_result blocked '[]' '[]' failed not_started true
assert_result $'blocked\t75'

write_result blocked '[]' '[]' failed not_started false
assert_result $'blocked\t75'

write_result blocked "[\"$AGENTS_COMMIT\"]" '[]' failed not_started false
assert_result $'invalid_result\t65'

write_result partial_publication "[\"$AGENTS_COMMIT\"]" "[\"$USER_COMMIT\"]" failed failed false
assert_result $'partial_publication\t75'
assert_result $'partial_publication\t75' 75

write_result partial_publication "[\"$AGENTS_COMMIT\"]" "[\"$USER_COMMIT\"]" failed not_started false
/usr/bin/jq '
  .user_vault.publication_mode="blocked"
  | .publication_mode.user_vault="blocked"
' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/blocked-peer.json"
mv "$FIXTURE_ROOT/blocked-peer.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

write_result partial_publication "[\"$AGENTS_COMMIT\"]" "[\"$USER_COMMIT\"]" failed failed false
/usr/bin/jq '.user_vault.commit_status="failed"' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/failed-hashes.json"
mv "$FIXTURE_ROOT/failed-hashes.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

write_result partial_publication '[]' '[]' failed failed true
assert_result $'invalid_result\t65'

write_result partial_publication '[]' '[]' failed failed true
/usr/bin/jq '.agents_vault.clean=false' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/dirty.json"
mv "$FIXTURE_ROOT/dirty.json" "$FIXTURE_ROOT/result.json"
assert_result $'partial_publication\t75'

write_result success "[\"$AGENTS_COMMIT\"]" "[\"$USER_COMMIT\"]" complete complete true ""
/usr/bin/jq --arg evidence "$EVIDENCE_COMMIT" '.evidence_finalization_commit=$evidence | .next_action=null' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/success.json"
mv "$FIXTURE_ROOT/success.json" "$FIXTURE_ROOT/result.json"
assert_result $'success\t0'
assert_result $'process_error\t75' 75

/usr/bin/jq '.evidence_recovery={
  target_path:"tasks/standing.md",quarantine_scope:"agents_git_dir",
  quarantine_root_identity:[1,2],base_head:("a"*40),candidate_head:("b"*40),
  original_restored:false,
  original_tombstone:{directory:".publication-evidence-original-fixture",directory_identity:[1,3],entry:"artifact",identity:[1,4],sha256:("c"*64),size:10,mode:33188},
  candidate:{identity:[1,5],sha256:("d"*64),size:11,mode:33188},
  head_updated:true,index_updated:true
}' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/recovery.json"
mv "$FIXTURE_ROOT/recovery.json" "$FIXTURE_ROOT/result.json"
assert_result $'success\t0'

/usr/bin/jq '.evidence_recovery.target_path="/private/task.md"' \
  "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/absolute-recovery.json"
mv "$FIXTURE_ROOT/absolute-recovery.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

/usr/bin/jq '
  .evidence_recovery.target_path="tasks/standing.md"
  | .user_vault.publication_mode="own_only"
  | .user_vault.clean=false
  | .user_vault.deferred_cleanup=[{"path":".codex-handoff/unsafe.md","reason":"guard rejection"}]
  | .publication_mode.user_vault="own_only"
  | .deferred_cleanup.user_vault=.user_vault.deferred_cleanup
' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/own-only.json"
mv "$FIXTURE_ROOT/own-only.json" "$FIXTURE_ROOT/result.json"
assert_result $'success\t0'

/usr/bin/jq '.evidence_recovery.original_tombstone.directory="bad\\\\name"' \
  "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/backslash-tombstone.json"
mv "$FIXTURE_ROOT/backslash-tombstone.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

/usr/bin/jq '.evidence_recovery.original_tombstone.directory=("bad"+([10]|implode)+"name")' \
  "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/control-tombstone.json"
mv "$FIXTURE_ROOT/control-tombstone.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

write_result success '[]' "[\"$USER_COMMIT\"]" complete complete true ""
/usr/bin/jq --arg evidence "$EVIDENCE_COMMIT" '.agents_vault.commit_status="complete" | .evidence_finalization_commit=$evidence | .next_action=null' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/invalid.json"
mv "$FIXTURE_ROOT/invalid.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

write_result success "[\"$AGENTS_COMMIT\"]" "[\"$USER_COMMIT\"]" complete complete true ""
/usr/bin/jq --arg evidence "$EVIDENCE_COMMIT" '.evidence_finalization_commit=$evidence | .next_action=null | .agents_vault.commit_hashes=["fake"]' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/fake-oid.json"
mv "$FIXTURE_ROOT/fake-oid.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

write_result success "[\"$AGENTS_COMMIT\"]" "[\"$USER_COMMIT\"]" complete complete true ""
/usr/bin/jq --arg evidence "$EVIDENCE_COMMIT" '.evidence_finalization_commit=$evidence | .next_action=null | .unexpected="forbidden"' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/extra-key.json"
mv "$FIXTURE_ROOT/extra-key.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

printf '{"outcome":"partial_publication"}\n' > "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'
assert_result $'process_error\t42' 42

echo "runner semantic interpretation: 22/22 passed"
