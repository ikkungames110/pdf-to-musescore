"""検出結果 (五線・小節線) をページ画像に重ねて PNG に出力するデバッグ用スクリプト。

使い方: python tools/debug_page.py score.pdf ページ番号(0始まり) out.png [x0 y0 x1 y1]
"""
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2mscz.extract import extract_page  # noqa: E402
from pdf2mscz.layout import find_systems  # noqa: E402

pdf, pno, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
doc = pymupdf.open(pdf)
page = doc[pno]
pg = extract_page(page, pno)
shape = page.new_shape()
for s in find_systems(pg):
    for st in s.staves:
        shape.draw_rect(pymupdf.Rect(st.x0, st.top, st.x1, st.bottom))
        shape.finish(color=(0, 0, 1), width=0.3)
    for b in s.barlines:
        shape.draw_line((b.x, s.top - 4), (b.x, s.bottom + 4))
        shape.finish(color=(1, 0, 0) if b.kind == "normal" else (0, 0.7, 0), width=0.8)
shape.commit()
clip = pymupdf.Rect(*map(float, sys.argv[4:8])) if len(sys.argv) >= 8 else None
page.get_pixmap(dpi=120 if clip is None else 250, clip=clip).save(out)
