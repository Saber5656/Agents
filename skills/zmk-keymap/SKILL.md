---
name: zmk-keymap
description: >
  Eyelash Corne（分割キーボード）のZMKファームウェアキーマップを更新・ビルド・書き込みするスキル。
  ユーザーが「キーマップを変更したい」「レイヤーにキーを追加したい」「ショートカットキーの設定を変えたい」
  「ファームウェアをビルドして書き込みたい」「ZMKの設定を修正したい」と言った場合は、
  明示的に「zmk-keymap」と言わなくてもこのスキルを使うこと。
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
category: Dev
created: 2026-03-15
updated: 2026-09-08
status: active
purpose: Eyelash Corne ZMK キーマップの安全な編集・ビルド・書き込みワークフローを提供する
argument-hint: "[変更内容の説明]"
---

# ZMK Keymap スキル (Eyelash Corne)

Eyelash Corne 分割キーボードのキーマップを設定ファイルから編集し、ビルドして書き込むための手順とナレッジ。

## 重要なパス

```
# キーマップ（シンボリックリンク経由でどちらからでもアクセス可）
${ZMK_DOTFILES_ROOT}/zmk/zmk-config/config/eyelash_corne.keymap
${ZMK_WORKSPACE_ROOT}/config/zmk-config/config/eyelash_corne.keymap  ← シムリンク

# KConfig設定
${ZMK_DOTFILES_ROOT}/zmk/zmk-config/config/eyelash_corne.conf

# ビルドターゲット定義
${ZMK_DOTFILES_ROOT}/zmk/zmk-config/build.yaml

# ビルド作業ディレクトリ
${ZMK_WORKSPACE_ROOT}/
```

実環境の root は `.env` や `*.local.md` などの ignored file で管理し、Git 管理しない。

## ビルド・書き込みコマンド

```bash
cd "${ZMK_WORKSPACE_ROOT}"

# 通常のキーマップ変更は中央側（左側）だけビルドする
nix develop --command just build eyelash_corne_left

# 右側専用のoverlay/configを変更した場合だけ右側もビルドする
# nix develop --command just build eyelash_corne_right

# 通常のキーマップ変更の書き込み（左側Nice!Nanoをブートローダーにする）
nix develop --command just flash eyelash_corne_left

# 右側専用の設定・firmwareを変更した場合だけ右側へ書き込む
# nix develop --command just flash eyelash_corne_right

# settings_reset（ペアリングリセット時）
nix develop --command just flash settings_reset
```

ビルド成果物: `firmware/nice_view-eyelash_corne_left.uf2`

## レイヤー構成

| レイヤー | 番号 | display-name | 概要 |
|---------|------|-------------|------|
| default_layer | 0 | QWERTY | メインレイヤー |
| lower_layer | 1 | NUMBER | 数字・Fnキー |
| raise_layer | 2 | SYMBOL | 記号・矢印 |
| layer_3 | 3 | Fn | ファンクション・マウス |

### レイヤーアクセス方法

- **Layer1**: Layer0 右Th3 (`&lt 1 BSPC`) でホールド
- **Layer2**: Layer0 左Th3 (`&lt 2 BSPC`) でホールド
- **Layer3**: Layer1 左Th3 (`&lt 3 BSPC`) または Layer1 右Th1 (`&lt 3 DEL`) でホールド

## サム（親指）キー配置

```
Layer0:  [LGUI/Tab]  [LSHFT/Space]  [LT1/Bspc]      [LT2/Bspc]  [RSHFT/Ret]  [RCTRL/Tab]
Layer1:  [none]      [none]         [LT3/Bspc]      [LT3/Del]   [RSHFT/Ret]  [N0]
Layer2:  [none]      [LANG2]        [LANG1]          [none]      [none]       [PgUp]
Layer3:  [trans]     [trans]        [trans]          [trans]     [trans]      [trans]
```

## ホームロウモッド

```c
// 左手: hml（ホールド = 左手修飾キー）
&hml LCTRL ESC  &hml LCTRL A  &kp S  &hml LALT D  &hml LGUI F

// 右手: hmr（ホールド = 右手修飾キー）
&hmr RGUI J  &hmr RALT K  &hmr RCTRL L
```

---

## 既知のハマりポイントと解決策

詳細は `references/known-issues.md` を参照。主要な問題を以下に要約。

### ⚠️ LANG1/LANG2 が効かない（最重要）

**原因**: `CONFIG_ZMK_HID_REPORT_TYPE_NKRO=y` を有効にすると、HIDレポートのキーコード上限がデフォルトで `0x67`（HID_USAGE_KEY_KEYPAD_EQUAL）になる。LANG1=`0x90`、LANG2=`0x91` はこれを超えるため**サイレントに無視される**。

**解決策**: `eyelash_corne.conf` に以下を追加：
```
CONFIG_ZMK_HID_KEYBOARD_NKRO_EXTENDED_REPORT=y
```
これにより上限が LANG8（`0x97`）まで拡張され、LANG1/LANG2 が通るようになる。

**現在のconf状態**: 両設定とも有効（設定済み）。

### ⚠️ ZMK Studio のオーバーライドが残る

ZMK Studio で変更したキーマップは flash に保存され、ファームウェアを書き直しても残る。

**現在の運用**: Studio はビルドから除外済み（`build.yaml` に `snippet: studio-rpc-usb-uart` なし）。ファイルベースのキーマップが唯一のソース。

過去に Studio を使っていた場合のリセット方法:
```bash
nix develop --command just flash settings_reset
# → 本番ファームウェアを再書き込み
# → BLE ペアリングを再設定
```

### ⚠️ ダブルタップで Hold が発動する

`&lt` や `&mt` をすばやく2回タップすると Hold 動作（レイヤー移行）が起動する。

**解決策**: キーマップ先頭に設定済み：
```c
&lt { quick-tap-ms = <175>; };
&mt { quick-tap-ms = <175>; };
```

---

## キーマップ編集の手順

1. `eyelash_corne.keymap` を読んで現状を確認する
2. 変更を加える（レイヤー番号・キー位置に注意）
3. ビルドして確認:
   ```bash
   cd "${ZMK_WORKSPACE_ROOT}"
   nix develop --command just build eyelash_corne_left
   ```
4. 左側 Nice!Nano をブートローダーモードにして書き込み:
   ```bash
   nix develop --command just flash eyelash_corne_left
   ```
5. 右側は書き込まない（通常のキーマップ変更は左側への書き込みだけで反映される）。右側専用のoverlay/configやハードウェア用firmwareを変更した場合に限り、右側も別途ビルド・書き込みする。
6. 動作確認後、dotfiles にコミット

### 左右分割の書き込み方針（重要）

このEyelash Corne構成では、`keymap` のbindings・layers・behaviorなど通常のキーマップ変更は中央側の左Nice!Nanoへ書き込めばよい。右側UF2を続けて書き込む必要はなく、右側を待機対象にも追加しない。右側専用overlay/config、基板固有のfirmware修正などを変更した場合だけ右側を対象にする。この判断は、過去の実機更新で左側のみの書き込みを完了し、右側更新なしで運用できた結果を反映している。

## キーマップの行列レイアウト（参考）

キーマップの bindings は左から右、上から下に並ぶ。各行13キー＋エンコーダー1個。

```
左手6列 + エンコーダー + 右手6列 = 13要素/行（Row1〜3）
親指Row: 左3 + 右3 = 6要素（エンコーダーなし）
```

## Sandboxing Compatibility

**Works without sandboxing:** ✅ Yes
**Works with sandboxing:** ❌ No（ビルド・書き込みコマンドにBash が必要）

- **Filesystem**: Read/Write（keymap, conf, build.yaml）
- **Bash**: ビルド・書き込み実行に必要
- **Configuration**: Nix 開発環境（`nix develop`）が必要
