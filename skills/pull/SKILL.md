---
name: pull
description: >
  Git pull workflow for all managed local repositories. Use this skill when the user says
  "プルして", "pullして", "全リポジトリをプルして", "管理 repo を最新化して",
  "fetch and merge all repos", or asks to fetch and merge the currently managed local
  repositories. This skill treats "プルして" as fetch + safe merge for every repository
  listed in references/managed-repositories.md. It must not push, force, reset hard,
  delete files, or auto-resolve conflicts.
user-invocable: true
allowed-tools: Bash, Read, Grep
category: Dev
created: 2026-06-05
updated: 2026-06-15
status: active
purpose: 管理対象ローカル Git リポジトリ全体を安全に fetch + merge する
argument-hint: "[--dry-run | --execute]"
---

# Git Pull Managed Repositories

`pull` は、管理対象ローカル Git リポジトリを一括で最新化するための道具スキルである。
ユーザーの短縮依頼「プルして」は、全対象 repo の `fetch` と安全な `merge` を意味する。

## Task Context Precondition

人間起点で呼ばれた場合でも、task record と実行 scope を確認する。
実行 scope と決定済みの policy を含む caller-supplied Saihai task context が、呼び出し元から明示的に渡されていなければならない。
外部フロー上の起票、承認、routing、role 選択はこのスキル内で決めず、受け取った context の検証と pull 実行だけを行う。
task context がない場合は、git 操作をせず `task_scope_missing` として停止する。

次の境界を常に守る。

| Operation | Policy |
|---|---|
| fetch | allowed |
| merge | allowed only by safe policy below |
| push | forbidden |
| force push | forbidden |
| `git reset --hard` | forbidden |
| delete / clean | forbidden |
| conflict auto-resolution | forbidden |

## When I Activate

- ユーザーが「プルして」と入力したとき。
- ユーザーが「全リポジトリをプルして」「管理 repo を最新化して」と言ったとき。
- ユーザーが managed local repositories の fetch + merge を依頼したとき。
- task context または Publication Manifest から、管理対象 repo の pull 実行を明示されたとき。

## What I Do

1. 共有タスク vault の managed repositories policy を正本として扱う。
2. 実行時は `references/managed-repositories.local.md` があればそれを優先し、なければ公開用雛形の `references/managed-repositories.md` から対象 repo を読む。
3. 各 repo の存在、current branch、upstream、dirty state を確認する。
4. `git fetch --all --prune` を実行する。
5. fetch 後の ahead / behind を確認する。
6. safe merge policy に従って merge または block を決める。
7. 結果を task record に記録する。

## Safe Merge Policy

| Repo state | Action |
|---|---|
| No upstream | fallback to `<remote>/<default_branch>` from the managed repo table; block only if that ref is unavailable |
| No remote updates | fetch only, no merge |
| Behind and clean | merge upstream |
| Diverged and clean | merge upstream with `--no-edit`; abort and block if conflict occurs |
| Dirty and no remote updates | fetch only, no merge |
| Dirty and remote updates exist | block merge with `dirty_worktree` |
| Unmerged paths already exist | block before fetch/merge |
| Merge conflict | abort merge and block with `merge_conflict` |

Dirty worktree means tracked modifications, staged changes, untracked files, or unresolved merge state.
This conservative rule prevents the shorthand command from mixing user work with remote changes.

## Vault iCloud + External Gitdir Handling

Vault repositories may use a different operational model from ordinary source repositories.
Vault worktrees may live under a synced folder, while Git metadata lives outside that synced folder in an external gitdir.
The Vault root `.git` should be a small `gitdir:` pointer file, not a real `.git/` directory.

This means sync tooling can sync file content across devices while each device still has its own Git HEAD, index, refs, and object database.
A non-writer device can therefore appear dirty after file sync even when its files already match `origin/main`.

For `repo_kind: vault`, treat dirty state as one of two cases before recommending merge or commit-first recovery:

| Case | Action |
|---|---|
| Working tree content matches upstream after fetch | Treat as metadata lag. Align HEAD/index with `git reset --mixed <upstream>` only. Do not merge, commit, delete, or use `git reset --hard`. |
| Working tree content differs from upstream | Treat as real local work. Preserve the diff and follow the normal dirty worktree policy. |

Safe manual check:

```bash
git fetch origin
git diff --quiet <upstream> -- . && git reset --mixed <upstream>
git status --short --branch
```

Use `origin/main` as `<upstream>` for managed Vault repositories unless the managed repository table says otherwise.
Choose one canonical periodic commit / push writer for Vault snapshots; other devices should align metadata after file sync instead of creating duplicate snapshot commits.

Current script behavior may still block dirty Vault repositories when remote updates exist.
If the script reports `dirty_worktree` for a Vault repo, inspect whether it is a metadata-lag candidate before handing the repo to `merge`.

## Command

Dry run:

```bash
python3 skills/pull/scripts/pull_managed_repos.py --dry-run
```

Execute:

```bash
python3 skills/pull/scripts/pull_managed_repos.py --execute
```

JSON output:

```bash
python3 skills/pull/scripts/pull_managed_repos.py --execute --json
```

## Result Fields

| Field | Meaning |
|---|---|
| `repo` | managed repository name |
| `path` | local repository root |
| `fetch_status` | `success`, `skipped`, or `failed` |
| `merge_status` | `merged`, `not_needed`, `blocked`, `dry_run`, or `failed` |
| `ahead` / `behind` | post-fetch relationship to upstream |
| `reason` | block or failure reason |

## Review Requirements

- If task policy requires review, reviewer selection is supplied by task context.
- This skill records repo inventory, no-push boundary, command output, and Vault/task evidence.
- Completion record must include Git Publication Result or publication-not-required reason when a publication flow exists.

## Sandboxing Compatibility

**Works without sandboxing:** Yes
**Works with sandboxing:** fetch / merge may require approval

- Filesystem: writes Git refs, FETCH_HEAD, merge commits, and task records.
- Network: fetch requires remote access.
- Configuration: each repo must already have remotes and upstreams.

## Related Files

- `references/managed-repositories.md`
- `references/managed-repositories.local.md` (ignored local override)
- `scripts/pull_managed_repos.py`
- `tests/test_pull_managed_repos.py`
- Vault policy: `03-Contexts/Policies/Managed-Local-Repositories.md`
