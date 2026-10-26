"""MuseScore 書き出しのベクター PDF から楽譜を認識して IR を作る。"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from . import ir, smufl
from .extract import Char, Glyph, Page, Seg, extract
from .layout import Barline, Staff, System, find_systems, stem_width

KEY_SHARPS = "FCGDAEB"
KEY_FLATS = "BEADGCF"
STEPS = "CDEFGAB"
BEAM_NAMES = ["quarter", "eighth", "16th", "32nd", "64th", "128th"]


# ---------------------------------------------------------------- データ構造

@dataclass
class Head:
    g: Glyph
    staff: Staff
    pos: int
    kind: str           # black / half / whole / breve
    shape: str          # normal / cross / ...
    grace: bool = False
    alter: int | None = None   # 明示された臨時記号
    tie_out: bool = False
    tie_in: "Head | None" = None
    pitch: str | None = None
    drum: int | None = None
    diatonic: int = 0

    @property
    def x(self):
        return self.g.x

    @property
    def x1(self):
        return self.g.x1

    @property
    def cx(self):
        return self.g.cx

    @property
    def y(self):
        return self.g.y


@dataclass
class Event:
    kind: str                  # chord / rest
    staff: Staff
    x: float                   # 左端
    x1: float
    y: float = 0.0
    heads: list = field(default_factory=list)
    base: str = "quarter"
    dots: int = 0
    stem: Seg | None = None
    stem_dir: str | None = None
    voice: int = 0
    tuplet: tuple | None = None   # (group id, actual, normal)
    onset: Fraction | None = None
    lyrics: list = field(default_factory=list)
    measure_rest: bool = False

    @property
    def cx(self):
        if self.heads:
            # 符幹側の符頭 (ずらされていない符頭) の中心
            xs = Counter(round(h.x, 1) for h in self.heads)
            x = xs.most_common(1)[0][0]
            h = min(self.heads, key=lambda h: abs(h.x - x))
            return h.cx
        return (self.x + self.x1) / 2

    def length(self) -> Fraction:
        d = ir.DURATIONS[self.base] * (2 - Fraction(1, 2**self.dots))
        if self.tuplet:
            d = d * Fraction(self.tuplet[2], self.tuplet[1])
        return d


@dataclass
class TextWord:
    text: str
    x0: float
    x1: float
    y: float
    size: float
    chars: list

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2


# ---------------------------------------------------------------- 補助

def _nearest_staff(staves: list[Staff], y: float) -> tuple[Staff, float]:
    best, bd = None, 1e9
    for st in staves:
        if st.top <= y <= st.bottom:
            d = 0.0
        else:
            d = min(abs(y - st.top), abs(y - st.bottom))
        if d < bd:
            best, bd = st, d
    return best, bd


def _key_alter(key: int, step: str) -> int:
    if key > 0 and step in KEY_SHARPS[:key]:
        return 1
    if key < 0 and step in KEY_FLATS[:-key]:
        return -1
    return 0


def _dur_from_length(length: Fraction) -> tuple[str, int] | None:
    for name, base in ir.DURATIONS.items():
        for dots in range(3):
            if base * (2 - Fraction(1, 2**dots)) == length:
                return name, dots
    return None


_CHORD_RE = re.compile(
    r"^\(?[A-G](?:b|#|♭|♯)?"
    r"(?:maj|Maj|MAJ|min|m|M|dim|aug|sus|add|alt|omit|no|o|ø|°|Δ|\+|-|[0-9]|\(|\)|,|b|#|♭|♯|\^)*"
    r"(?:/[A-G](?:b|#|♭|♯)?)?\)?$"
)


def is_chord_symbol(s: str) -> bool:
    return bool(_CHORD_RE.match(s))


def _load_drumset() -> dict:
    """(line, head shape) -> MIDI ピッチ。"""
    text = (Path(__file__).parent / "data" / "drumset.xml").read_text(encoding="utf-8")
    table = {}
    for m in re.finditer(r'<Drum pitch="(\d+)">\s*<head>(\w+)</head>\s*<line>(-?\d+)</line>', text):
        pitch, head, line = int(m.group(1)), m.group(2), int(m.group(3))
        shape = {"cross": "cross", "normal": "normal", "diamond": "diamond"}.get(head, head)
        table.setdefault((line, shape), pitch)
    # 見た目が同じ楽器は、手元の楽譜で使用頻度が高い方を既定にする
    table.update({(7, "normal"): 35, (3, "normal"): 38, (5, "normal"): 41, (0, "normal"): 48, (-3, "cross"): 52})
    return table


DRUMSET = _load_drumset()


# ---------------------------------------------------------------- 文字のまとまり

def group_words(chars: list[Char]) -> list[TextWord]:
    """同じベースライン上の文字を、隙間で区切って単語にまとめる。"""
    lines: list[list[Char]] = []
    for c in sorted(chars, key=lambda c: (c.y, c.x0)):
        for ln in lines:
            if abs(ln[0].y - c.y) < 0.3 * max(c.size, ln[0].size) and abs(ln[0].size - c.size) < 0.3 * c.size:
                ln.append(c)
                break
        else:
            lines.append([c])
    words = []
    for ln in lines:
        ln.sort(key=lambda c: c.x0)
        cur: list[Char] = []
        for c in ln:
            if c.c.isspace():
                if cur:
                    words.append(cur)
                cur = []
                continue
            if cur and c.x0 - cur[-1].x1 > 0.07 * c.size:
                words.append(cur)
                cur = []
            cur.append(c)
        if cur:
            words.append(cur)
    out = []
    for w in words:
        out.append(TextWord("".join(c.c for c in w), w[0].x0, w[-1].x1, statistics.median(c.y for c in w),
                            max(c.size for c in w), w))
    return out


# ---------------------------------------------------------------- 本体

class Recognizer:
    def __init__(self, pages: list[Page]):
        self.pages = pages
        self.sw = stem_width(pages)
        self.systems: list[tuple[Page, System]] = []
        for p in pages:
            for s in find_systems(p, self.sw):
                self.systems.append((p, s))
        counts = Counter(len(s.staves) for _, s in self.systems)
        self.nstaves = counts.most_common(1)[0][0] if counts else 0
        self.warnings: list[dict] = []
        # 五線ごとの状態
        self.clef = ["G"] * self.nstaves
        self.key = [0] * self.nstaves
        self.timesig = "4/4"
        self.is_drum = [False] * self.nstaves
        self.pending_ties: dict[tuple[int, int], Head] = {}  # (staff, pos) -> 段をまたぐタイの始点

    # ------------------------------------------------------------ ページ全体
    def run(self) -> dict:
        score = {"format": "pdf2mscz-ir/1", "meta": {}, "parts": [], "measures": []}
        if not self.systems:
            score["warnings"] = [{"msg": "五線が見つかりません (ベクター PDF ではない可能性があります)"}]
            return score
        self._read_header(score)
        for page, sysm in self.systems:
            if len(sysm.staves) != self.nstaves:
                self.warnings.append({"page": page.index + 1, "msg": f"段の五線数が {len(sysm.staves)} (想定 {self.nstaves})。この段はスキップしました"})
                continue
            self._system(page, sysm, score)
        # 拍子・調は最初の小節に必ず入れる
        if score["measures"]:
            m0 = score["measures"][0]
            m0.setdefault("timeSig", "4/4")
            m0.setdefault("key", 0)
        for i, part in enumerate(score["parts"]):
            if self.is_drum[i]:
                part["instrument"] = "drumset"
        if self.warnings:
            score["warnings"] = self.warnings
        return score

    def _read_header(self, score: dict):
        page, sysm = self.systems[0]
        sp = sysm.staves[0].sp
        # タイトル類: 最初の段より上にある文字
        top_chars = [c for c in page.chars if c.bottom < sysm.top - 4 * sp]
        lines: dict[float, list] = {}
        for w in group_words(top_chars):
            if re.fullmatch(r"\d+", w.text) and w.size < 9:
                continue
            for y in lines:
                if abs(y - w.y) < 0.3 * w.size:
                    lines[y].append(w)
                    break
            else:
                lines[w.y] = [w]
        rows = []
        for y, ws in sorted(lines.items()):
            ws.sort(key=lambda w: w.x0)
            text = " ".join(w.text for w in ws)
            rows.append((y, ws[0].x0, ws[-1].x1, max(w.size for w in ws), text))
        meta = score["meta"]
        if rows:
            big = max(rows, key=lambda r: r[3])
            if big[3] >= 14:
                meta["title"] = big[4]
            composer, lyricist, subtitle = [], [], []
            for r in rows:
                if r is big and "title" in meta:
                    continue
                if "=" in r[4] and len(r[4]) < 12:
                    continue
                center = (r[1] + r[2]) / 2
                if r[2] > page.width * 0.75 and r[1] > page.width * 0.4:
                    composer.append(r[4])
                elif r[1] < page.width * 0.25 and r[2] < page.width * 0.6:
                    lyricist.append(r[4])
                elif abs(center - page.width / 2) < page.width * 0.1:
                    subtitle.append(r[4])
            if composer:
                meta["composer"] = "\n".join(composer)
            if lyricist:
                meta["lyricist"] = "\n".join(lyricist)
            if subtitle:
                meta["subtitle"] = "\n".join(subtitle)
        # パート名: 最初の段の左側
        names = [[] for _ in range(self.nstaves)]
        if len(sysm.staves) == self.nstaves:
            left = [c for c in page.chars if c.x1 < sysm.x0 - 0.3 * sp and sysm.top - 3 * sp < c.y < sysm.bottom + 3 * sp]
            for w in group_words(left):
                st, _ = _nearest_staff(sysm.staves, w.y - w.size * 0.3)
                names[st.index].append(w)
        for k in range(self.nstaves):
            ws = sorted(names[k], key=lambda w: (round(w.y), w.x0))
            lines = []
            for w in ws:
                if lines and abs(lines[-1][0] - w.y) < 0.3 * w.size:
                    lines[-1][1].append(w.text)
                else:
                    lines.append((w.y, [w.text]))
            texts = [" ".join(t) for _, t in lines]
            name = texts[0] if texts else f"Part {k + 1}"
            score["parts"].append({"name": name, "longName": "\n".join(texts) if texts else name, "staves": 1})

    # ------------------------------------------------------------ 段
    def _system(self, page: Page, sysm: System, score: dict):
        staves = sysm.staves
        sp = statistics.median(st.sp for st in staves)
        sp_size = 4 * sp
        x_lo, x_hi = sysm.x0 - 0.5 * sp, sysm.x1 + 0.5 * sp
        y_lo, y_hi = sysm.top - 12 * sp, sysm.bottom + 12 * sp
        if score.get("spatium") is None:
            score["spatium"] = sp * 25.4 / 72  # mm

        def mine(x, y):
            return x_lo <= x <= x_hi and y_lo <= y <= y_hi

        glyphs = [g for g in page.glyphs if mine(g.x, g.y)]
        # 他の段に近い要素は除外
        other = [s for p2, s in self.systems if p2 is page and s is not sysm]

        def closest_here(y):
            _, d = _nearest_staff(staves, y)
            for s in other:
                _, d2 = _nearest_staff(s.staves, y)
                if d2 < d:
                    return False
            return True

        glyphs = [g for g in glyphs if closest_here(g.y)]
        segs = [s for s in page.segs if x_lo <= s.x0 <= x_hi and y_lo <= s.y0 <= y_hi and closest_here((s.y0 + s.y1) / 2)]
        polys = [p for p in page.polys if x_lo <= p.x0 <= x_hi and y_lo <= p.y0 <= y_hi and closest_here((p.y0 + p.y1) / 2)]

        # 小節の x 範囲
        bounds = []
        start = sysm.x0
        for b in sysm.barlines:
            bounds.append((start, b.x, b))
            start = b.x_end
        nmeas = len(bounds)

        # ---- 音部記号・拍子・調号 (グリフ)
        clefs = []  # (staff idx, x, clef)
        timesigs: dict[int, list] = {}
        heads: list[Head] = []
        rests: list[Event] = []
        accs: list[Glyph] = []
        dots: list[Glyph] = []
        flags: list[Glyph] = []
        for g in glyphs:
            if g.text_font:
                continue
            st, d = _nearest_staff(staves, g.y)
            if g.code in smufl.CLEFS:
                c = smufl.CLEFS[g.code]
                if c == "C":
                    c = {8: "C1", 6: "C2", 4: "C3", 2: "C4", 0: "C5"}.get(st.pos(g.y), "C3")
                clefs.append((st.index, g.x, c))
            elif g.code in smufl.TIMESIG_DIGITS or g.code in (smufl.TIMESIG_COMMON, smufl.TIMESIG_CUT):
                timesigs.setdefault(st.index, []).append(g)
            elif g.code in smufl.NOTEHEAD_KIND:
                kind, shape = smufl.NOTEHEAD_KIND[g.code]
                heads.append(Head(g, st, st.pos(g.y), kind, shape, grace=g.size < 0.85 * sp_size))
            elif g.code in smufl.RESTS:
                if d > 6 * sp:
                    continue
                ev = Event("rest", st, g.x, g.x1, g.y, base=smufl.RESTS[g.code])
                rests.append(ev)
            elif g.code in smufl.ACCIDENTALS:
                accs.append(g)
            elif g.code == smufl.AUG_DOT:
                dots.append(g)
            elif g.code in smufl.FLAGS:
                flags.append(g)

        # ---- 符幹と和音
        stems = [s for s in segs if s.vertical and abs(s.width - self.sw) < 0.02 + 0.05 * self.sw and s.y1 - s.y0 > 0.8 * sp]
        head_stem: dict[int, Seg] = {}
        for h in heads:
            best, bd = None, 0.45 * sp
            for s in stems:
                if not (s.y0 - 0.7 * sp <= h.y <= s.y1 + 0.7 * sp):
                    continue
                d = min(abs(h.x1 - self.sw / 2 - s.x0), abs(h.x + self.sw / 2 - s.x0))
                if d < bd:
                    best, bd = s, d
            if best is not None:
                head_stem[id(h)] = best
        chords: list[Event] = []
        by_stem: dict[int, list[Head]] = {}
        stem_of: dict[int, Seg] = {}
        loose = []
        for h in heads:
            s = head_stem.get(id(h))
            if s is None:
                loose.append(h)
            else:
                by_stem.setdefault(id(s), []).append(h)
                stem_of[id(s)] = s
        for sid, hs in by_stem.items():
            s = stem_of[sid]
            # 符頭が符幹の下端側にあれば上向き
            ys = [h.y for h in hs]
            up = abs(max(ys) - s.y1) < abs(min(ys) - s.y0)
            ev = Event("chord", hs[0].staff, min(h.x for h in hs), max(h.x1 for h in hs), heads=hs, stem=s,
                       stem_dir="up" if up else "down")
            chords.append(ev)
        loose.sort(key=lambda h: h.x)
        for h in loose:
            for ev in chords:
                if ev.stem is None and ev.staff is h.staff and abs(ev.heads[0].cx - h.cx) < 0.6 * h.g.w and ev.heads[0].grace == h.grace:
                    ev.heads.append(h)
                    ev.x = min(ev.x, h.x)
                    ev.x1 = max(ev.x1, h.x1)
                    break
            else:
                chords.append(Event("chord", h.staff, h.x, h.x1, heads=[h]))

        # ---- 音価
        beam_polys = []
        for p in polys:
            if p.x1 - p.x0 < 0.4 * sp:
                continue
            mid = (p.x0 + p.x1) / 2
            t, b = p.y_at(mid)
            if 0.25 * sp <= b - t <= 0.9 * sp:
                beam_polys.append(p)
        beam_groups: dict[int, int] = {}  # id(chord) -> 連桁グループ番号 (主連桁)
        for ev in chords:
            kind = ev.heads[0].kind
            if kind in ("whole", "breve", "half"):
                ev.base = kind
                if kind != "half" or ev.stem is None:
                    continue
            if ev.stem is None:
                ev.base = "quarter" if kind == "black" else kind
                continue
            s = ev.stem
            n = 0
            seen = []
            for p in beam_polys:
                if not (p.x0 - 0.15 * sp <= s.x0 <= p.x1 + 0.15 * sp):
                    continue
                t, b = p.y_at(min(max(s.x0, p.x0), p.x1))
                if b < s.y0 - 0.4 * sp or t > s.y1 + 0.4 * sp:
                    continue
                if any(abs(t - t2) < 0.2 * sp for t2 in seen):
                    continue
                seen.append(t)
                n += 1
                # 最も外側の連桁でグループ化
            if seen:
                outer = min(seen) if ev.stem_dir == "up" else max(seen)
                for p in beam_polys:
                    if p.x0 - 0.15 * sp <= s.x0 <= p.x1 + 0.15 * sp:
                        t, _ = p.y_at(min(max(s.x0, p.x0), p.x1))
                        if abs(t - outer) < 0.05 * sp:
                            beam_groups[id(ev)] = p
            for f in flags:
                if abs(f.x - s.x0) < 0.6 * sp and s.y0 - sp <= f.y <= s.y1 + sp:
                    n = max(n, smufl.FLAGS[f.code])
            if kind == "black":
                ev.base = BEAM_NAMES[min(n, len(BEAM_NAMES) - 1)]
            elif kind == "half" and n:
                # 2分音符の符頭に連桁 = トレモロ等。そのまま 2 分扱い
                ev.base = "half"
        # 装飾音は捨てる
        chords = [ev for ev in chords if not ev.heads[0].grace]

        # ---- 付点
        allev = chords + rests
        for d in dots:
            best, bd = None, 2.2 * sp
            for ev in allev:
                if ev.staff is not _nearest_staff(staves, d.y)[0]:
                    continue
                if ev.kind == "chord":
                    if not any(abs(h.y - d.y) <= 0.8 * sp for h in ev.heads):
                        continue
                    right = max(h.x1 for h in ev.heads)
                else:
                    if abs(ev.y - d.y) > 3 * sp:
                        continue
                    right = ev.x1
                gap = d.x - right
                if -0.2 * sp <= gap < bd:
                    best, bd = ev, gap
            if best is not None:
                best.dots += 1
        for ev in allev:
            if ev.dots and ev.kind == "chord":
                # 和音の各符頭に付点がつくので、列 (x) の数を数える
                xs = sorted({round(d.x, 1) for d in dots if any(abs(h.y - d.y) <= 0.8 * sp for h in ev.heads)
                             and 0 <= d.x - max(h.x1 for h in ev.heads) < 2.2 * sp})
                ev.dots = 1 if len(xs) <= 1 else 2
            elif ev.dots > 2:
                ev.dots = 1

        # ---- 臨時記号
        # 隣り合う臨時記号を塊 (チェーン) にまとめ、符頭のすぐ左に付く塊だけを臨時記号とする。
        # それ以外 (段頭・小節頭の塊) は調号として扱う。
        free_accs = []
        chains: list[list[Glyph]] = []
        for a in sorted(accs, key=lambda a: (_nearest_staff(staves, a.y)[0].index, a.x)):
            st = _nearest_staff(staves, a.y)[0]
            for ch in chains:
                if _nearest_staff(staves, ch[0].y)[0] is st and a.x - max(c.x1 for c in ch) < 0.5 * sp \
                        and any(abs(a.y - c.y) < 4 * sp for c in ch):
                    ch.append(a)
                    break
            else:
                chains.append([a])
        for ch in chains:
            right = max(c.x1 for c in ch)
            ys = [c.y for c in ch]
            near = [h for h in heads if -0.5 * sp <= h.x - right < 0.8 * sp and min(ys) - 0.3 * sp <= h.y <= max(ys) + 0.3 * sp]
            if not near:
                free_accs += ch
                continue
            for a in ch:
                cands = [h for h in heads if abs(h.y - a.y) < 0.3 * sp and -0.5 * sp <= h.x - right < 0.8 * sp + 2 * h.g.w]
                if cands:
                    h = min(cands, key=lambda h: h.x - a.x1)
                    if h.alter is None:
                        h.alter = smufl.ACCIDENTALS[a.code]

        # ---- 連符
        self._tuplets(page, sysm, chords, rests, beam_groups, segs, sp)

        # ---- タイ
        self._ties(page, sysm, heads, sp)

        # ---- 小節ごとに分配
        events_by = [[[] for _ in staves] for _ in range(nmeas)]
        for ev in chords + rests:
            x = ev.cx
            for j, (a, b, _) in enumerate(bounds):
                if a - 0.2 * sp <= x < b:
                    events_by[j][ev.staff.index].append(ev)
                    break

        # 音部記号・拍子・調号の位置を小節に
        def measure_of(x):
            for j, (a, b, _) in enumerate(bounds):
                if a - 0.5 * sp <= x < b:
                    return j
            return None

        def first_event_x(j, k):
            evs = events_by[j][k]
            return min((e.x for e in evs), default=bounds[j][1])

        def is_courtesy(j, k, x):
            # 段の最後の小節で、全イベントより後ろにある記号は予告 (次の段で再掲される)
            if j != nmeas - 1:
                return False
            evs = events_by[j][k]
            return bool(evs) and x > max(e.x1 for e in evs)

        # 変更 (音部記号・調号): (j, k) -> [(x, 種類, 値)]。x=None は小節頭
        changes: dict[tuple, list] = {}

        def last_event_x(j, k):
            evs = events_by[j][k]
            return max((e.x1 for e in evs), default=None)

        for k, x, c in clefs:
            j = measure_of(x)
            if j is None:
                continue
            last = last_event_x(j, k)
            if last is None or x <= first_event_x(j, k):
                changes.setdefault((j, k), []).append((None, "clef", c))
            elif x > last:
                # 小節線の直前の音部記号は次の小節から (段末なら次の段で再掲される)
                if j + 1 < nmeas:
                    changes.setdefault((j + 1, k), []).append((None, "clef", c))
            else:
                changes.setdefault((j, k), []).append((x, "clef", c))

        ts_change = {}
        for k, gs in timesigs.items():
            gs.sort(key=lambda g: g.x)
            groups = []
            for g in gs:
                if groups and g.x - groups[-1][-1].x < 2.2 * sp:
                    groups[-1].append(g)
                else:
                    groups.append([g])
            st = staves[k]
            for grp in groups:
                j = measure_of(grp[0].x)
                if j is None or is_courtesy(j, k, grp[0].x):
                    continue
                if grp[0].code == smufl.TIMESIG_COMMON:
                    ts = "4/4"
                elif grp[0].code == smufl.TIMESIG_CUT:
                    ts = "2/2"
                else:
                    num = "".join(str(smufl.TIMESIG_DIGITS[g.code]) for g in sorted(grp, key=lambda g: g.x) if g.y < st.mid)
                    den = "".join(str(smufl.TIMESIG_DIGITS[g.code]) for g in sorted(grp, key=lambda g: g.x) if g.y > st.mid)
                    if not num or not den:
                        continue
                    ts = f"{num}/{den}"
                ts_change.setdefault(j, ts)

        free_accs.sort(key=lambda a: (_nearest_staff(staves, a.y)[0].index, a.x))
        acc_groups: list[list[Glyph]] = []
        for a in free_accs:
            st, _ = _nearest_staff(staves, a.y)
            if acc_groups and _nearest_staff(staves, acc_groups[-1][-1].y)[0] is st and a.x - acc_groups[-1][-1].x1 < 1.5 * sp:
                acc_groups[-1].append(a)
            else:
                acc_groups.append([a])
        for grp in acc_groups:
            st, _ = _nearest_staff(staves, grp[0].y)
            j = measure_of(grp[0].x)
            if j is None or is_courtesy(j, st.index, grp[0].x):
                continue
            vals = [smufl.ACCIDENTALS[a.code] for a in grp]
            sharps = sum(1 for v in vals if v > 0)
            flats = sum(1 for v in vals if v < 0)
            key = sharps if sharps else -flats
            if grp[0].x > first_event_x(j, st.index):
                last = last_event_x(j, st.index)
                if len(grp) < 2 and key != 0 or last is None or grp[0].x > last:
                    continue  # 浮いた臨時記号は無視
                changes.setdefault((j, st.index), []).append((grp[0].x, "key", key))
            else:
                changes.setdefault((j, st.index), []).append((None, "key", key))

        # 段頭では調号が必ず再掲されるので、無ければハ長調 (調号なし)
        for k in range(len(staves)):
            if nmeas and not any(t == "key" and x is None for x, t, _ in changes.get((0, k), [])):
                changes.setdefault((0, k), []).append((None, "key", 0))

        # ---- 小節ごとに処理
        self._cur_chords = chords
        texts = self._texts(page, sysm, sp, bounds)
        for j in range(nmeas):
            a, b, bar = bounds[j]
            m = {"staves": []}
            if j in ts_change and ts_change[j] != self.timesig:
                self.timesig = ts_change[j]
                m["timeSig"] = self.timesig
            elif not score["measures"]:
                m["timeSig"] = ts_change.get(j, self.timesig)
                self.timesig = m["timeSig"]
            tslen = ir.parse_timesig(self.timesig)
            mnum = len(score["measures"]) + 1
            totals = []
            # 調号は最初の音高五線を代表に
            ref = next((k for k in range(len(staves)) if not self.is_drum[k] and not any(
                t == "clef" and v == "PERC" for _, t, v in changes.get((j, k), []))), 0)
            for k, st in enumerate(staves):
                stir = {}
                mids = []
                for x, t, v in changes.get((j, k), []):
                    if t == "clef" and v == "PERC":
                        self.is_drum[k] = True
                    if x is not None:
                        mids.append((x, t, v))
                    elif t == "clef":
                        if v != self.clef[k] or mnum == 1:
                            stir["clef"] = v
                        self.clef[k] = v
                    else:
                        self.key[k] = v
                        if k == ref:
                            m["key"] = v
                evs = sorted(events_by[j][k], key=lambda e: e.cx)
                voices = self._voices(evs, st, sp, tslen, mnum, k)
                for v in voices:
                    _fix_tuplet_ratio(v, tslen)
                self._pitches(evs, k, sorted(mids, key=lambda c: c[0]))
                stir["voices"] = [self._to_ir(v, tslen) for v in voices]
                for x, t, v in sorted(mids, key=lambda c: c[0]):
                    after = [e for e in evs if e.x > x and e.onset is not None]
                    at = min(after, key=lambda e: e.x).onset if after else tslen
                    stir.setdefault("changes", []).append({"at": str(at), t: v})
                    if t == "key" and k == ref:
                        m["key"] = v
                        m["keyAt"] = str(at)
                totals.append(sum((e.length() for e in voices[0]), Fraction(0)) if voices and voices[0] else None)
                m["staves"].append(stir)
            # 弱起・不完全小節
            valid = [t for t in totals if t]
            if valid:
                common = Counter(valid).most_common(1)[0][0]
                if common != tslen and common < tslen and Counter(valid)[common] >= max(1, len(valid) // 2):
                    m["len"] = f"{common.numerator}/{common.denominator}"
            mlen = ir.parse_timesig(m["len"]) if "len" in m else tslen
            for k in range(len(staves)):
                for vi, v in enumerate(m["staves"][k]["voices"]):
                    tot = ir.voice_length(v, mlen)
                    if v and tot != mlen and not (vi > 0 and tot < mlen):
                        self.warnings.append({
                            "page": page.index + 1, "measure": mnum, "staff": k + 1, "voice": vi + 1,
                            "msg": f"音価の合計 {tot} が小節の長さ {mlen} と一致しません"})
            # 小節線
            if bar.kind in ("double", "end"):
                m["barline"] = bar.kind
            if bar.kind in ("end-repeat", "end-start-repeat"):
                m["repeatEnd"] = True
            prev_bar = bounds[j - 1][2] if j > 0 else None
            if prev_bar is not None and prev_bar.kind in ("start-repeat", "end-start-repeat"):
                m["repeatStart"] = True
            if j == 0 and self._starts_with_repeat(page, sysm, sp):
                m["repeatStart"] = True
            # テキスト類
            for item in texts.get(j, []):
                kind = item[0]
                if kind == "tempo":
                    m["tempo"] = item[1]
                elif kind == "rehearsal":
                    m["rehearsal"] = item[1]
                elif kind == "harmony":
                    _, k, x, name = item
                    at = self._onset_at(events_by[j], x, k)
                    m["staves"][k].setdefault("harmony", []).append({"at": str(at), "name": name})
                elif kind == "lyrics":
                    pass  # _texts 内で各イベントに付与済み
            if j == nmeas - 1:
                m["lineBreak"] = True
            score["measures"].append(m)
        # 歌詞はイベントに付与されているので IR 化後に反映済み

    def _starts_with_repeat(self, page, sysm, sp) -> bool:
        dots = [g for g in page.glyphs if g.code in (0xE043, 0xE044) and sysm.top - sp <= g.y <= sysm.bottom + sp]
        return any(sysm.x0 <= g.x <= sysm.x0 + 12 * sp for g in dots) and not any(abs(b.x - sysm.x0) < 12 * sp for b in sysm.barlines)

    # ------------------------------------------------------------ 声部
    def _voices(self, evs: list[Event], st: Staff, sp: float, tslen: Fraction, mnum: int, k: int) -> list[list[Event]]:
        if not evs:
            return [[]]
        cols: list[list[Event]] = []
        for e in evs:
            if cols and abs(e.cx - statistics.mean(c.cx for c in cols[-1])) < 0.7 * sp:
                cols[-1].append(e)
            else:
                cols.append([e])
        multi = any(len(c) > 1 for c in cols)

        def rest_shift(e):
            # 休符の標準位置からのずれ (負 = 上, 正 = 下)。全休符は第 4 線から下がる
            default = st.top + sp if e.base in ("whole", "breve") else st.mid
            return (e.y - default) / sp

        # 他の音と並ぶ全休符 (小節休符) や、上下にずらされた休符があれば多声部
        if not multi and len(evs) > 1:
            multi = any(e.kind == "rest" and abs(rest_shift(e)) >= 1.5 for e in evs)
        if not multi:
            for e in evs:
                e.voice = 0
            v = [evs]
        else:
            for c in cols:
                if len(c) == 1:
                    e = c[0]
                    if e.kind == "chord" and e.stem_dir == "down":
                        e.voice = 1
                    elif e.kind == "rest" and rest_shift(e) > 0.5:
                        e.voice = 1
                    else:
                        e.voice = 0
                    continue
                for e in c:
                    if e.kind == "chord" and e.stem_dir:
                        e.voice = 0 if e.stem_dir == "up" else 1
                    else:
                        others = [o for o in c if o is not e]
                        oy = statistics.mean(min(h.y for h in o.heads) if o.heads else o.y for o in others)
                        ey = min(h.y for h in e.heads) if e.heads else e.y
                        e.voice = 0 if ey < oy else 1
            v = [[e for e in evs if e.voice == 0], [e for e in evs if e.voice == 1]]
            if not v[0]:
                v = [v[1]]
                for e in v[0]:
                    e.voice = 0
            elif not v[1]:
                v = [v[0]]
        # 全休符 1 個だけなら小節休符
        for voice in v:
            if len(voice) == 1 and voice[0].kind == "rest" and voice[0].base == "whole" and not voice[0].dots:
                voice[0].measure_rest = True
        return v

    # ------------------------------------------------------------ 音高
    def _pitches(self, evs: list[Event], k: int, mids: list = ()):
        top = smufl.CLEF_TOP.get(self.clef[k], 38)
        state: dict[int, int] = {}
        mids = list(mids)
        for ev in sorted(evs, key=lambda e: e.x):
            while mids and mids[0][0] < ev.x:
                _, t, v = mids.pop(0)
                if t == "clef":
                    self.clef[k] = v
                    top = smufl.CLEF_TOP.get(v, 38)
                else:
                    self.key[k] = v
            for h in ev.heads:
                if self.is_drum[k]:
                    h.drum = DRUMSET.get((h.pos, h.shape)) or DRUMSET.get((h.pos, "normal")) or 38
                    continue
                d = top - h.pos
                h.diatonic = d
                step = STEPS[d % 7]
                octave = d // 7
                if h.tie_in is not None and h.tie_in.pitch and h.alter is None:
                    h.pitch = h.tie_in.pitch
                    continue
                if h.alter is not None:
                    alter = h.alter
                    state[d] = alter
                else:
                    alter = state.get(d, _key_alter(self.key[k], step))
                h.pitch = ir.pitch_name(step, alter, octave)
        for _, t, v in mids:
            if t == "clef":
                self.clef[k] = v
            else:
                self.key[k] = v

    # ------------------------------------------------------------ IR 化
    def _to_ir(self, voice: list[Event], tslen: Fraction) -> list:
        out = []
        t = Fraction(0)
        groups: dict[int, dict] = {}
        for ev in voice:
            ev.onset = t
            t += ev.length() if not ev.measure_rest else tslen
            if ev.kind == "rest":
                item = {"type": "rest", "duration": "measure" if ev.measure_rest else ev.base}
            else:
                notes = []
                for h in sorted(ev.heads, key=lambda h: -h.y):
                    n = {"drum": h.drum} if h.drum is not None else {"pitch": h.pitch}
                    if h.tie_out:
                        n["tie"] = True
                    if any(n == o for o in notes):
                        continue
                    notes.append(n)
                item = {"type": "chord", "duration": ev.base, "notes": notes}
                if ev.lyrics:
                    item["lyrics"] = ev.lyrics
            if ev.dots and not ev.measure_rest:
                item["dots"] = ev.dots
            if ev.tuplet:
                gid, actual, normal = ev.tuplet
                if gid not in groups or groups[gid] is not out[-1]:
                    groups[gid] = {"type": "tuplet", "actual": actual, "normal": normal, "events": []}
                    out.append(groups[gid])
                groups[gid]["events"].append(item)
            else:
                out.append(item)
        return out

    # ------------------------------------------------------------ 連符
    def _tuplets(self, page, sysm, chords, rests, beam_groups, segs, sp):
        nums = []
        for c in page.chars:
            if c.c.isdigit() and "Italic" in c.font and sysm.x0 - sp < c.x0 < sysm.x1 and sysm.top - 6 * sp < c.y < sysm.bottom + 6 * sp:
                nums.append(c)
        for g in page.glyphs:
            if 0xE880 <= g.code <= 0xE889 and sysm.x0 < g.x < sysm.x1 and sysm.top - 6 * sp < g.y < sysm.bottom + 6 * sp:
                nums.append(Char(str(g.code - 0xE880), g.x, g.x1, g.y, g.size, "tuplet", g.y - g.size / 2, g.y))
        # 隣接する数字をまとめる
        nums.sort(key=lambda c: (round(c.y), c.x0))
        merged = []
        for c in nums:
            if merged and abs(merged[-1][1] - c.y) < 0.5 and c.x0 - merged[-1][3] < 0.5 * c.size:
                merged[-1] = (merged[-1][0] + c.c, c.y, merged[-1][2], c.x1)
            else:
                merged.append((c.c, c.y, c.x0, c.x1))
        # 小節番号 (段頭・小節線の直後で、最上段より上) を除く
        starts = [sysm.x0] + [b.x_end for b in sysm.barlines]
        merged = [t for t in merged
                  if not (t[1] < sysm.top - 0.5 * sp and any(-1.5 * sp <= t[2] - x <= 1.5 * sp for x in starts))]
        events = chords + rests
        gid = 0
        for text, y, x0, x1 in merged:
            n = int(text)
            if n < 2:
                continue
            cx = (x0 + x1) / 2
            # 番号に最も近いイベント (縦方向は符幹の端まで)
            def vdist(ev):
                if ev.stem is not None:
                    lo, hi = ev.stem.y0, ev.stem.y1
                elif ev.heads:
                    lo, hi = min(h.y for h in ev.heads), max(h.y for h in ev.heads)
                else:
                    lo = hi = ev.y
                return 0 if lo <= y <= hi else min(abs(y - lo), abs(y - hi))
            def beam_side(ev):
                # 連符の数字は符幹の先端 (連桁) 側に置かれる
                if ev.stem is None or ev.kind != "chord":
                    return True
                if ev.stem_dir == "up":
                    return y < ev.stem.y0 + 0.5 * sp
                return y > ev.stem.y1 - 0.5 * sp
            cand = [e for e in events if abs(e.cx - cx) < 8 * sp and vdist(e) < 4 * sp and beam_side(e)]
            if not cand:
                continue
            # 括弧 (ブラケット) を探す
            left = [s for s in segs if not s.vertical and abs(s.x1 - x0) < 1.5 * sp and min(abs(s.y0 - y), abs(s.y1 - y)) < 1.5 * sp and s.x1 - s.x0 > 0.5 * sp]
            right = [s for s in segs if not s.vertical and abs(s.x0 - x1) < 1.5 * sp and min(abs(s.y0 - y), abs(s.y1 - y)) < 1.5 * sp and s.x1 - s.x0 > 0.5 * sp]
            staff = min(cand, key=lambda e: (vdist(e), abs(e.cx - cx))).staff
            if left and right:
                bx0 = min(s.x0 for s in left)
                bx1 = max(s.x1 for s in right)
                group = [e for e in events if e.staff is staff and bx0 - 0.5 * sp <= e.cx <= bx1 + 0.5 * sp]
            else:
                yc = y - 0.35 * (x1 - x0 + 1)  # 数字の縦の中心 (おおよそ)

                def beam_dist(e):
                    p = beam_groups[id(e)]
                    xx = min(max(cx, p.x0), p.x1)
                    t, b = p.y_at(xx)
                    return min(abs(yc - t), abs(yc - b)) + abs(cx - xx)
                near = min((e for e in cand if e.kind == "chord" and id(e) in beam_groups),
                           key=beam_dist, default=None)
                if near is None:
                    continue
                staff = near.staff
                bg = beam_groups[id(near)]
                group = [e for e in chords if beam_groups.get(id(e)) is bg]
                # 連桁の途中にある休符
                if group:
                    gx0, gx1 = min(e.cx for e in group), max(e.cx for e in group)
                    group += [r for r in rests if r.staff is staff and gx0 < r.cx < gx1]
            group = sorted((e for e in group if e.tuplet is None), key=lambda e: e.cx)
            if not group:
                continue
            # 数字の下にある、音価の合計が n × (2 のべき乗分の 1) になる連続部分を選ぶ
            best_rng, best_score = None, None
            for i in range(len(group)):
                total = Fraction(0)
                for j in range(i, len(group)):
                    e = group[j]
                    total += ir.DURATIONS[e.base] * (2 - Fraction(1, 2**e.dots))
                    unit = total / n
                    if unit.numerator != 1 or unit.denominator & (unit.denominator - 1):
                        continue
                    center = (group[i].cx + group[j].cx) / 2
                    score = (abs(center - cx), -(j - i))
                    if best_score is None or score < best_score:
                        best_rng, best_score = (i, j), score
            if best_rng is not None:
                group = group[best_rng[0]:best_rng[1] + 1]
            normal = 1
            while normal * 2 < n:
                normal *= 2
            if n == 2:
                normal = 3
            gid += 1
            for e in group:
                e.tuplet = (f"{id(sysm)}-{gid}", n, normal)

    # ------------------------------------------------------------ タイ
    def _ties(self, page, sysm, heads, sp):
        seen = set()
        for c in page.curves:
            key = (round(c.x0, 1), round(c.y0, 1), round(c.x1, 1), round(c.y1, 1))
            if key in seen:
                continue
            seen.add(key)
            if not (sysm.x0 - sp <= c.x0 <= sysm.x1 + sp and sysm.top - 8 * sp <= c.y0 <= sysm.bottom + 8 * sp):
                continue
            if c.y1 - c.y0 > 2.0 * sp:
                continue
            sx, sy = c.start
            ex, ey = c.end
            if sx > ex:
                sx, sy, ex, ey = ex, ey, sx, sy
            h1 = None
            bd = 1e9
            for h in heads:
                if h.cx - 0.8 * sp <= sx <= h.x1 + 1.2 * sp and abs(h.y - sy) < 1.3 * sp:
                    d = abs(h.y - sy) + abs(sx - h.x1)
                    if d < bd:
                        h1, bd = h, d
            h2 = None
            bd = 1e9
            for h in heads:
                if h.x - 1.2 * sp <= ex <= h.cx + 0.8 * sp and abs(h.y - ey) < 1.3 * sp:
                    d = abs(h.y - ey) + abs(ex - h.x)
                    if d < bd:
                        h2, bd = h, d
            if h1 is not None and h2 is not None and h1 is not h2 and h1.staff is h2.staff and h1.pos == h2.pos and h2.x > h1.x:
                h1.tie_out = True
                h2.tie_in = h1
            elif h1 is not None and h2 is None and ex > sysm.x1 - 2.5 * sp:
                h1.tie_out = True
                self.pending_ties[(h1.staff.index, h1.pos)] = h1
            elif h1 is None and h2 is not None and sx < sysm.x0 + 12 * sp:
                prev = self.pending_ties.pop((h2.staff.index, h2.pos), None)
                if prev is not None:
                    h2.tie_in = prev

    # ------------------------------------------------------------ テキスト
    def _onset_at(self, staff_events: list[list[Event]], x: float, k: int) -> Fraction:
        """x 位置に対応する拍位置 (段内の全五線のイベントから推定)。"""
        pts = []
        for evs in staff_events:
            for e in evs:
                if e.onset is not None and not e.measure_rest:
                    pts.append((e.x, e.onset))
        if not pts:
            return Fraction(0)
        best = min(pts, key=lambda p: abs(p[0] - x))
        return best[1]

    def _texts(self, page: Page, sysm: System, sp: float, bounds) -> dict:
        """段に属する文字を分類する。戻り値: 小節番号 -> [(種類, ...)]。歌詞はイベントへ直接付与する。"""
        staves = sysm.staves
        out: dict[int, list] = {}
        prev_sys = [s for p, s in self.systems if p is page and s.bottom < sysm.top]
        next_sys = [s for p, s in self.systems if p is page and s.top > sysm.bottom]
        y_min = (max(s.bottom for s in prev_sys) + sysm.top) / 2 if prev_sys else sysm.top - 25 * sp
        if not prev_sys and page.index == 0:
            y_min = max(y_min, sysm.top - 10 * sp)
        y_max = (min(s.top for s in next_sys) + sysm.bottom) / 2 if next_sys else sysm.bottom + 25 * sp
        chars = [c for c in page.chars if y_min < c.y < y_max and sysm.x0 - 0.5 * sp < c.x0 < sysm.x1 + sp]
        # 音楽記号の文字置換 (コードネームの ♭ など)
        for g in page.glyphs:
            if g.text_font and g.code in smufl.TEXT_SUBST and y_min < g.y < y_max:
                chars.append(Char(smufl.TEXT_SUBST[g.code], g.x, g.x1, g.y, g.size, "music", g.y - g.size, g.y))

        def measure_of(x):
            for j, (a, b, _) in enumerate(bounds):
                if a - 2 * sp <= x < b:
                    return j
            return len(bounds) - 1

        # テンポ
        used = set()
        for g in page.glyphs:
            if g.code in smufl.TEMPO_NOTES and y_min < g.y < y_max and sysm.x0 - sp < g.x < sysm.x1:
                after = sorted((c for c in chars if abs(c.y - g.y) < 0.6 * c.size and 0 <= c.x0 - g.x < 12 * sp),
                               key=lambda c: c.x0)
                txt = "".join(c.c for c in after)
                m = re.search(r"=\s*(\d+(?:\.\d+)?)", txt)
                if m:
                    bpm = float(m.group(1))
                    unit = smufl.TEMPO_NOTES[g.code]
                    q = bpm * float(ir.DURATIONS[unit] / Fraction(1, 4))
                    sym = {"quarter": "metNoteQuarterUp", "eighth": "metNote8thUp", "half": "metNoteHalfUp", "whole": "metNoteWhole"}[unit]
                    out.setdefault(measure_of(g.x), []).append(
                        ("tempo", {"bpm": q, "text": f"<sym>{sym}</sym> = {m.group(1)}"}))
                    for c in after:
                        if c.x0 <= after[0].x0 + 8 * sp:
                            used.add(id(c))
        # 練習番号 (枠囲み)
        for r in page.rects:
            if not (y_min < r.y1 < y_max and sysm.x0 - sp < r.x0 < sysm.x1):
                continue
            inside = [c for c in chars if r.x0 - 0.5 <= c.x0 and c.x1 <= r.x1 + 0.5 and r.y0 - 0.5 <= c.y <= r.y1 + 0.5]
            if inside and r.y1 < sysm.top + sp:
                txt = "".join(c.c for c in sorted(inside, key=lambda c: c.x0)).strip()
                if txt:
                    out.setdefault(measure_of(r.x0 + 1.5 * sp), []).append(("rehearsal", txt))
                    used.update(id(c) for c in inside)
        # 枠なしの練習番号: 最上段の上にある太字の短い文字列
        for w in group_words([c for c in chars if id(c) not in used and "Bold" in c.font and c.y < sysm.top]):
            if len(w.text) <= 3 and w.size >= 9 and w.y > sysm.top - 8 * sp:
                out.setdefault(measure_of(w.x0 + 1.5 * sp), []).append(("rehearsal", w.text))
                used.update(id(c) for c in w.chars)
        # 小節番号 (段頭の斜体数字) と連符の数字は除外
        rest = [c for c in chars if id(c) not in used and not ("Italic" in c.font and c.c.isdigit())]
        words = group_words(rest)
        # 単語を五線の間の領域ごとに行へ
        lines: dict[tuple, list[TextWord]] = {}
        for w in words:
            above = [st for st in staves if st.bottom < w.y]
            below = [st for st in staves if st.top > w.y - 0.3 * w.size]
            a = above[-1].index if above else -1
            b = below[0].index if below else len(staves)
            if a >= 0 and b < len(staves) and b != a + 1:
                continue  # 五線の中の文字 (強弱記号以外のテキスト) は無視
            lines.setdefault((a, b), []).append(w)
        for (a, b), ws in lines.items():
            rows: list[list[TextWord]] = []
            for w in sorted(ws, key=lambda w: w.y):
                if rows and abs(rows[-1][0].y - w.y) < 0.4 * w.size:
                    rows[-1].append(w)
                else:
                    rows.append([w])
            verse = 0
            for row in rows:
                row.sort(key=lambda w: w.x0)
                chord_like = sum(1 for w in row if is_chord_symbol(w.text))
                if chord_like >= max(1, 0.8 * len(row)) and b < len(staves):
                    for w in row:
                        if is_chord_symbol(w.text):
                            out.setdefault(measure_of(w.x0), []).append(("harmony", b, w.x0, w.text))
                    continue
                if a < 0:
                    continue  # 一番上の五線より上の文字 (表情記号など) は今は扱わない
                verse += 1
                self._assign_lyrics(row, staves[a], verse, sp, page, sysm)
        return out

    def _assign_lyrics(self, row: list[TextWord], st: Staff, verse: int, sp: float, page: Page, sysm: System):
        # この五線の和音 (歌詞の対象) は self._cur_chords から
        chords = [e for e in self._cur_chords if e.staff is st and e.kind == "chord"]
        if not chords:
            return
        chords.sort(key=lambda e: e.cx)
        cxs = [e.cx for e in chords]
        size = row[0].size
        base = statistics.median(w.y for w in row)
        dashes = [sg for sg in page.segs if sg.horizontal and base - 0.6 * size < sg.y0 < base - 0.1 * size
                  and sg.x1 - sg.x0 < 2 * size and sysm.x0 - sp <= sg.x0 <= sysm.x1 + sp]

        def dash_between(x0, x1):
            return any(x0 - 0.5 <= sg.x0 and sg.x1 <= x1 + 0.5 for sg in dashes)

        items = []  # [(lyric dict, 前にハイフン, 後ろにハイフン)]
        prev_x1 = sysm.x0
        for wi, w in enumerate(row):
            next_x0 = row[wi + 1].x0 if wi + 1 < len(row) else sysm.x1 + sp
            segs = _split_word(w, cxs, sp)
            for si_, (text, ci, _) in enumerate(segs):
                ev = chords[ci]
                if any(ly.get("verse", 1) == verse for ly in ev.lyrics):
                    continue
                item = {"text": text}
                if verse > 1:
                    item["verse"] = verse
                ev.lyrics.append(item)
                before = si_ == 0 and dash_between(prev_x1, w.x0)
                after = si_ == len(segs) - 1 and dash_between(w.x1, next_x0)
                items.append([item, before, after])
            prev_x1 = w.x1
        for item, before, after in items:
            syl = {(False, True): "begin", (True, True): "middle", (True, False): "end"}.get((before, after))
            if syl:
                item["syllabic"] = syl


def _fix_tuplet_ratio(voice: list[Event], mlen: Fraction):
    """小節の長さに合わない場合、連符の比率 (normal) を別の候補に変えて合わせる。"""
    def total():
        return sum((mlen if e.measure_rest else e.length() for e in voice), Fraction(0))
    if total() == mlen:
        return
    gids = list(dict.fromkeys(e.tuplet[0] for e in voice if e.tuplet))
    for gid in gids:
        members = [e for e in voice if e.tuplet and e.tuplet[0] == gid]
        actual, normal0 = members[0].tuplet[1], members[0].tuplet[2]
        cands = [c for c in (actual - 1, actual, actual + 1, actual // 2, actual * 2, 2, 3, 4, 6, 8) if c > 0 and c != normal0]
        for c in cands:
            for e in members:
                e.tuplet = (gid, actual, c)
            if total() == mlen:
                return
        for e in members:
            e.tuplet = (gid, actual, normal0)


def _split_word(w: TextWord, cxs: list[float], sp: float):
    """単語 w を音符の中心 cxs に合わせて分割する。[(文字列, 音符番号, syllabic)]。"""
    import bisect
    # 分割するのは日本語 (かな・漢字) の文字列だけ。欧文の単語は 1 音節として扱う
    if not any(ord(c.c) >= 0x3000 for c in w.chars):
        lo = bisect.bisect_left(cxs, w.x0 - 3 * sp)
        hi = bisect.bisect_right(cxs, w.x1 + 3 * sp)
        if lo >= hi:
            return []
        return [(w.text, min(range(lo, hi), key=lambda i: abs(cxs[i] - w.cx)), None)]
    lo = bisect.bisect_left(cxs, w.x0 - 3 * sp)
    hi = bisect.bisect_right(cxs, w.x1 + 3 * sp)
    cand = list(range(lo, hi))
    if not cand:
        return []
    chars = w.chars
    n = len(chars)
    # 1 つにまとめた場合
    best_single = min(cand, key=lambda i: abs(cxs[i] - w.cx))
    single_cost = abs(cxs[best_single] - w.cx)
    if n == 1 or single_cost < 0.6 * sp:
        return [(w.text, best_single, None)]
    # DP: 文字列を連続区間に分け、それぞれ異なる音符 (左から順) に割り当てる
    INF = 1e18
    m = len(cand)
    f = [[INF] * (m + 1) for _ in range(n + 1)]
    back = {}
    f[0] = [0.0] * (m + 1)
    for i in range(n):
        for jj in range(m + 1):
            if f[i][jj] >= INF:
                continue
            for k in range(i + 1, n + 1):
                cx = (chars[i].x0 + chars[k - 1].x1) / 2
                for j2 in range(jj, m):
                    cost = f[i][jj] + abs(cxs[cand[j2]] - cx) + 0.3 * sp
                    if cost < f[k][j2 + 1]:
                        f[k][j2 + 1] = cost
                        back[(k, j2 + 1)] = (i, jj, j2)
    best = min(range(m + 1), key=lambda j: f[n][j])
    if f[n][best] >= single_cost + 0.3 * sp:
        return [(w.text, best_single, None)]
    out = []
    k, j = n, best
    while k > 0:
        i, jj, j2 = back[(k, j)]
        out.append(("".join(c.c for c in chars[i:k]), cand[j2], None))
        k, j = i, jj
    out.reverse()
    return out


def recognize(path: str) -> dict:
    pages = extract(path)
    r = Recognizer(pages)
    return r.run()
