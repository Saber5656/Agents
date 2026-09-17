# 作業方針

[共通ルール](../COMMON-AGENTS.md) を前提に、達成することを定める。手段・順序・担当は目的に合わせて選ぶ。

| 文書 | 内容 | 抽出元の policy |
|---|---|---|
| [協働](collaboration.md) | 必要な専門性と成果の統合 | AI-Organization.md |
| [作業状況](work-tracking.md) | 依頼・進捗・依存・履歴 | Dispatcher-IO-Contract.md |
| [引き継ぎと完了](handoff-and-completion.md) | 要件共有・検証・成果提供 | Gate-IO-Contract.md |
| [知識と記録](knowledge-and-context.md) | 正本・原記録・索引・環境知識 | Task-File-Conventions.md |
| [モバイルSDK互換性](mobile-sdk-compatibility.md) | Expo Goの端末・依存・配信SDK照合と実機受け入れ | 2026-09-08の利用者指示・互換性エラーの再発 |

協働・作業状況・引き継ぎと完了・知識と記録の出典は Saihai の `organization/policies/` にある4文書。固有の受付、承認、状態遷移、コマンド、モデル固定を除き、要件として再構成した。旧文書との矛盾は現在の共通ルールに合わせ、レビューを実施し、Vault に取得可能な全コンテキストを保存する。

[ロール一覧](../roles/README.md) は専門性の選択に使える。全ロールを読み込んだり別プロセスとして起動したりする前提はない。
