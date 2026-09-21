# 実行評価の再現と範囲

`evals.json` の6ケースを、同じモデル・reasoning effort・入力fixtureで比較する。
新規スキルのbaselineは `without_skill`、比較対象は `with_skill`。
baselineへスキル本文や採点基準を渡さない。期待結果は採点側だけが読む。

1. `skill-creator/SKILL.md` に従って `skills/.workspace/prepare-3d-print/iteration-N/` を用意する。
2. 各ケース・各条件に独立した実行を使う。消費元は既存ChatGPTログインのCodex CLI。
   今回の要求モデルは `gpt-5.6-luna`、effortは `low`。モデル同一性は要求値と実報告を分ける。
3. `context-fixture.json` と対象promptだけを共通入力にする。
   with_skillにはSKILL.md・参照資料・記録テンプレートを追加する。
   ホストのスキル自動探索と起動指示を無効化し、baselineへの混入を防ぐ。
4. 工程の判断と利用者向け報告を実際に生成する。fixture値は実データの測定結果ではない。
   実行プロンプト・設定・生出力・終了状態・実測時間・報告されたusageを保存する。
   利用不能/timeoutを正解として採点しない。
5. 主担当は各出力を読み、`expectations` の内容を根拠付きでpass/fail採点する。
   キーワードが含まれるだけではpassにせず、矛盾する指示や完了の捏造がないか確認する。
6. `skill-creator/scripts/aggregate_benchmark.py` と `eval-viewer/generate_review.py --static` で
   比較表とレビュー画面を生成する。欠落usageを文字数で補った集計は採用しない。

この評価は工程選択・情報不足時の応答・納品報告のモデル実行評価。
Blenderの形状処理、CAD寸法、画像整合性の視覚判定、サービスAPI、スライス、実機造形の
成功率は測らない。ツールを無効化した試験は、実ツールが利用可能なときの副作用抑制の証明でもない。
これらは対象環境と許可を得た別の受入試験が必要。

1ケース1回/条件は最低限の比較であり、再現性の統計的保証ではない。
ケース間の標準偏差を同じケースの実行揺らぎと呼ばない。
descriptionの専用24件routing最適化、実際のアプリ画面の再読込確認は別試験として区別する。
