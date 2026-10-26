# pdf2mscz

PDF 楽譜 -> MuseScore (.mscz) 変換ツール。詳細は README.md。

## よくある依頼と手順

- 「〇〇.pdf を mscz にして」
  1. `.venv/bin/pdf2mscz convert 〇〇.pdf -o 〇〇.mscz --ir`
  2. 「五線が見つかりませんでした」なら画像 PDF → `prompts/hitl_omr.md` の手順に従う
  3. 要確認箇所が出たら `prompts/hitl_review.md` の手順で直す
- 中間表現の形式は `docs/IR_FORMAT.md` が正。HITL の出力は必ずこの形式にし、`pdf2mscz validate` を通す。

## 開発メモ

- 環境: `uv venv && uv pip install -e .` (pymupdf は Web 版と同じ 1.28.2 に合わせる)
- 精度の確認: `.venv/bin/python tools/evaluate.py` (サンプルは `/mnt/c/Users/user/Desktop/楽譜`。PDF と同名 .mscz が正解)
- 小節単位のデバッグ: `tools/show_measure.py`
- macOS 由来の日本語ファイル名は NFD のことがある (find/シェルの文字列で一致しない)
- Web 版は `web/` (Pyodide 上で同じ Python パッケージを動かす)。`web/build.sh` で `web/dist` を作り Cloudflare Pages (`pdf-to-musescore`) にデプロイ
