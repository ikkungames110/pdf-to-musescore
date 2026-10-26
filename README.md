# pdf2mscz — PDF 楽譜を MuseScore (.mscz) に変換

PDF の楽譜を解析してデジタルデータ (中間表現 JSON) に落とし込み、MuseScore 形式 (`.mscz`) で出力します。

## 2 つの経路

| PDF の種類 | 方法 | 人の手 |
|---|---|---|
| **ベクター PDF** (MuseScore・Finale・Dorico などから書き出したもの) | PDF 内の音楽記号 (SMuFL フォントのグリフ)・線・連桁の座標を直接解析 | 不要 (まれな要確認箇所だけ) |
| **画像 PDF** (紙のスキャンなど) | Claude Code が画像を読んで決まった形式の JSON を作る (HITL) | Claude Code に一言頼む |

手元の楽譜フォルダの PDF 116 個はすべて MuseScore から書き出したベクター PDF だったため、
**OCR も LLM も使わずに**、記号を座標から確定的に読み取れます。

### 精度 (手元の PDF と、対になる .mscz 115 曲で比較)

| 項目 | F1 |
|---|---|
| 音符 (小節・五線・拍位置・音価・音高が完全一致) | **0.997** |
| 休符 | 0.999 |
| 歌詞 (文字・音節のつながり) | 0.990 |
| 小節数・段数 | 115/115 曲で一致 |

残る誤りは主に、見た目が同じドラム (Bass Drum 35/36 など。PDF からは区別不可能) や、元データ側の特殊な設定です。
音価の合計が合わない小節は「要確認」として出力され (115 曲で計 37 か所)、HITL で直せます。

## インストール

```bash
uv venv && uv pip install -e .     # または pip install -e .
```

## 使い方

```bash
# PDF -> mscz (要確認箇所があれば表示)
pdf2mscz convert 曲.pdf -o 曲.mscz

# 中間表現 JSON も出す (要確認箇所の修正・HITL 用)
pdf2mscz convert 曲.pdf -o 曲.mscz --ir

# フォルダ内をまとめて
pdf2mscz convert 楽譜/*.pdf -d 出力フォルダ

# JSON -> mscz / JSON の検査
pdf2mscz from-ir 曲.json -o 曲.mscz
pdf2mscz validate 曲.json

# HITL 用: ページ画像 (段ごとの拡大つき)・指定小節の切り出し
pdf2mscz images 曲.pdf -d work/曲 --split
pdf2mscz crop 曲.pdf 46 -o m46.png
```

ブラウザ版 (インストール不要、PDF はブラウザ内だけで処理): https://pdf-to-musescore.pages.dev

## HITL (Claude Code に読ませる)

- 画像 PDF を変換: Claude Code に `prompts/hitl_omr.md の手順で 〇〇.pdf を mscz にして`
- 要確認箇所を直す: `prompts/hitl_review.md の手順で 〇〇.pdf の要確認箇所を直して`

どちらも出力は `docs/IR_FORMAT.md` の JSON に固定され、`pdf2mscz validate` で検査してから `.mscz` にします。

## ローカル LLM について

- **ベクター PDF には不要**です。上の自動解析が LLM より速く正確です (1 曲数秒、GPU 不要)。
- **画像 PDF** をローカルで自動化するなら、LLM より専用の OMR エンジン **Audiveris** (オープンソース、MusicXML を出力 → MuseScore で読み込み) が現実的です。
  RTX 3080 (10GB) で動く画像対応 LLM (Qwen2.5-VL 7B など) は、多声部の密な楽譜の音高・リズムを小節単位で正確に読むにはまだ精度が足りません。
  画像 PDF が増えてきたら Audiveris 連携 + 要確認箇所だけ HITL、という構成を追加できます。

## 仕組み

```
PDF ─ extract.py ─> 音楽グリフ・文字・線分・連桁 (座標付き)
    ─ layout.py  ─> 五線・段・小節線
    ─ recognize.py ─> 符頭+符幹=和音、連桁/旗/付点=音価、音部記号/調号/臨時記号=音高、
                      連符・タイ・声部・歌詞・コード・練習番号・テンポ
    ─> 中間表現 JSON (ir.py, docs/IR_FORMAT.md)
    ─ mscx_write.py ─> MuseScore 3.02 形式 .mscz (MuseScore 3 / 4 で開ける)
```

## 開発

```bash
python tools/evaluate.py [PDF やフォルダ]       # 同名 .mscz と比較して精度を出す
python tools/show_measure.py 曲.pdf 46 3 out.png   # 小節の画像・認識結果・正解を表示
```
