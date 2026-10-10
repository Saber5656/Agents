# CLIの手順と照合

以下は `rorkai/App-Store-Connect-CLI` のソースで確認した例。
現場の`asc version`と各`--help`を確認してから使う。同名の `tddworks/asc-cli` と構文を混ぜない。
変数は対象を解決した値を渡し、IDや設定値を推測しない。

## 読み取り

```sh
asc version
asc --help
asc auth status
asc screenshots --help
asc screenshots list --help
asc screenshots download --help
asc api --help
```

`auth status`だけで実際のAPIアクセスや操作権限まで確認済みにはしない。
既存プロファイルの選択方法をhelpで確認し、対象アプリの読み取りで照合する。
ログには秘密値を出さない。

```sh
asc apps list
asc versions list --app "$APP_ID"
asc localizations list --version "$VERSION_ID"
asc screenshots list --version-localization "$LOCALIZATION_ID"
asc screenshots download --version-localization "$LOCALIZATION_ID" \
  --output-dir "$BACKUP_DIR"
```

`LOCALIZATION_ID`は`ja-JP`のようなlocaleコードではなく、version localization resourceのID。
CLI出力のラッパーやキーは版によって確認し、`data[]`や`sets[]`を想像で固定しない。
各listが全ページを返すか確認し、必要なページを取得する。
一覧が省略した属性は、既に確認したscreenshot IDへ読み取りGETで補完する。

```sh
asc api GET "/v1/appScreenshots/$SCREENSHOT_ID"
```

必要なら公式仕様で`fields[appScreenshots]`を調べる。APIのページングURLや画像URLを
無加工で公開ログへ転記しない。private inventoryと共有用manifestを分ける。

## Apple画像サーバーからの取得

`imageAsset`のURL属性と返却寸法を確認する。テンプレートの`{w}`、`{h}`、`{f}`等は、
レスポンスと公式定義に従って展開する。元ファイルのURLを推測して組み立てない。
公式APIから得たApple CDNのHTTPS URLであることを確かめ、API JWTを付けずにdownloadする。
URLはprivateファイルやクライアントの入力で渡し、署名付きURLをシェル履歴・argvへ露出させない。

download時のHTTP成功に加えて、画像として開けるか、実際の寸法・形式・内容を確認する。
サムネイルを元データと誤認しない。backupとして必要な解像度に不足する場合は復元可能としない。
再downloadのSHA-256は取得物の証跡であり、元アップロードの同一性は別に確認する。

## 制作とレビュー

`raw`は実アプリ画面、`reference`は参考素材、`generated`は登録候補。
元画面・言語・display typeを取り違えず、文字・実画面・装飾を確認できる一覧を用意する。
CLIのframe/reviewが使える場合はhelpと必要なローカル依存を確認する。
画像生成モデルや有料APIへ勝手に切り替えない。

```sh
asc screenshots sizes --help
asc screenshots list-frame-devices --help
asc screenshots frame --help
asc screenshots review-generate --help
asc screenshots plan --help
asc screenshots apply --help
```

`review-approve --all-ready`は人間が見た証拠にはならない。
plan/applyに使うmanifest/approvedファイルは使用版のschemaで作り、内容・対象を検証する。

## 指定setの更新

次の例は、差し替えが依頼され、全旧画像のbackupと復元手順が検証済みで、
変更直前のremoteとinventoryが一致する場合にのみ実行する。
`--replace`は対象setの旧画像を先に削除し得る。途中失敗の復旧を用意してから使う。

```sh
asc screenshots upload --help
asc screenshots upload --version-localization "$LOCALIZATION_ID" \
  --path "$GENERATED_DIR" --device-type "$DEVICE_TYPE" --replace --confirm
```

`DEVICE_TYPE`の入力はhelpで確認する。例示のCLIでは`IPHONE_65`等とAPIの
`APP_IPHONE_65`等は表記が異なるため、実際の対応表に従う。
ディレクトリ内のソート順と登録後の関係の順序を確認する。
元の枚数・新しい枚数が異なるときも、追加・残存・削除が意図したとおりか照合する。
個別deleteを使うなら、この対象setに属するIDだけを指定する。

## 照合と復元

| 保存する証跡 | 意味 |
|---|---|
| local MD5 / remote sourceFileChecksum | 元ファイルの送信整合性。AppleのchecksumはMD5 |
| local / downloaded SHA-256 | そのファイルのbyte列。同じ表現なら一致を検証できる |
| 画像寸法・形式・画素や目視 | Appleで変換された配信画像の内容確認。byte一致とは別 |
| resource/set IDと関係の順序 | ファイル名だけでは検証できない対象・掲載順の確認 |

処理完了後に属性を再取得する。`UPLOAD_COMPLETE`はまだ`COMPLETE`ではない。
`FAILED`ではエラー内容とpartial stateを保存し、成功と報告しない。
timeoutは処理失敗と決めつけず、現状確認から再開する。

復元では変更前の対象・順序表と検証済みbackupを使い、該当setだけ再登録する。
復元後も処理状態・件数・順序・内容を読み戻す。復元画像がCDN由来なら元データとの違いを報告する。
この手順はIDの維持や無停止の原子的復元を保証しない。

## 実験とヘッダ画像

まず読み取りとhelpから始める。

```sh
asc product-pages experiments --help
asc product-pages experiments list --version-id "$VERSION_ID"
asc app-clips header-images --help
```

実験はCLI版とresource世代（v1/v2）で親IDが異なる。draft作成・treatmentへの素材登録は
開始・審査提出・勝者適用と別操作として扱い、必要な権限と既存の許可に従う。
ヘッダ画像は最初に掲載枠を解決する。App Clipと特定できた場合だけ、そのlocalization IDを使う。
通常version用、Custom Product Page用、treatment用のresourceを取り違えない。
