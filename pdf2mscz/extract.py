"""PDF ページからベクター要素 (音楽グリフ・テキスト文字・線分・塗り多角形・曲線) を取り出す。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

MUSIC_FONTS = ("Leland", "MScore", "Bravura", "Emmentaler", "Gonville", "MuseJazz", "Petaluma", "Finale")
MUSIC_TEXT_FONTS = ("LelandText", "BravuraText", "MScoreText", "MuseJazzText", "PetalumaText")


@dataclass
class Glyph:
    code: int        # SMuFL コードポイント
    x: float         # 原点 x (グリフ左端)
    y: float         # 原点 y (ベースライン = 五線上の位置)
    w: float         # 送り幅
    size: float      # フォントサイズ (pt)
    text_font: bool  # LelandText などテキスト用音楽フォント

    @property
    def x1(self):
        return self.x + self.w

    @property
    def cx(self):
        return self.x + self.w / 2


@dataclass
class Char:
    c: str
    x0: float
    x1: float
    y: float         # ベースライン
    size: float
    font: str
    top: float
    bottom: float


@dataclass
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    path: int        # 同じパス由来かの識別子

    @property
    def horizontal(self):
        return abs(self.y1 - self.y0) < 0.05 and abs(self.x1 - self.x0) > 0.05

    @property
    def vertical(self):
        return abs(self.x1 - self.x0) < 0.05 and abs(self.y1 - self.y0) > 0.05


@dataclass
class Poly:
    pts: list        # [(x, y), ...]
    x0: float
    y0: float
    x1: float
    y1: float

    def y_at(self, x: float) -> tuple[float, float]:
        """多角形 (連桁を想定) の x における上端・下端 y。"""
        ys = []
        n = len(self.pts)
        for i in range(n):
            (ax, ay), (bx, by) = self.pts[i], self.pts[(i + 1) % n]
            if ax == bx:
                if abs(ax - x) < 1e-6:
                    ys += [ay, by]
                continue
            if min(ax, bx) - 1e-6 <= x <= max(ax, bx) + 1e-6:
                ys.append(ay + (by - ay) * (x - ax) / (bx - ax))
        if not ys:
            return (self.y0, self.y1)
        return (min(ys), max(ys))


@dataclass
class Curve:
    x0: float
    y0: float
    x1: float
    y1: float
    start: tuple
    end: tuple


@dataclass
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Page:
    index: int
    width: float
    height: float
    glyphs: list = field(default_factory=list)
    chars: list = field(default_factory=list)
    segs: list = field(default_factory=list)
    polys: list = field(default_factory=list)
    curves: list = field(default_factory=list)
    rects: list = field(default_factory=list)
    staff_paths: list = field(default_factory=list)  # 5 本線をまとめて描いたパス [(x0, x1, [y*5], width)]


def _base_font(name: str) -> str:
    return name.split("+")[-1]


def extract_page(page: pymupdf.Page, index: int) -> Page:
    out = Page(index, page.rect.width, page.rect.height)
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES)
    for b in raw["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                font = _base_font(s["font"])
                is_music = font.startswith(MUSIC_FONTS) and font not in MUSIC_TEXT_FONTS
                is_mtext = font in MUSIC_TEXT_FONTS
                for ch in s["chars"]:
                    o = ord(ch["c"])
                    x0, y0, x1, y1 = ch["bbox"]
                    ox, oy = ch["origin"]
                    if (is_music or is_mtext) and 0xE000 <= o <= 0xF8FF:
                        out.glyphs.append(Glyph(o, ox, oy, x1 - x0, s["size"], is_mtext))
                    else:
                        out.chars.append(Char(ch["c"], x0, x1, oy, s["size"], font, y0, y1))
    for pi, dr in enumerate(page.get_drawings()):
        items = dr["items"]
        kinds = "".join(it[0] for it in items)
        fill = dr.get("fill") is not None and "f" in dr["type"]
        width = dr.get("width") or 0.0
        if fill and kinds and set(kinds) == {"l"}:
            pts = [(it[1].x, it[1].y) for it in items]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            out.polys.append(Poly(pts, min(xs), min(ys), max(xs), max(ys)))
            continue
        if "c" in kinds:
            first, last = items[0], items[len(items) // 2 - 1] if len(items) >= 2 else items[0]
            r = dr["rect"]
            out.curves.append(Curve(r.x0, r.y0, r.x1, r.y1, (first[1].x, first[1].y), (last[-1].x, last[-1].y)))
            continue
        if kinds == "re" or kinds == "qu":
            r = dr["rect"]
            if fill and "s" not in dr["type"]:
                # 塗りつぶし矩形は線分として扱う (太い小節線・連桁など)
                if r.width > r.height:
                    out.polys.append(Poly([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)], r.x0, r.y0, r.x1, r.y1))
                else:
                    out.segs.append(Seg((r.x0 + r.x1) / 2, r.y0, (r.x0 + r.x1) / 2, r.y1, r.width, pi))
            else:
                out.rects.append(Rect(r.x0, r.y0, r.x1, r.y1))
            continue
        if "s" not in dr["type"]:
            continue
        lines = [it for it in items if it[0] == "l"]
        for it in lines:
            a, b = it[1], it[2]
            x0, x1 = (a.x, b.x) if a.x <= b.x else (b.x, a.x)
            y0, y1 = (a.y, b.y) if a.y <= b.y else (b.y, a.y)
            out.segs.append(Seg(x0, y0, x1, y1, width, pi))
        if len(lines) == 5 and len(items) == 5:
            hs = [it for it in lines if abs(it[1].y - it[2].y) < 0.05]
            if len(hs) == 5:
                ys = sorted(it[1].y for it in hs)
                gaps = [ys[i + 1] - ys[i] for i in range(4)]
                x0 = min(min(it[1].x, it[2].x) for it in hs)
                x1 = max(max(it[1].x, it[2].x) for it in hs)
                if max(gaps) - min(gaps) < 0.1 and min(gaps) > 0.5:
                    out.staff_paths.append((x0, x1, ys, width))
    return out


def extract(path: str) -> list[Page]:
    doc = pymupdf.open(path)
    return [extract_page(p, i) for i, p in enumerate(doc)]
