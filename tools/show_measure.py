"""指定した小節 (1 始まり) の PDF 上の画像と、認識結果・正解を表示するデバッグ用スクリプト。

使い方: python tools/show_measure.py score.pdf 小節番号 [五線番号(1始まり)] out.png
"""
import json
import os
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2mscz import mscx_read  # noqa: E402
from pdf2mscz.extract import extract  # noqa: E402
from pdf2mscz.layout import find_systems, stem_width  # noqa: E402
from pdf2mscz.recognize import recognize  # noqa: E402

pdf, mno = sys.argv[1], int(sys.argv[2])
staff = int(sys.argv[3]) if len(sys.argv) > 4 else None
out = sys.argv[-1]
pages = extract(pdf)
sw = stem_width(pages)
k = 0
for p in pages:
    for s in find_systems(p, sw):
        n = len(s.barlines)
        if k < mno <= k + n:
            j = mno - k - 1
            x0 = s.barlines[j - 1].x if j > 0 else s.x0
            x1 = s.barlines[j].x
            if staff:
                st = s.staves[staff - 1]
                y0, y1 = st.top - 30, st.bottom + 30
            else:
                y0, y1 = s.top - 30, s.bottom + 30
            pymupdf.open(pdf)[p.index].get_pixmap(dpi=220, clip=pymupdf.Rect(x0 - 40, y0, x1 + 5, y1)).save(out)
            print(f"page {p.index + 1}, 段内 {j + 1} 小節目")
        k += n
res = recognize(pdf)
m = res["measures"][mno - 1]
sel = [m["staves"][staff - 1]] if staff else m["staves"]
print("認識:", json.dumps({k: v for k, v in m.items() if k != "staves"}, ensure_ascii=False))
for st in sel:
    print("  ", json.dumps(st, ensure_ascii=False))
if os.path.exists(pdf[:-4] + ".mscz"):
    gt = mscx_read.read(pdf[:-4] + ".mscz")
    m = gt["measures"][mno - 1]
    sel = [m["staves"][staff - 1]] if staff else m["staves"]
    print("正解:", json.dumps({k: v for k, v in m.items() if k != "staves"}, ensure_ascii=False))
    for st in sel:
        print("  ", json.dumps(st, ensure_ascii=False))
