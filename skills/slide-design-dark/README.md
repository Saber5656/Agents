# minorun Marp skills

[minorun365/minorun-marp-skill](https://github.com/minorun365/minorun-marp-skill) の3スキルを併用する。

- `slide-story`: 登壇の構成、つかみ、段階的な開示、時間配分。
- `slide-figures`: 図解の情報量、SVG、文字サイズ。
- `slide-design-dark`: 黒地テーマ、余白、PDFとSVGの検査。

たとえば「minorunのスキルで、10分の登壇資料をMarpとPDFで作って」と依頼する。
サンプルはリポジトリの `examples/harness-talk/` にある。

## リソースと依存関係

テーマは `theme/minorun-dark.css`、検査は `tools/` に同梱。
両ディレクトリを資料フォルダにコピーして使う。各skillのライセンスは `LICENSE`、
取得元・固定リビジョン・ローカル追記は `UPSTREAM.md` を参照。

必要なものは Node.js、Marp CLI、Chrome、Python 3 + Pillow、poppler、mupdf-tools。
macOSのフォントは既存のヒラギノ丸ゴへフォールバックする。
MarpはMarkdownをスライドへ変換する道具で、編集元はMarkdownのまま残せる。
PDF生成にはChrome、レイアウト検査には画像化・文字抽出のための追加ツールが必要。

```sh
npm install --prefix .local/marp --no-audit --no-fund @marp-team/marp-cli@4.5.1
python3 -m venv .local/marp-venv
.local/marp-venv/bin/pip install Pillow==12.3.0
brew install poppler mupdf-tools
export PATH="$PWD/.local/marp/node_modules/.bin:$PWD/.local/marp-venv/bin:$PATH"
export MARP_NODE_MODULES="$PWD/.local/marp/node_modules/"
mkdir -p my-talk
cp -R "$SKILLS_ROOT/slide-design-dark/theme" "$SKILLS_ROOT/slide-design-dark/tools" my-talk/
```

## 書き出しと検査

以下は資料フォルダ内で実行する。`marp` はインストールしたCLI、`python3` は
Pillowを導入した仮想環境のものを使う。

```sh
marp --no-stdin deck.md --pdf --html --theme theme/minorun-dark.css --allow-local-files
marp --no-stdin deck.md --html --theme theme/minorun-dark.css -o deck.html
python3 tools/check-dark-margins.py deck.pdf
python3 tools/check-dark-gaps.py deck.pdf
python3 tools/check-figure-text.py deck.pdf
node tools/check-svg-box-fit.mjs images/*.svg
```

`--html` は自作デッキ内のHTMLを有効化する。`--allow-local-files` は画像等のローカル読込を許可するため、対象デッキと参照先を確認して使う。
SVG検査が `puppeteer-core` を見つけられない場合は、実際に解決できるMarpの
node_modulesルートを `MARP_NODE_MODULES` に設定する。Chromeの場所は `CHROME_PATH` で変更できる。

上流の `check-dark-gaps.py` はNGを出しても終了コードが0になるため、
終了コードだけでなく出力の `NG 0 件` まで確認する。
`check-reuse-diff.py` は過去資料を流用した場合にだけ使う。
検査ツールの未検出は合格の証明ではない。全ページを画像にして目視し、図のページは
150dpi以上で確認する。個別デッキの見た目は `deck.md` のstyleで調整し、共通テーマを変更しない。

## 検証環境

今回のサンプルは Marp CLI 4.5.1、Pillow 12.3.0、mupdf-tools 1.28.3 と
macOS上のChromeで生成。実行時の詳細と目視結果はサンプルの `VALIDATION.md` を参照。
新規作成ではNode/Python依存をローカルに導入する。既存のグローバル設定は変更しない。
