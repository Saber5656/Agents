# App Store Screenshots

既存ストア画像の取得・制作・登録・照合を、CLI中心で繰り返せるようにするスキル。

呼び出し例:

```text
/app-store-screenshots 日本語の既存ストア画像を参考に3枚作り直して。登録はまだ。
/app-store-screenshots 指定したバージョンのja-JP画像をこの3枚に差し替えて。
```

取得、backup、制作、preview、対象を絞った登録、処理状態とchecksumの読み戻しまでを扱う。
画像の見た目の確認と、ファイルの機械的な一致確認は分けて記録する。
A/Bテストとヘッダ画像は依頼された場合だけ扱い、用途と対象resourceを確認する。

- [スキル本体](SKILL.md)
- [CLI手順・照合・復元](references/cli-workflow.md)
- [一次資料・確認範囲](references/sources.md)
- [評価ケース](evals/evals.json)

手順と評価ケースを検証したスキル。実サービスへの登録成功を保証するものではなく、
実行時に導入CLIの種類・バージョン・認証・Appleの現行仕様を確認する。
