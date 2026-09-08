# 依頼・引き継ぎ・完了

- 依頼の意図、成果物、完了条件、範囲、制約、仮定、不足情報を理解できるようにする。
- 作業は独立した目的と完了条件でまとめ、依存や利用者の判断が必要な部分を明確にする。
- 引き継ぎには目的、現在の成果、対象、必要な根拠、残作業、注意点を含める。形式と分量は相手の作業に合わせる。
- 担当者の宣言と実際の実行を区別し、検証コマンド・結果・成果への参照で確認できるようにする。
- 成果物と受け入れ条件を照合し、レビュー指摘を修正して元指摘と影響範囲を確認する。
- Git の変更は既存作業と区別して意味単位で保存し、push 前に個人パスと秘密値を検査する。
- 実装、検証、commit、push、PR、merge、配布、利用確認は実際に到達した状態を報告する。必要な公開範囲は依頼に合わせる。
- Vault へ全コンテキストを保存し、記録と成果物への参照を返す。未完了・未検証・取得不能な情報を明示する。
- 最終応答では結論を先に、成果・根拠・制限を利用者に理解できる言葉で伝える。

## プロジェクト固有の delivery profile

アプリケーションやデータを扱う作業では、リポジトリの任意の
[delivery profile](../docs/delivery-profiles.md) を索引として解決する。profile が
ない場合は README、package scripts、CI workflow、project config に実在する情報だけを
再利用し、一般的な build、起動、migration、deploy コマンドを推測してはならない。
profile または既存情報が必要なコマンド・対象・read-back 方法を欠く場合は、その段階を
`incomplete` と記録し、実行できた段階だけを報告する。

完了の状態は別々に記録する。`packaged` は成果物の identity と digest を確認した
状態、`deployed` は指定された test target への変更と endpoint の実 read-back が
成功した状態、`migrated` は指定 data target の migration 結果を read-back した状態、
`recovered` は失敗した deploy または migration の bounded rollback/recovery 後に
以前の usable state を確認した状態、`usable` は利用者が見る health または主要動作を
実際に確認した状態を表す。一つの状態を別の状態の証拠として流用しない。

明示的な deploy または migration の依頼がない限り、profile の optional な
`deploy`/`migration` 段階は実行しない。その場合も no-deployment の証跡として release
や tag が作られていないこと、関連する disposable database が変更されていないことを
対象だけの read-only Git/DB snapshot で確認する。無関係な database を読む必要はない。
provider の成功文字列、metadata、HTTP
status、mock のみでは `deployed`、`migrated`、`usable` を報告しない。handoff には
source/effective revision、実行した command、target、期待値と実測値、artifact または
read-back の場所、未検証段階を含める。

handoff は選択された作業単位だけでなく、元の全要件、最新revision、依存、未完了の
acceptanceを含める。完了判定はTaskStoreのverified acceptanceとcompletion evidenceに
加え、要件の依存が解決していることを確認する。別課題のlocal-only follow-upは元作業の
完了条件へ追加せず、Issue化や外部通知を行わない。

- ローカルタスクを実行状態の正本とし、担当・依存・作業単位・Issue/PRリンク・個別受入証拠を保持する。Issue化状態は実行状態と独立させる。
- 新しい別課題は担当者がローカル登録し、別バッチのエージェントがIssue化する。元作業の不具合・採用済みレビュー指摘は担当範囲で自動修正する。
- 通常の開発はマージ・正本main同期・対応チャット整理まで進める。利用者が明示的に狭めた範囲を優先し、別課題のIssue化待ちで止めない。
- 会話/Appを閉じても保存した状態から継続・復旧する。処理単位のタイムアウトや利用枠待ちをタスク全体の放棄へ変換しない。
