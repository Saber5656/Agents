---
name: git-workspace-prep
description: >
  task context の Branch Plan に従って、作業開始前のGitブランチ作成・checkout・worktree準備を行うスキル。
  複数の作業者やプロセスが同一taskで作業する前に、1 task = 1 working branch / worktree を固定したいときに使う。
  main-push-repos.md にある repo でも task worktree を使い、最後に main へ統合する。
  `${DEV_REPO_ROOT}/*` では task-specific git worktree を作成または利用し、publication は PR 作成へ進める。
user-invocable: true
allowed-tools: Bash, Read, Grep
category: Dev
created: 2026-06-02
status: active
purpose: Branch Plan に基づき task 共有 working branch を準備する
argument-hint: "[Branch Plan]"
---

# Git Workspace Prep

`git-workspace-prep` は作業開始前に repository を task 共有 branch または task-specific git worktree へ揃える。
branch 名や repo profile は決めず、task context に記録された `Branch Plan` に従う。

`push/references/main-push-repos.md` に載っている repository は default branch push が許可されるが、これは task worktree 作成の免除ではない。
通常は task-specific worktree branch を作り、publication で main へ統合してから default branch を push する。

`${DEV_REPO_ROOT}/*` 配下の repository は、local task branch と git worktree を作業開始単位にし、publication で branch ごとの PR 作成へ進める。
現在 branch が main 以外でも、Branch Plan の `working_branch` / `worktree_path` と一致しない branch を使い回してはならない。

## Task Context Precondition

task record と `Branch Plan` が必要。
`Branch Plan` を含む caller-supplied Saihai task context が、呼び出し元から明示的に渡されていなければならない。
外部フロー上の起票、承認、routing、role 選択はこのスキル内で決めず、受け取った typed artifact の検証と実行だけを行う。
未起票または Branch Plan 不足の場合は、git 操作を行わず停止する。

## When I Activate

- ✅ task planning layer が `Branch Plan` を作成した直後
- ✅ source repo の default branch 上で作業を始める前
- ✅ 複数の作業者やプロセスが同じ task branch で作業する必要があるとき
- ✅ main-push repo で task worktree を作成または利用するとき
- ❌ branch 名をその場で設計するとき
- ❌ commit / push / PR を行うとき

## Branch Plan

| Field | Required | 内容 |
|---|---:|---|
| `repo_root` | Yes | 対象 repo |
| `repo_kind` | Yes | `source` / `vault` / `skills` / `other` |
| `base_branch` | Yes | 作業元 branch |
| `working_branch` | Yes | task 共有 branch。read-only / emergency / human-approved exception で `branch_action: none` の場合だけ `base_branch` と同じ値を許容 |
| `branch_owner` | Yes | 通常 `task` |
| `shared_by` | Yes | branch を共有する作業者、プロセス、または呼び出し元 |
| `default_branch_push_allowed` | Yes | `main-push-repos.md` 記載 repo などの default branch push 許可 |
| `branch_action` | No | `none` / `checkout_existing` / `create_working_branch` / `checkout_task_worktree` / `create_task_worktree`。未指定時は `working_branch` と `workspace_mode` から推定 |
| `workspace_mode` | No | `default_branch` / `branch_checkout` / `task_worktree`。`${DEV_REPO_ROOT}/*` は `task_worktree` を要求 |
| `worktree_required` | No | managed writable repos では通常 `true` |
| `worktree_path` | When `task_worktree` | task-specific git worktree の path。例: `${TASK_WORKTREE_ROOT}/<repo-name>/<TSK-####-slug>` |
| `current_branch_reuse_policy` | No | `only_if_matches_planned_task_branch_and_worktree` |
| `publication_flow` | No | `merge_to_main_and_push` / `create_pr_from_task_branch` |

## Workflow

1. `repo_root` が Git repository であることを確認する。
2. 現在 branch と dirty state を確認する。
3. `workspace_mode: task_worktree`、`worktree_required: true`、または `repo_root` が managed writable repo に一致する場合は、`working_branch` と `worktree_path` を必須にする。
4. `branch_action: none` は、read-only task、emergency task、または明示的に worktree 不要と記録された例外だけで許可する。
5. task worktree mode では `git worktree list --porcelain` で既存 worktree と branch の対応を確認する。
6. planned `worktree_path` が既に存在し、planned `working_branch` と一致する場合は、その worktree を使用対象として `prep_status: complete` を記録する。
7. planned `working_branch` が未作成の場合は `git worktree add <worktree_path> -b <working_branch> <base_branch>` を実行する。
8. planned `working_branch` が存在し、worktree 未割当の場合は `git worktree add <worktree_path> <working_branch>` を実行する。
9. planned branch が別 worktree で既に使用中、または `worktree_path` が別 task の内容を指す場合は停止する。
10. branch checkout mode では、`working_branch` が存在する場合は checkout し、default/base branch 上で `working_branch` が存在しない場合は `git switch -c <working_branch>` を実行する。
11. branch checkout mode で現在 branch が別の非 default branch の場合は、勝手に切り替えず `branch_mismatch` として停止する。
12. 結果を `Workspace Prep Result` として task record に記録する。

## Safety Rules

| 状況 | 対応 |
|---|---|
| dirty worktree | branch switch が安全な場合だけ許可。conflict risk がある場合は停止 |
| main-push repo on default branch | default branch push は許可されるが、通常は task-specific git worktree を作成または利用する |
| `${DEV_REPO_ROOT}/*` source repo | task-specific git worktree を作成または利用し、任意の現在 branch を使い回さない |
| source / skills repo on default branch, not whitelisted and not `${DEV_REPO_ROOT}/*` | `working_branch` を作成して checkout |
| vault / skills / dotfiles repo with default branch push allowed | task-specific git worktree を作成または利用し、publication で default branch へ統合してから push する |
| branch name missing | 明示的な `branch_action: none` 例外以外では停止 |
| current branch is another task branch in `${DEV_REPO_ROOT}/*` | その branch を使い回さず、planned task worktree を作成または利用する。planned worktree に移れない場合だけ停止 |
| current branch is another task branch outside task worktree mode | 停止し、Branch Plan owner に確認を戻す |
| planned branch already checked out in another worktree | 停止し、Branch Plan owner に確認を戻す |
| worktree path belongs to another task | 停止し、Branch Plan owner に確認を戻す |

どの呼び出し元であっても、`Branch Plan` にない branch を独自作成しない。

## Workspace Prep Result

| Field | Required | 内容 |
|---|---:|---|
| `prep_status` | Yes | `complete` / `not_required` / `blocked` |
| `repo_root` | Yes | 対象 repo |
| `base_branch` | Yes | 作業元 branch |
| `working_branch` | Yes | task 共有 branch |
| `previous_branch` | Yes | 実行前 branch |
| `current_branch` | Yes | 実行後 branch |
| `default_branch_push_allowed` | Yes | default branch push 許可の有無 |
| `branch_action` | Yes | `none` / `checkout_existing` / `create_working_branch` / `checkout_task_worktree` / `create_task_worktree` |
| `workspace_mode` | Yes | `default_branch` / `branch_checkout` / `task_worktree` |
| `worktree_path` | When applicable | 使用または作成した task worktree path |
| `blocked_reason` | When blocked | 停止理由 |

## Sandboxing Compatibility

**Works without sandboxing:** Yes
**Works with sandboxing:** May require git ref write approval

- **Filesystem**: `.git` ref updates
- **Network**: none
- **Configuration**: existing Git repository

## Related Skills

- `push`: push policy and execution
