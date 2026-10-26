"""五線・段 (システム)・小節線の検出。"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from .extract import Page, Seg


@dataclass
class Staff:
    ys: list          # 5 本の線の y
    x0: float
    x1: float
    line_width: float
    index: int = -1   # 段内での番号

    @property
    def top(self):
        return self.ys[0]

    @property
    def bottom(self):
        return self.ys[4]

    @property
    def sp(self):
        return (self.ys[4] - self.ys[0]) / 4

    @property
    def mid(self):
        return self.ys[2]

    def pos(self, y: float) -> int:
        """五線上の位置 (第 5 線 = 0, 下へ 1 ずつ半間)。"""
        return round((y - self.top) / (self.sp / 2))


@dataclass
class Barline:
    x: float          # 代表 x (最初の線)
    x_end: float      # 最後の線
    kind: str         # normal / double / end / start-repeat / end-repeat
    lines: list = field(default_factory=list)


@dataclass
class System:
    page: int
    staves: list
    barlines: list = field(default_factory=list)  # 小節の区切り (左端の段頭線は含まない)
    x0: float = 0.0
    x1: float = 0.0

    @property
    def top(self):
        return self.staves[0].top

    @property
    def bottom(self):
        return self.staves[-1].bottom


def find_staves(page: Page) -> list[Staff]:
    rows: list[Staff] = []
    for x0, x1, ys, width in sorted(page.staff_paths, key=lambda s: (round(s[2][0], 0), s[0])):
        for r in rows:
            if abs(r.ys[0] - ys[0]) < 0.3 and abs(r.ys[4] - ys[4]) < 0.3 and x0 <= r.x1 + 2 and x1 >= r.x0 - 2:
                r.x0 = min(r.x0, x0)
                r.x1 = max(r.x1, x1)
                break
        else:
            rows.append(Staff(list(ys), x0, x1, width))
    rows.sort(key=lambda r: r.top)
    return rows


def stem_width(pages: list[Page]) -> float:
    """符幹の太さ (文書全体で最も多い縦線の太さ)。"""
    c = Counter(round(s.width, 2) for page in pages for s in page.segs if s.vertical and s.width > 0)
    return c.most_common(1)[0][0] if c else 0.3


def find_systems(page: Page, sw: float | None = None) -> list[System]:
    staves = find_staves(page)
    if not staves:
        return []
    parent = list(range(len(staves)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for s in page.segs:
        if not s.vertical:
            continue
        hit = [i for i, st in enumerate(staves)
               if st.x0 - 3 * st.sp <= s.x0 <= st.x0 + 1.5 * st.sp and s.y0 <= st.bottom + 0.5 and s.y1 >= st.top - 0.5]
        for i in hit[1:]:
            parent[find(i)] = find(hit[0])
    groups: dict[int, list] = {}
    for i, st in enumerate(staves):
        groups.setdefault(find(i), []).append(st)
    systems = []
    for g in groups.values():
        g.sort(key=lambda st: st.top)
        for k, st in enumerate(g):
            st.index = k
        systems.append(System(page.index, g, x0=min(st.x0 for st in g), x1=max(st.x1 for st in g)))
    systems.sort(key=lambda s: s.top)
    if sw is None:
        sw = stem_width([page])
    for sysm in systems:
        sysm.barlines = _find_barlines(page, sysm, sw)
    return systems


def _find_barlines(page: Page, sysm: System, sw: float) -> list[Barline]:
    sp = statistics.median(st.sp for st in sysm.staves)
    cands: list[tuple[float, float, int]] = []  # (x, width, staff index)
    for s in page.segs:
        if not s.vertical or s.width < sw * 1.1:
            continue
        if s.x0 < sysm.x0 + 0.5 * sp or s.x0 > sysm.x1 + 1:
            continue
        for st in sysm.staves:
            if s.y0 <= st.top + 0.3 and s.y1 >= st.bottom - 0.3:
                cands.append((s.x0, s.width, st.index))
    cands.sort()
    clusters: list[list] = []
    for c in cands:
        if clusters and c[0] - clusters[-1][-1][0] < 1.2 * sp:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    out = []
    need = max(1, (len(sysm.staves) + 1) // 2)
    for cl in clusters:
        if len({c[2] for c in cl}) < need:
            continue
        xs = sorted({round(c[0], 1) for c in cl})
        widths = {}
        for c in cl:
            widths.setdefault(round(c[0], 1), c[1])
        merged = []
        for x in xs:
            if merged and x - merged[-1] < 0.25:
                continue
            merged.append(x)
        kind = "normal"
        if len(merged) >= 2:
            wmax = max(widths.values())
            kind = "end" if wmax > 2.5 * min(widths.values()) else "double"
        out.append(Barline(merged[0], merged[-1], kind, merged))
    # 繰り返し記号 (点) の判定
    for b in out:
        dots = [g for g in page.glyphs if g.code in (0xE043, 0xE044) and sysm.top - sp <= g.y <= sysm.bottom + sp]
        left = any(b.x - 2 * sp <= g.x <= b.x for g in dots)
        right = any(b.x_end <= g.x <= b.x_end + 2 * sp for g in dots)
        if right and not left:
            b.kind = "start-repeat"
        elif left and not right:
            b.kind = "end-repeat"
        elif left and right:
            b.kind = "end-start-repeat"
    return out
