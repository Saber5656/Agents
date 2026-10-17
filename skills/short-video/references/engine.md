# エンジン仕様と解析メモ

2026-10-18に添付7画像と公開コードを照合。
参照: https://github.com/kokushin/short-animation-harness
確認revision: `3711254bff7d52121e483deef78385e227e8aedd`。
コードはMIT、画像・音素材は同梱されない。資料にある動物や音楽の再配布許可はコードのMITから得られない。

## 導入

Node.js 20以上、npm、ffmpeg、ffprobeが必要。既存checkoutの未保存変更を保持する。
新規の場合は空の目的別ディレクトリへcloneし、利用するrevisionを記録する。既存checkoutを自動でresetしない。

```sh
git clone https://github.com/kokushin/short-animation-harness.git short-animation-harness
cd short-animation-harness
npm ci
npm run setup
npm run typecheck
```

`npm run setup`はGoogle FontsからOFLフォントを取得し素材を登録する。依存関係のlockfileが無い場合だけ`npm install`を使う。
`REMOTION_BROWSER_EXECUTABLE`で既存の互換性あるChromium実行ファイルを指定できる。未指定ならRemotionがheadless browserを取得する場合がある。
`ffmpeg -hide_banner -filters`で`drawtext`があるか確認する。macOSの一部ビルドには無い。音量計測には`loudnorm`/`ebur128`も使う。

## 台本と素材

| 場所 | 用途 |
|---|---|
| `public/places/<id>.png` | 背景。jpg/jpeg/webpも使用可能 |
| `public/actors/<id>/default.png` | 役者。png/jpg/jpeg/webpを登録可能、透過PNG推奨 |
| `public/actors/<id>/<look>.png` | 見た目の差分 |
| `public/se/<id>.wav` | 効果音/声。mp3/m4a/oggも使用可能 |
| `public/bgm/<id>.mp3` | BGM。wav/m4a/oggも使用可能 |
| `assets.json` | 名前→ファイル、役者幅`w`と元画像の向き`faces` |
| `src/assets/index.generated.json` | 自動生成する寸法と音の長さ |

`npm run assets`の後に実在するidを選ぶ。castのキーは登場人物ID、`actor`は素材IDであり別物。
同じ素材を複数の登場人物として使える。場面の`setup`に無い登場人物は操作できない。
左向きに描かれた絵には素材側の`faces: "left"`を設定し、`turn.facing`と合わせる。

役者素材`dot/default.png`を登録した後の例（BGMなし）：

```json
{
  "format": "short", "title": "待ち合わせ", "end": "cut",
  "cast": { "a": { "actor": "dot", "size": 0.6 } },
  "scenes": [{
    "id": "s1", "place": "white", "enter": "cut",
    "cam": { "x": 800, "y": 450, "zoom": 1 },
    "setup": { "a": { "at": [700, 730], "facing": "right" } },
    "beats": [
      { "dur": 1, "note": "主人公を見せる", "text": [{ "body": "待ち合わせ", "pos": "top" }] },
      { "dur": 2, "note": "右へ歩く", "do": [{ "who": "a", "act": "move", "to": [870, 730] }] },
      { "dur": 1, "note": "到着", "text": [{ "body": "到着！", "pos": "top" }] }
    ]
  }]
}
```

拍の`dur`は秒、アクションの`at`は拍の先頭からの秒。`move.dur`省略時は拍末まで。
舞台の高さ900、背景がない場合の横幅1600。`at`/`to`は足もとの中央、yが大きいほど前面。
通常の正のzoomでは可視幅は`900 * (outputWidth / outputHeight) / zoom`。実際のカメラ中心は`src/engine/state.ts`で背景境界へクランプされるため、端付近はクランプ後の中心で計算する。幅120、cam.x=800、zoom=1の縦画面なら全身の入るxはおおむね607〜993（追加の余白は別途）。checkはこの条件を完全には検査しない。

横動画1920×1080は`format: "long"`。カメラ中心とzoomから可視範囲が決まる。
背景の幅、役者の画像比率と幅、zoomも含めて画面内に収める。

## 実行と落とし穴

- `check`: schema・参照・一部のはみ出し・逆方向歩行等を検査し時刻表を出す。全フレームの見た目や聴感の代替にはならない。
- `stills`: 拍の60%地点を抽出する。瞬間的な不具合は見落とす。
- `render`: 最終版`out/<slug>/<slug>.mp4`と`thumbnail.png`。`--draft`は半解像度の`<slug>.draft.mp4`。
- `review`: 最終版MP4を対象に既定4fps。draft-onlyには使えない。同じ`out/<slug>/review/`を毎回削除するため、区間指定前に記録を別名で保存する。
- 全フレーム抽出はスキルの`review_frames.py`を使えばdraftにも適用できる。フレーム数とシート範囲のmanifestを作り、既存ディレクトリを拒否する。drawtextなしでも抽出できるが画像内ラベルが省かれる。
- scriptの出力数一致は確認資料の完全性を示すだけで、全シートを目視した証明ではない。
- `render`は音量を-14 LUFS目標に調整し、short-termが大きい部分を抑える。リミッターや素材次第で最終値がずれる。最終MP4を再解析する。聴取環境が無ければ音量検査と聴感を区別する。

## 資料から採用した設計

素材登録→構造化台本→書き出し前検査→実動画の確認→修正の反復を採用。
「全コマ」という説明はupstreamの既定抽出と一致しないため、スキルでは間引きと全フレームを区別する。
不足演出をschema/時間割/描画/検査/説明へ一貫して追加する考え方は採用するが、旧Saihaiや常駐基盤の導入は含めない。
