# モバイル SDK 互換性とデモの動作確認

Expo / React Native の新規実装、UI変更、検証、デモ再開、QR再発行に適用する。SDK番号の固定ルールではなく、利用するクライアントとの互換性確認ルールとする。

## 作業開始時

1. 対象の OS・実機/シミュレータ・Expo Go/development build を区別する。Expo Go のアプリバージョンと対応 SDK を、最新の端末画面・本人の確認・取得できる実機情報から記録する。今回の画像があれば既に分かることを聞き直さない。不明な端末条件を「最新だろう」と推測しない。
2. 実際の作業ディレクトリ、起動プロセス、workspace を特定する。package.json、lockfile、インストール済み expo / React / React Native / native modules を照合し、`npx expo config --type public` の解決済み設定も確認する。app config の sdkVersion 表記だけを変更して互換性を装わない。
3. [公式の互換性案内](https://docs.expo.dev/troubleshooting/expo-go-version-mismatch/)と[配布先](https://expo.dev/go)をその時点で確認する。ストアの配布状況や旧版の導入条件は OS・SDK・時点で変わる。iOS実機に旧版を簡単に入れられると案内しない。公式記述と端末の観測が違う場合は差異を明記し、実際の端末との不一致を無視しない。
4. SDK選択は今回利用するクライアントに合わせる。「別プロダクトで54だった」「以前57で成功した」は今回の互換性の証拠にしない。依存更新済み/クライアント更新済みなら過去の検証を失効させる。

## 不一致への対応

- 互換性エラーは、トンネル再接続・QR再生成・キャッシュ削除だけでは解消しない。SDKの比較を先に行う。
- 修正が依頼範囲なら、[公式アップグレード手順](https://docs.expo.dev/workflow/upgrading-expo-sdk-walkthrough/)と対象release notesに従い、SDKと関連依存を整合させる。飛び級の影響、Metro overrides、Expo Goに含まれないnative modules、Node要件も確認する。
- `npx expo install --check`、`npx expo-doctor`、型検査、対象OSのexport/buildを実行する。`expo install --fix` は依存を書き換えるため、修正工程として差分をレビューする。依存の警告を無条件にignoreしない。
- 認証、費用、配布方法の変更、未承認の開発ビルド導入が必要なら、その点だけ利用者へ確認する。ルール追加だけの依頼をSDKアップグレードの承認と解釈しない。

## QRを渡す直前

1. 正しいworkspaceのサーバーを起動し、要求された場合はトンネルを使う。QRをdecodeして実際の起動URLと一致することを確認する。
2. 実際の公開URLへ対象OSの `expo-platform` ヘッダーを付けてmanifestを取得し、SDK/runtimeVersionとbundleの取得を確認する。例: `curl -fsS -H 'expo-platform: ios' "$EXPO_DEMO_URL"`。SDK、対象端末、URL、確認日時、commit、起動方法を記録する。
3. 端末のExpo GoがサポートするSDKとmanifestのSDKが一致しない場合は、QRを「利用可能」として配布しない。端末情報がなければ「配信確認のみ・実機互換性未確認」と明記する。
4. 実機で初期画面と主要操作を確認する。実機を操作できなければ、その確認だけを利用者へ依頼し、未検証として扱う。Webやシミュレータの成功を実機成功と置き換えない。
5. 依存整合、bundle生成、ネットワーク到達、実機起動、主要操作の各結果を分けて報告する。トンネルの存続条件、watch/reload無効化などの回避策と未修正事項も伝える。

## 受け入れ例

- 端末SDK57 / プロジェクトSDK54 / manifest SDK54 / HTTP200 → **互換性NG**。doctorとCIが成功していても利用可能ではない。
- 端末SDK不明 / manifest SDK57 / export成功 → **配信・build確認のみ**。実機確認済みとはしない。
- 端末と配信SDK一致 / 依存整合 / 実機で初期画面と主要操作成功 → その端末・日時・commitの範囲で**実機確認済み**。後日の再起動・別端末に無条件で流用しない。
