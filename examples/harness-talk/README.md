# ハーネスに気をつけるべき要素

minorun Marp skillsで作成した、AIエージェントのハーネス設計を扱う約10分のサンプル。
特定イベントや話し手の実話ではない。例は説明用として明示している。

- `deck.pdf`: 投影・閲覧用（25ページ）
- `deck.html`: ブラウザで閲覧するスライド。矢印キーで移動
- `deck.md`: 編集するMarp原稿。コメント内に発表ノート
- `speaker-notes.md`: 時間配分・説明・一次資料へのリンク
- `preview.png`: 全ページ一覧
- `VALIDATION.md`: 実行した検査と目視の記録

HTMLには隣の `images/` が必要。フォルダ全体を保持する。PDFは単体で持ち運べる。

## 再生成

依存関係の導入方法は `skills/slide-design-dark/README.md` を参照。
このフォルダで、インストール済みMarp CLIとPillow導入済みPythonを使用する。

```sh
marp --no-stdin deck.md --pdf --html --theme theme/minorun-dark.css --allow-local-files
marp --no-stdin deck.md --html --theme theme/minorun-dark.css -o deck.html
python3 tools/check-dark-margins.py deck.pdf
python3 tools/check-dark-gaps.py deck.pdf
python3 tools/check-figure-text.py deck.pdf
node tools/check-svg-box-fit.mjs images/*.svg
```

## 帰属

テーマと検査ツールは [minorun365/minorun-marp-skill](https://github.com/minorun365/minorun-marp-skill)
の `c6b20bbb8a1f2d2059e3764e2acd539c312785e4` から無変更で複製。
Apache-2.0、Copyright 2026 Minoru Onda。ライセンス原文は `LICENSE`。
デッキ固有のCSSは `deck.md` に分離し、SVG図は今回新規作成。
外部イラストは使用していない。技術内容の出典は `speaker-notes.md` にページ単位で記載。
