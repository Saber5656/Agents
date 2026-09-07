# Skill Updater

既存スキルを修正・改善するときの専用スキル。

## What It Does

- 既存スキルの不満点、改善方針、修正後の出力イメージを先に擦り合わせる
- `SKILL.md` などを必要最小限で修正する
- 通常の変更はreview/承認待ちにせず、focused validationとfeature unit単位のintegrated full validationを行う
- permission expansion、authentication secret、data-loss riskだけは指定providerによる一度の限定reviewを行い、修正後のbot reviewを起動しない
- 修正後に eval / benchmark を必ず実施する
- 結果を設定済みの task/evidence vault または親taskへhandoffする

## Triggers

- 「このスキルを直したい」
- 「既存スキルを改善したい」
- 「このスキルの出力が不満」
- 「SKILL.md を更新して」
- 「トリガーを調整したい」

See [SKILL.md](SKILL.md) for full workflow.
