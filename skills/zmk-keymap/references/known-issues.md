# ZMK Keymap 既知の問題と解決策

## 1. LANG1/LANG2 キーが反応しない（NKRO + 高位キーコード問題）

### 症状
- `&kp LANG1` / `&kp LANG2` をキーマップに設定してもmacOSで言語変換が起きない
- ZMK Studio の Key Picker で "Lang" が選択できない

### 根本原因
ZMK の NKRO（N-Key Rollover）モードはビットマップ形式の HID レポートを使う。
デフォルトでは上限が `HID_USAGE_KEY_KEYPAD_EQUAL`（0x67）に設定されている。

```c
// zmk/app/include/zmk/hid.h
#if IS_ENABLED(CONFIG_ZMK_HID_KEYBOARD_NKRO_EXTENDED_REPORT)
#define ZMK_HID_KEYBOARD_NKRO_MAX_USAGE HID_USAGE_KEY_KEYBOARD_LANG8   // 0x97
#else
#define ZMK_HID_KEYBOARD_NKRO_MAX_USAGE HID_USAGE_KEY_KEYPAD_EQUAL      // 0x67
#endif
```

LANG1 = 0x90、LANG2 = 0x91 は 0x67 を超えるため、HID レポートから**サイレントに除外**される。

### 解決策
`eyelash_corne.conf` に以下を追加：

```
CONFIG_ZMK_HID_KEYBOARD_NKRO_EXTENDED_REPORT=y
```

### 現在の状態
✅ 解決済み。`eyelash_corne.conf` に両設定が記載されている：
```
CONFIG_ZMK_HID_REPORT_TYPE_NKRO=y
CONFIG_ZMK_HID_KEYBOARD_NKRO_EXTENDED_REPORT=y
```

---

## 2. ZMK Studio のオーバーライドが残る

### 症状
- キーマップを修正してファームウェアを書き直してもキーの挙動が変わらない
- 一部のキーだけ変更が反映されない

### 根本原因
ZMK Studio で変更したキーマップは NVS（フラッシュメモリ）に保存される。
ファームウェアを書き直してもこのデータは消えず、ファームウェアの設定を上書きし続ける。

### 解決策

**方法1（推奨）: settings_reset でフラッシュをクリア**
```bash
# 左側 Nice!Nano をブートローダーモードに
nix develop --command just flash settings_reset

# 本番ファームウェアを再書き込み
nix develop --command just flash eyelash_corne_left

# BLE ペアリングを再設定（必要に応じて）
```

**方法2: ZMK Studio で個別リセット**
ZMK Studio に USB 接続して、当該キーを「firmware default」にリセットする。

### 現在の状態
✅ Studio をビルドから除外済み（`build.yaml` に `snippet: studio-rpc-usb-uart` なし）。
ファイルベースのキーマップが唯一の設定ソースになっているため、この問題は発生しない。

---

## 3. ダブルタップで Hold 動作が起動する

### 症状
- Delete キーを2回連続で素早く押すとレイヤー移行が発動する
- Shift+Enter などでも同様

### 根本原因
`&lt`（layer-tap）や `&mt`（mod-tap）はデフォルトで quick-tap を無効にしており、
短時間内の2回目のタップを Hold として解釈することがある。

### 解決策
`eyelash_corne.keymap` 先頭（`/ {` の前）に設定済み：
```c
&lt { quick-tap-ms = <175>; };
&mt { quick-tap-ms = <175>; };
```
175ms 以内に同じキーをタップすると Hold ではなく Tap として扱われる。

### 現在の状態
✅ 設定済み。

---

## 4. ビルドが古いキャッシュを使う

### 症状
- `build.yaml` を変更してもビルドが古いターゲット（例: `eyelash_corne_studio_left`）を使い続ける

### 原因
west が既存のビルドディレクトリをキャッシュとして再利用する。

### 解決策
```bash
# ビルドキャッシュを削除してからリビルド
rm -rf "${ZMK_WORKSPACE_ROOT}/build/"
nix develop --command just build eyelash_corne_left
```

---

## 5. シリアルポートが占有されている

### 症状
ZMK Studio 接続時に `Failed to execute 'open' on 'SerialPort': The port is already open.`

### 解決策
```bash
# 占有プロセスを確認
lsof /dev/cu.usbmodem*

# USB を抜き差し、またはブラウザを完全に再起動
```
