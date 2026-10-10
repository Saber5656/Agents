# 出典と確認範囲

確認日: 2026-10-11。

## 入力となった知識

ユーザーが提供した2枚の画像には、既存のApp Store Connect接続を使い、ストア画像の
取得・登録・確認・削除と実験作成をCLI中心に進める例が写っていた。
不足属性は読み取りAPI、旧画像の保存はApple画像サーバーで補い、登録後に読み戻して照合し、
最後に人間が見た目を確認する点を手順へ反映した。

投稿URL、使用CLIの配布元・バージョン、ヘッダ画像のresource種別は画像だけでは確認できない。
画像原本と転記は既存のprivate Vaultに保存し、公開スキルへ転載していない。

## CLIの一次資料

例は以下のmaintainerリポジトリを参照した。Appleが提供する公式CLIではない。
確認commit: `7af4b877a881b4463c15eb79e3affb5742b233f6`。

- [Screenshots command](https://github.com/rorkai/App-Store-Connect-CLI/blob/7af4b877a881b4463c15eb79e3affb5742b233f6/commands/screenshots.mdx)
- [Authentication](https://github.com/rorkai/App-Store-Connect-CLI/blob/7af4b877a881b4463c15eb79e3affb5742b233f6/authentication.mdx)
- [Product page experiments](https://github.com/rorkai/App-Store-Connect-CLI/blob/7af4b877a881b4463c15eb79e3affb5742b233f6/internal/cli/productpages/experiments.go)
- [App Clip header images](https://github.com/rorkai/App-Store-Connect-CLI/blob/7af4b877a881b4463c15eb79e3affb5742b233f6/internal/cli/appclips/header_images.go)
- [同名の別CLI](https://github.com/tddworks/asc-cli): 実行名だけで構文の互換性を判断しない。

実行時は導入版のhelpを優先する。対応機能が増えても、GET補完の必要性を固定しない。
特に画像寸法と提出要件はCLI文書よりAppleの現行規約を優先する。

## Appleの一次資料

- [App Screenshots](https://developer.apple.com/documentation/appstoreconnectapi/app-screenshots): 画像とsetのresource。
- [Uploading Assets](https://developer.apple.com/documentation/appstoreconnectapi/uploading-assets-to-app-store-connect):
  予約・送信・commit・非同期処理の確認、元ファイルのMD5照合。
- [Read app screenshot information](https://developer.apple.com/documentation/appstoreconnectapi/get-v1-appscreenshots-_id_):
  `sourceFileChecksum`、`imageAsset`、`assetDeliveryState`等の取得。
- [ImageAsset](https://developer.apple.com/documentation/appstoreconnectapi/imageasset): 配信画像の属性。
- [Screenshot specifications](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications):
  提出先ごとの必要画像・寸法・形式・透明度の制約。実行時に再確認する。
- [Product Page Optimization](https://developer.apple.com/help/app-store-connect/create-product-page-optimization-tests/overview-of-product-page-optimization/):
  実験の目的と通常ページとの関係。

## 検証の限界

スキル作成時はCLIソースとApple資料を照合した。作成環境に`asc`は導入されておらず、
実アカウントへの接続・画像登録・実験開始・公開反映は実行していない。
評価は架空の状態に対する手順判断であり、実APIの成功や画像品質の保証とは分けて扱う。
