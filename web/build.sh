#!/usr/bin/env bash
# web/dist を作る: 静的ファイル + pdf2mscz パッケージ (zip) + PyMuPDF の wasm ホイール
set -euo pipefail
cd "$(dirname "$0")"
WHL=pymupdf-1.28.2-cp313-abi3-pyemscripten_2025_0_wasm32.whl
rm -rf dist && mkdir -p dist
cp index.html worker.js dist/
mkdir -p .cache
if [ ! -f ".cache/$WHL" ]; then
  curl -sSfL -o ".cache/$WHL" "https://files.pythonhosted.org/packages/58/8c/d897dcd32a25b58186c968b15ce4324ca029e9d96460de12325314e390be/$WHL"
fi
cp ".cache/$WHL" "dist/${WHL/pyemscripten_2025_0/pyodide_2025_0}"  # Pyodide 0.28 の micropip は旧タグ名しか認識しない
(cd .. && python3 - <<'PY'
import zipfile, pathlib
with zipfile.ZipFile("web/dist/pdf2mscz.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in pathlib.Path("pdf2mscz").rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            z.write(p, p.as_posix())
PY
)
cp ../docs/IR_FORMAT.md ../prompts/hitl_omr.md ../prompts/hitl_review.md dist/
echo "built: $(du -sh dist | cut -f1)"
