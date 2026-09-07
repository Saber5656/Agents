# ロール要件

役割の目的・責務・成果・品質観点を参照するための文書集。実行順序、固定チーム編成、モデルの割り当ては定義しない。主担当は必要な役割を選び、自分で兼ねるか委譲するかを判断する。

共通の運用は [COMMON-AGENTS.md](../COMMON-AGENTS.md)、作業方針は [policies](../policies/README.md) を参照する。各ロールの作業にもレビュー、適切な検証、全コンテキストの Vault 保存を適用する。

ロールの参照は、自動起動や常駐監視の設定ではない。文体ロールも選択した場面だけに適用する。

## 一覧

| ロール | 目的 | 抽出上の扱い |
|---|---|---|
| [business-director](business-director.md) | 事業判断の統括 | 責務を抽出 |
| [business-information-strategy](business-information-strategy.md) | 情報戦略 | 責務を抽出 |
| [business-legal-reviewer](business-legal-reviewer.md) | 法務観点レビュー | 責務を抽出 |
| [business-marketing-director](business-marketing-director.md) | 事業統括の旧名称 | 旧名称。business-director を参照 |
| [business-partnership-manager](business-partnership-manager.md) | 提携調整 | 責務を抽出 |
| [business-strategy](business-strategy.md) | 事業戦略 | 責務を抽出 |
| [contents-director](contents-director.md) | コンテンツ統括 | 責務を抽出 |
| [contents-formatter](contents-formatter.md) | 文章整形 | 責務を抽出 |
| [contents-quality-manager](contents-quality-manager.md) | コンテンツ品質 | 責務を抽出 |
| [contents-researcher](contents-researcher.md) | 調査 | 責務を抽出 |
| [gate-prompt-formatter](gate-prompt-formatter.md) | 依頼整理 | 制御手順から目的を抽出 |
| [gate-response-humanizer](gate-response-humanizer.md) | 応答の文体調整 | 責務を抽出 |
| [gate-task-assessor](gate-task-assessor.md) | 作業充足確認 | 旧互換定義から確認目的を抽出 |
| [gate-task-creator](gate-task-creator.md) | 作業記録の構成 | 制御手順から目的を抽出 |
| [gate-task-evaluator](gate-task-evaluator.md) | 成果物評価 | 制御手順から目的を抽出 |
| [gate-task-guardian](gate-task-guardian.md) | 完了報告の整合確認 | 旧互換定義から確認目的を抽出 |
| [git-publisher](git-publisher.md) | Git 成果の提供 | 制御手順から目的を抽出 |
| [infra-director](infra-director.md) | 知識基盤の統括 | 責務を抽出 |
| [infra-local-qa](infra-local-qa.md) | ローカル知識の品質 | 責務を抽出 |
| [infra-task-dispatcher](infra-task-dispatcher.md) | 作業と記録の健全性確認 | 制御手順から目的を抽出 |
| [infra-team-bootstrap](infra-team-bootstrap.md) | 作業環境の準備 | 制御手順から目的を抽出 |
| [teams-developer](teams-developer.md) | 実装の基本要件 | 旧実装メモから要件を抽出 |
| [teams-project-manager](teams-project-manager.md) | 作業全体の調整 | 制御手順から目的を抽出 |
| [tech-architect](tech-architect.md) | アーキテクチャ | 責務を抽出 |
| [tech-backend](tech-backend.md) | バックエンド | 責務を抽出 |
| [tech-data-structure](tech-data-structure.md) | データ設計 | 責務を抽出 |
| [tech-debugger](tech-debugger.md) | 不具合調査 | 責務を抽出 |
| [tech-designer](tech-designer.md) | UI・UX 設計 | 責務を抽出 |
| [tech-devopssec](tech-devopssec.md) | 開発・配布基盤の安全性 | 責務を抽出 |
| [tech-director](tech-director.md) | 技術作業の統括 | 責務を抽出 |
| [tech-docs](tech-docs.md) | 技術文書 | 責務を抽出 |
| [tech-frontend](tech-frontend.md) | フロントエンド | 責務を抽出 |
| [tech-infrastructure](tech-infrastructure.md) | 技術環境 | 責務を抽出 |
| [tech-lead](tech-lead.md) | 技術判断の統合 | 責務を抽出 |
| [tech-mobile](tech-mobile.md) | モバイル | 責務を抽出 |
| [tech-performance](tech-performance.md) | 性能 | 責務を抽出 |
| [tech-qa](tech-qa.md) | 品質評価 | 責務を抽出 |
| [tech-reviewer](tech-reviewer.md) | 統合レビュー | 責務を抽出 |
| [tech-security](tech-security.md) | セキュリティ | 責務を抽出 |
| [tech-tester](tech-tester.md) | テスト | 責務を抽出 |

## 出典と整理判断

出典は Saihai の `organization/roles/<role>/skill.md`。40件を同名の Markdown に対応付けた。UI 設計とセキュリティは各ロールの参照資料の観点も要約した。本文は転載ではなく要件の再記述である。

廃止済み・互換用だった役割は、残っている目的を参照可能にしたもので、過去の実行方式の復活を意味しない。技術判断の特定モデル固定、ファイル数による自動委譲、定期実行間隔、報告形式の固定は採用しない。現在のレビュー必須・TDD・全コンテキスト保存を優先する。
