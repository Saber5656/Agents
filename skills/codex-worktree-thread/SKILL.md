---
name: codex-worktree-thread
description: >
  Codex Appでrepo作業用のworktree付きチャットセッションを作成または分岐するときに使う。
  ユーザーが「worktreeで作業分岐」「issueごとにチャットセッションを作成」
  「親チャットの文脈を引き継がずに分岐」「forkで作って」「tmuxが混ざらないように」
  「別セッションで並行作業」などと言ったら必ず使う。既定は `fork_thread + worktree`
  で親文脈を引き継ぐ。ユーザーが「文脈なし」「親チャットを引き継がない」と明示した場合だけ
  `create_thread + existing project + worktree` を使う。repo作業で `projectless` や tmux 起動へ
  フォールバックしてはいけない。
user-invocable: true
allowed-tools: Read, Grep, Bash, tool_search, codex_app.list_projects, codex_app.list_threads, codex_app.create_thread, codex_app.fork_thread, codex_app.read_thread, codex_app.send_message_to_thread, codex_app.set_thread_title
category: Operation
created: 2026-06-29
status: active
purpose: Codex Appのthread/worktree分岐を一定の手順に固定し、projectless化やtmux混入による作業分岐のブレを防ぐ
argument-hint: "[issue番号/分岐したい作業/文脈継承の有無]"
---

# Codex Worktree Thread

このスキルは、Codex App上でrepo作業をissueやタスクごとにworktree付きチャットへ分岐するための運用ゲートである。
目的は、毎回同じUI配置・同じ文脈継承ルール・同じ初回プロンプトで分岐し、tmuxやprojectless threadによる混乱を避けること。

## Activation

- ユーザーがissueごとの並行作業チャットを作りたいとき。
- ユーザーがworktree付きのCodex threadを作りたいとき。
- ユーザーが親チャットの文脈を引き継ぐかどうかを指定して、作業セッションを分けたいとき。
- ユーザーがtmux混入、別Project化、thread/worktree対応のズレを避けたいとき。
- 既に作られたthread/worktreeの挙動差を調査し、今後の標準手順を固定したいとき。

## Non-goals

- このスキルはtmux sessionを作成しない。
- このスキルはGitHub issueの実装を直接行わない。
- このスキルはPR作成、commit、pushを行わない。
- このスキルはrepo作業用threadを `projectless` targetで作成しない。
- このスキルはCodex App外のターミナルプロセスを、Codex threadの代替として起動しない。

## Core Decision Table

| User intent | Tool pattern | Context | UI placement | Notes |
|---|---|---|---|---|
| worktreeで作業分岐、issueごとの並行作業 | `fork_thread` with `environment: { type: "worktree" }` | 親threadの完了済み履歴を継承 | 同じProject配下 | 既定。親のOSS方針、Vault方針、セキュリティ判断を共有したいときに最も安定 |
| 親文脈なしで同じrepo/Projectに分岐 | `create_thread` with existing `projectId` and `environment: { type: "worktree" }` | 継承なし | 同じProject配下 | ユーザーが「文脈なし」「親チャットを引き継がない」と明示したときだけ |
| repo作業ではない一般タスクを新規threadにする | `create_thread` with `target: { type: "projectless" }` | 継承なし | 独立Project風に見える | repo worktree分岐では使わない |
| 同じcheckout内で会話だけ分ける | `fork_thread` with same-directory or no environment | 継承あり | 同じProject配下 | 並行実装には不向き。worktreeが不要と明示されたときだけ |
| tmuxで別作業者を起動 | 使わない | Codex UIに紐づかない | UIと対応しない | このスキルでは禁止。thread/worktree対応が崩れるため |

## Invariants

1. Codex App thread操作は、まず `tool_search` で `create_thread`、`fork_thread`、`list_projects`、`list_threads`、`send_message_to_thread`、`set_thread_title` を探してから使う。
2. repo作業の分岐では `projectless` を使わない。必ず既存Project配下に作る。
3. 親文脈を引き継ぐか不明な場合は、`fork_thread + worktree` を既定にする。
4. 親文脈なしは、ユーザーが明示した場合だけ `create_thread + existing project + worktree` にする。
5. `tmux`、手動ターミナル、外部プロセス起動をCodex thread分岐の代替にしない。
6. `fork_thread` はsource threadの完了済み履歴だけをコピーする。source threadの現在実行中turnや未完了応答はコピーされない。
7. `fork_thread + worktree` が `pendingWorktreeId` だけを返す場合は、thread IDを捏造しない。必要なら `list_threads` で後から確認する。
8. `startingState` はツールスキーマ上は使えるが、環境差で失敗する場合がある。失敗したらtmuxやprojectlessへ逃げず、plain worktree fork/createを一度だけ試し、子threadの初回プロンプトでfetch/branch作成を指示する。
9. 複数issueを並行作成する場合も、issueごとのtitle、branch、初回prompt、pending/thread IDを表で記録する。
10. 作成後にユーザーへ、どのthreadがどのworktree/branch/issueに対応するかを必ず報告する。

## Hard Safety Rules

| Rule | Reason |
|---|---|
| repo worktree分岐で `projectless` を使わない | `projectless` は別Project風に見え、ユーザーが期待するProject groupingとズレる |
| `projectless` を使う前にrepo作業ではないことを確認する | repo taskで `projectless` を選ぶと、issue/thread/worktreeの対応が追いにくくなる |
| `projectless` をfallbackにしない | `fork_thread` や `create_thread` の失敗を `projectless` で隠すと、後から原因を追えない |
| `tmux` でCodex作業者を起動しない | `tmux` はCodex App sidebarのthread/worktree対応に現れず、進捗とcheckoutの対応が崩れる |
| `tmux` をfallbackにしない | `tmux` へ逃げると、スキルの目的であるCodex App thread管理が失われる |
| 既存の `tmux` sessionをこのスキルで再利用しない | `tmux` 内のcwdやbranchはCodex Appのthread metadataと一致する保証がない |

## Workflow

### 1. User intentを判定する

次の順に判定する。

| 判定 | 条件 | 選ぶモード |
|---|---|---|
| `fresh-context` | 「文脈なし」「親チャットを引き継がない」「独立した新規文脈」と明示 | `create_thread + existing project + worktree` |
| `inherited-context` | 「fork」「引き継ぐ」「同じ流れで」「issueごとに並行」または文脈指定なし | `fork_thread + worktree` |
| `not-repo-work` | repoやworktreeではない一般的な背景thread | 必要なら `create_thread + projectless` |
| `ambiguous-project` | Project候補が複数あり、repo対応が判断できない | ユーザーへ確認 |

### 2. Codex App toolsをロードする

`tool_search` で少なくとも次を探す。

```text
codex_app fork_thread create_thread list_projects list_threads send_message_to_thread set_thread_title worktree project
```

該当ツールが使えない場合は、tmuxやshell起動で代替せず、ツール不足として停止する。

### 3. inherited-context mode: fork_threadを使う

既定のrepo作業分岐では、次を使う。

```json
{
  "environment": { "type": "worktree" }
}
```

source threadを明示された場合だけ `threadId` を指定する。通常は省略してcalling threadをforkする。

実行後:

- `threadId` が返った場合は、必要に応じて `set_thread_title` と `send_message_to_thread` を実行する。
- `pendingWorktreeId` だけが返った場合は、pending IDを記録し、`list_threads` で生成完了を確認できる場合だけtitle設定やfollow-up送信に進む。
- pending状態のまま完了できない場合は、pending IDと次に必要な確認をユーザーに返す。

### 4. fresh-context mode: create_threadを使う

親文脈なしで同じrepo/Projectに作る場合:

1. `list_projects` を呼び、現在のrepo/path/nameに一致するProjectを選ぶ。
2. Projectが一意に決まらない場合は、候補を出してユーザーに確認する。
3. `create_thread` を次の形で呼ぶ。

```json
{
  "prompt": "<full initial prompt>",
  "target": {
    "type": "project",
    "projectId": "<project id from list_projects>",
    "environment": { "type": "worktree" }
  }
}
```

repo作業で以下を使ってはいけない。

```json
{
  "target": { "type": "projectless" }
}
```

### 5. Thread titleを固定する

titleは、Project sidebarで一目で判別できる形にする。

推奨形式:

```text
<repo/product> issue #<number> <short task name>
```

例:

```text
Vynema issue #1 MVP requirements
Vynema issue #2 architecture choices
Vynema issue #3 v2 docs cleanup
```

title設定がpendingのため未実行なら、未設定であることを結果に残す。

### 6. Child initial promptを作る

forkの場合でも、重要な制約は親文脈だけに頼らず、follow-up promptに再掲する。
fresh-contextの場合は、親文脈がないため必要情報をより厚く渡す。

必須項目:

- repo名、現在の目的、対象issue/タスク。
- AGENTS.mdやproject guidanceを読むこと。
- worktree/branch方針。
- main/origin/mainの最新化方針。
- Vault記録が必要なrepoでは、task recordを作ること。
- 実装範囲と非対象範囲。
- test/verification方針。
- commit/push/PR可否。未承認なら禁止を明記する。
- security-sensitive、OSS、release、publicationなどの特別ルールがあれば明記する。
- 結果報告で返してほしい項目。

テンプレート:

```markdown
このthreadは `<repo>` の `<task>` 用worktree sessionです。

必ず最初に `AGENTS.md` とrepo guidanceを読んでください。
対象: <issue number / issue URL / task summary>
想定branch: `<branch-name>`
開始点: latest `origin/main` unless impossible

作業ルール:
- tmuxや外部ターミナルで別作業者を起動しない。
- Vault記録が必要なrepoでは、作業開始時と完了時に記録する。
- commit/push/PRは、ユーザーが明示するまで行わない。
- scope外のissueや無関係なrefactorへ広げない。

まず確認して報告すること:
1. 現在のcwd/worktree
2. branch
3. 対象issueの理解
4. 実装または調査の最初の計画
```

### 7. Resultを報告する

必ず次の表を返す。

| Task | Mode | Tool | Thread/Pending ID | Title | Branch/worktree | Status |
|---|---|---|---|---|---|---|
| issue #1 | inherited-context | `fork_thread + worktree` | `...` | `...` | pending / path if known | created / queued / needs follow-up |

`create_thread` を成功させた場合、Codex Appが要求する `::created-thread{...}` directiveが必要な環境では、最終応答に含める。

## Failure Handling

| Failure | Response |
|---|---|
| Codex App thread toolsが見つからない | `tool_search` で探し直し、それでも無ければ停止。tmuxで代替しない |
| Project候補が複数 | `A/B/C` でProject選択を確認する |
| `create_thread` が `projectless` しか作れない状況 | repo作業では停止し、既存Projectの選択を求める |
| `fork_thread` が `pendingWorktreeId` だけ返す | pending IDを報告し、thread IDを捏造しない |
| `startingState` 付き呼び出しが失敗 | plain worktreeで一度だけ再試行し、branch作成はchild promptへ移す |
| ユーザーがtmux起動を求める | Codex App thread/worktree対応が崩れるリスクを説明し、Codex thread toolでの代替案を提示する |
| 子threadへfollow-up送信できない | 作成済みID、未送信prompt、ユーザーが手動投入できる文面を返す |

## Output Style

- 日本語で、短くても対応表は省略しない。
- 曖昧な場合は質問する。ただし、親文脈あり/なしが曖昧なだけなら既定の `fork_thread + worktree` で進める。
- 複数issueを作る場合は、issueごとに1行で対応関係が分かるようにする。
- 「tmuxは使っていない」「projectlessは使っていない」を結果または検証に含める。

## Related Skills

- `git-workspace-prep`: 既にあるworktree/branchを手元で準備する必要があるときに使う。ただしCodex App thread作成の代替ではない。
- `github:github`: issue本文やPR情報の確認が必要なときに使う。
- `pr`: 子threadで作業完了後、ユーザーがPR作成を明示したときに使う。
- `save`: 有用なthread分岐運用の記録を後から保存したいときに使う。
