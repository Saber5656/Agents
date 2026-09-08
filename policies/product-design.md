# Web・モバイルの設計を実装より先に確定する

Webサイト・Webアプリ・モバイルアプリの新規UI設計、画面追加、再設計に適用する。
利用者の継続指示として、設計時は OpenAI の `product-design@openai-curated-remote`
を必ず使用する。バックエンドのみの修正や、決定済みデザイン内の小さな不具合修正で
新しいデザイン案を作り直す必要はない。

## 実行入口

1. 現在のセッションの Product Design skills と導入状態を確認する。
   CLIでは `codex plugin list --json` で正式ID・version・installed・enabledを確認する。
   初回導入は `codex plugin add product-design@openai-curated-remote --json`。
   バージョンや個人環境のcache絶対パスをリポジトリに固定しない。
2. 導入済みプラグインの `skills/index/SKILL.md` と `skills/user-context/SKILL.md`
   を読み、user-contextのpreflightを実行する。その実在するパッケージの
   `skills/user-context/scripts/user_context_preflight.py` を使う。
   続けて現在のrouterが指定する設計skillを実行する。名前を挙げるだけでは使用済みにしない。
3. 新規設計は `get-context` で対象・利用者の目的・Web/モバイルを整理し、
   未決定の見た目は `ideate`、選定済み画像からの実装は `image-to-code` へ進む。
   既存画面の評価は `audit`、忠実なURL再現は `url-to-code` という専用入口を尊重する。
   同名の他プラグインskillと混同しない。
4. セッションにplugin toolsがない場合は、導入済みの正規skillを明示的に読み、
   そのskillが必要とする利用可能な実ツールで実行する。workerでpluginsを無効化している
   設定を一括解除しない。必要なskill・画像生成・画面確認等が使えない場合は、その設計工程を
   未実行として記録し、別の対応セッションへ引き継ぐ。独立した作業は継続する。

## 初回実装の前に決めること

- 利用者と主要操作、情報の優先順位、画面構成、遷移、配色、文字、余白、主要部品、
  素材、レスポンシブ動作、読み込み・空・エラー状態、アクセシビリティを設計する。
- 既存の正式デザイン・ブランド・コンポーネントを尊重する。初回生成した仮画面を
  無条件に正解や新しい制約として扱わない。新規案は同じ要件から比較し、選定理由を残す。
- 視覚的なターゲットが未決定ならrouter/ideateの案提示・選択を実装より先に行う。
  利用者の選択済み案を聞き直さない。案の選定まで委任済みなら主担当が目的・制約に基づき
  選定して記録する。新たな重大な仕様選択だけを利用者に確認する。
- 選定前にUIをscaffoldしたり、仮の画面を本実装として作り始めたりしない。
  設計を既存の設計文書、なければ `DESIGN.md` に記録してから、初回実装を完成形に近づける。
  一発で無修正になることは保証せず、実画面・主要操作の検証と必要な修正は行う。
- モバイルは対象のnative/runtime要件とSDK互換性方針を維持する。pluginのWeb prototypeを
  実機動作済みモバイルアプリの代わりにしない。既存プロジェクトを勝手に別templateへ置換しない。

## 記録と完了

Agents Vaultを文脈・環境知識の正本とし、pluginの保存contextから対応するVault記録を参照する。
使用したplugin版・skill・preflight結果・設計案・選定理由・設計文書・実画面の検証を、
元のローカルタスクに結び付ける。秘密値は保存しない。

設計QAは選定案と実画面を対応付けて行い、同じ証拠でレビューを繰り返さない。
必要な修正後は変更箇所を確認する。別課題はローカル登録までとする。
導入、skill実行、画像生成、実装、実機確認、公開の到達状態を区別する。
plugin導入は外部サービスの有料利用や未承認の公開を許可するものではない。

公式案内: [Product Designを含むCodexプラグイン](https://openai.com/index/codex-for-every-role-tool-workflow/)
／ [公式配布先](https://chatgpt.com/plugins/product-design)。具体的な手順は導入版のskillを優先する。
