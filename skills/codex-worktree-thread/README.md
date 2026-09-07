# Codex Worktree Thread

> Codex Appのworktree付きthread分岐を、fork/createの判断からtitle・初回promptまで固定するスキル。

## What It Does

- 既定では `fork_thread + worktree` で親チャットの文脈を引き継ぐ。
- 「文脈なし」と明示された場合だけ `create_thread + existing project + worktree` を使う。
- repo作業で `projectless` を使わない。
- tmuxや外部ターミナル起動をCodex thread分岐の代替にしない。
- 作成後にthread/pending ID、title、issue、branch/worktree対応を表で返す。

## Quick Prompts

```text
issue #1, #2, #3 をworktreeで分岐してチャットセッションを作って
```

```text
親チャットの文脈を引き継がずに、issue #4 用のworktree threadを作って
```

```text
tmuxが混ざらないように、Codex Appのforkで作業分岐して
```

## Default Policy

| Request | Tool |
|---|---|
| 普通のworktree分岐 | `fork_thread + worktree` |
| 親文脈なし | `create_thread + existing project + worktree` |
| repo作業のprojectless | 使わない |
| tmux | 使わない |

See [SKILL.md](SKILL.md) for full workflow details.
