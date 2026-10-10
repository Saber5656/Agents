---
name: app-store-screenshots
description: >
  App Store Connectの既存ストア画像を取得・参考に新しいスクリーンショットを作り、
  asc CLIを中心に登録・差し替え・読み戻し検証を行う。
  「ストア画像を作り直す」「登録済みスクショをバックアップ」「App Store画像を差し替え」
  「ストア画像のA/Bテストやヘッダ画像を設定」などに使う。
  アプリのUI実装、一般的な画像編集のみ、Google Play、TestFlight配布や審査提出だけには使わない。
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
category: Dev
created: 2026-10-11
status: active
purpose: 既存ストア画像の取得から制作・登録・照合までを再現可能な手順にする
argument-hint: "[対象アプリ・バージョン・言語・画像の作成または差し替え]"
---

# App Store ストア画像

既存の画像と実際のアプリを材料にストア画像を作る。取得・登録・照合はCLI/APIで記録し、
見た目の良し悪しはプレビューで確認する。コマンド例は
[CLI手順](references/cli-workflow.md)、確認元と限界は[出典](references/sources.md)を読む。

## 1. 対象と作業範囲を確定する

会話・プロジェクト設定・読み取り結果から次を解決する。一意に決まる情報は聞き直さない。

- App IDとbundle ID、platform、App Store version ID。
- locale、screenshot display type、画像の枚数と順序。
- 通常の製品ページ、Custom Product Page、実験のtreatment、ヘッダ画像のどれか。
- ローカル制作、アップロード、既存画像の差し替え、実験開始など依頼された到達点。

対象が複数残る場合は変更対象を確認し、回答に依存しない調査・ローカル制作を続ける。
作成だけの依頼ではアップロードしない。差し替えまで依頼済みなら通常の再確認を足さない。
審査提出、ストア公開、実験開始、勝者の適用は、それぞれ会話で許可された範囲に従う。

## 2. CLIと既存認証を確認する

`asc version`、`asc --help`、使用するsubcommandの`--help`を記録する。
`asc`という実行名だけでは配布元を特定できない。同名の別製品にコマンドを混ぜない。
このスキルの例は `rorkai/App-Store-Connect-CLI` 用で、Apple純正CLIではない。

既存のプロファイル・APIキー設定を使い、必要な操作権限と対象アプリへのアクセスを確認する。
未導入や認証切れなら不足を具体的に伝え、本人の操作が必要な部分以外を進める。
401は認証、403は権限、429は利用制限として切り分け、ネットワーク障害・処理待ちと混同しない。
認証を上書きして直そうとしない。
秘密鍵・JWT・署名付きURLを公開成果物、シェル引数の記録、応答へ載せない。

## 3. 既存画像を取得して復元材料を残す

1. 対象のlocaleとsetを列挙し、ページングを最後まで取得する。各listの自動ページングや
   `--paginate`対応を確認し、存在しないflagを一律に付けない。
   新しい画像の枚数から旧画像の総数を推定しない。未取得ページがある間は旧総数は未確定。
2. resource ID、親ページ/set、display type、件数、関係の順序、fileName、fileSize、
   `sourceFileChecksum`、`imageAsset`、`assetDeliveryState`をprivate inventoryへ保存する。
3. CLIの出力に必要な属性がなければ、Apple公式APIの該当resourceへGETする。
   `asc api GET`が使える版なら既存認証を再利用する。GET補完を別の書き込み操作へ広げない。
4. CLIのdownload、またはAPIが返したApple画像サーバーのURLから旧画像を保存する。
   `imageAsset`のURLがテンプレートなら、返却寸法と公式仕様に沿って展開する。
   URLを推測せず、画像サーバーへAPI用Authorization/JWTを送らない。
5. backupの画像を開けるか、寸法・枚数・順序と対応表が揃うか確認する。元の制作ファイルも
   あれば保存する。CDN画像は変換済みの場合があるため、元のアップロードファイルと同一と
   決めつけず、復元後に保てる内容・画質・寸法と不足を記録する。

削除や`--replace`の前に、対象set全体を戻せる材料と復元手順を用意する。
取得失敗や欠落で復元できないときは、その破壊的な操作だけ止め、制作・調査を続ける。
backup取得の403は、APIの操作権限とCDN URLの期限・取得先を分けて調べる。

## 4. 新画像を制作する

旧画像の訴求、対象ユーザー、実装済み機能、ブランド、文言、順序を整理してから作る。
実アプリの画面と参考素材を区別し、未実装機能や架空の画面を実際のUIとして見せない。

- 画像生成・既存画像の編集には利用可能な `imagegen` スキルと対応ツールを使う。
  ブランド素材、実画面、文字、装飾を分け、再編集できる材料を残す。
- アプリ自体の新規UI設計・再設計が含まれる場合は、先に
  `$AGENTS_ROOT/policies/product-design.md`と該当する公式Product Design skillを実行する。
- アプリの起動・画面取得が必要なら `$AGENTS_ROOT/policies/mobile-testing.md` を読む。
  Expo / React Nativeでは `mobile-sdk-compatibility.md` も適用する。
- 寸法、向き、形式、alphaの有無、必要なdisplay targetはAppleの現行規約で確認する。
  CLIの古い「必須サイズ」説明だけを根拠にしない。

`reference/`、`backup/`、`raw/`、`generated/`、`review/`をlocale/display typeごとに分け、
`01-home.png`のような連番で順序を固定する。manifestには対象ID、相対パス、順序、寸法、
形式、local MD5、SHA-256、対応するremote ID、backupと復元可否を残す。
期限付きURLや認証情報を共有用manifestに含めない。

原寸画像と一覧プレビューを作り、文字切れ、可読性、翻訳、画面との一致、余白を確認する。
人間の目視がまだなら「目視未確認」と明示する。

## 5. 許可された変更を適用する

変更直前にremoteを読み直し、backup時点から他者の変更がないか照合する。
差分があれば取り込み、今回の対象を再計算する。画像・順序・対象・差分が確認できる状態で適用する。

CLIが対応するならplan/applyを使う。`--replace`は対象setの既存画像を先に削除する場合があり、
一括で成功または無変更となる操作ではない。backupと復元準備を満たしてから使う。
localeを省いたfan-outや別display typeの巻き込みを避け、指定したsetだけ操作する。

途中失敗では現状と各resource IDを記録し、既に成功した登録を重複実行しない。
CLIのresumeがある場合も、失敗記録とremoteの現状を照合してから再開する。
誤削除・誤差し替えなら、保存した画像・順序・対象表から復元して読み戻す。
復元でIDが変わる可能性も記録する。

## 6. 登録結果を読み戻して検証する

終了コード0やHTTP 200だけでは完了にしない。対象set/resourceを再取得し、次を確認する。

| 検証 | 根拠と判定 |
|---|---|
| 処理状態 | `COMPLETE`を確認。`UPLOAD_COMPLETE`等は待機中、`FAILED`は失敗として扱う |
| 対象と順序 | app/version、親ページ、locale、display type、件数、関係の順序をmanifestと照合 |
| 元ファイルの整合性 | remote `sourceFileChecksum`（MD5）と実際に送ったlocal MD5を比較 |
| 配信画像 | 再downloadし、寸法・形式・内容を確認。SHA-256同士は同じbyte表現のときに比較 |
| 見た目 | 実際の配信画像で文字・レイアウト・順序を確認。人間の目視実施は独立して記録 |

CDNの再圧縮・形式変換でSHA-256が違っても、即「アップロード失敗」にしない。
逆に、見た目が似ているだけでbyte一致としない。元MD5の一致、配信画像の内容確認、
byte一致は別の結果として示す。checksum欠落・不一致は対象と処理状態を再取得して調査し、
未解決なら整合性未確認とする。画素比較も色空間や圧縮の条件を明示する。

## 7. 必要な場合だけ拡張する

Product Page Optimizationは既存experimentとtreatmentを確認してからdraftを準備する。
実験作成・素材登録と、審査提出・開始・勝者の適用を混同しない。

「ヘッダ画像」は用途を解決する。App Clip、製品ページの素材、別の掲載枠ではresourceが異なる。
写真由来の知識だけでApp Clipと断定しない。App Clipなら対応するheader-imagesのhelpを確認する。
Custom Product Pageやtreatmentの画像へ通常version-localization用コマンドを流用しない。

## 8. 結果と手順を残す

作成済み、ローカル検証済み、アップロード済み、処理確認済み、目視済み、公開反映済みを区別する。
生成物・manifest・backup・復元手順・秘密値を除いた実行記録へのリンクと未検証事項を伝える。
ストア公開は実際の公開ページで確認できたときだけ報告する。
作業コンテキストと環境の知識は共通ルールに沿って既存Vaultへ記録する。

## Examples

- 「日本語ストア画像を作り直して。登録はまだ」→既存画像を取得し新画像と一覧を作成。
  アップロードせず、成果物と目視の状況を返す。
- 「この3枚に差し替えて」→対象・backup・変更直前の状態を確認し、指定setを更新して照合する。
- 「TestFlightへ配って」→このスキルの対象外。配布のワークフローへ引き継ぐ。

## Sandboxing Compatibility

調査と制作は読み取りと成果物ディレクトリへの書き込みで行える。
登録にはApp Store Connectへのネットワーク接続と対象への書き込み権限が必要。
Sandboxや認証の制約があれば該当工程を未実行として残し、制約を迂回しない。
