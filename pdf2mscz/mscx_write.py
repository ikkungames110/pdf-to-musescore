"""IR -> MuseScore 3.02 形式 (.mscx / .mscz) への書き出し。

3.02 形式は MuseScore 3 / 4 のどちらでも開ける。
"""
from __future__ import annotations

import io
import zipfile
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import escape

from . import ir

DATA = Path(__file__).parent / "data"

DEFAULT_DRUM_TPC = {  # MIDI ピッチ -> 既定のスペル (tpc)
    0: 14, 1: 21, 2: 16, 3: 11, 4: 18, 5: 13, 6: 20, 7: 15, 8: 10, 9: 17, 10: 12, 11: 19,
}

CLEF_STAFF_TYPE = {"PERC": "perc5Line"}


def _frac(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}"


class _W:
    def __init__(self):
        self.buf = io.StringIO()
        self.depth = 0

    def line(self, s: str):
        self.buf.write("  " * self.depth + s + "\n")

    def open(self, tag: str, attrs: str = ""):
        self.line(f"<{tag}{(' ' + attrs) if attrs else ''}>")
        self.depth += 1

    def close(self, tag: str):
        self.depth -= 1
        self.line(f"</{tag}>")

    def leaf(self, tag: str, text, attrs: str = ""):
        self.line(f"<{tag}{(' ' + attrs) if attrs else ''}>{escape(str(text))}</{tag}>")


def _pitch_of(note: dict) -> tuple[int, int]:
    if "drum" in note:
        p = int(note["drum"])
        return p, DEFAULT_DRUM_TPC[p % 12]
    return ir.pitch_to_midi_tpc(note["pitch"])


def _duration_type(length: Fraction) -> tuple[str, int] | None:
    for name, base in ir.DURATIONS.items():
        for dots in range(4):
            if base * (2 - Fraction(1, 2**dots)) == length:
                return name, dots
    return None


def _flatten_positions(score: dict):
    """(staff, voice) ごとにコードの位置 [(measure_idx, tick_in_measure, event)] を作る (タイ解決用)。"""
    pos: dict[tuple[int, int], list] = {}
    for mi, m, mlen, _ in ir.iter_measure_context(score):
        for si, st in enumerate(m["staves"]):
            for vi, events in enumerate(st.get("voices", [])):
                lst = pos.setdefault((si, vi), [])

                def walk(evs, t, scale):
                    for e in evs:
                        if e.get("type") == "tuplet":
                            inner_scale = scale * Fraction(e["normal"], e["actual"])
                            walk(e["events"], t, inner_scale)
                            t += ir.event_length(e) * scale
                            continue
                        if e.get("type") == "chord":
                            lst.append((mi, t, e))
                        t += ir.event_length(e, mlen) * scale
                    return t

                walk(events, Fraction(0), Fraction(1))
    return pos


def _resolve_ties(score: dict):
    """note['tie'] = True を (next 位置, prev 位置) の組に解決する。id(note) -> dict。"""
    nexts, prevs = {}, {}
    for (si, vi), lst in _flatten_positions(score).items():
        for k, (mi, t, ev) in enumerate(lst):
            if k + 1 >= len(lst):
                continue
            mi2, t2, ev2 = lst[k + 1]
            for n in ev["notes"]:
                if not n.get("tie"):
                    continue
                p = _pitch_of(n)[0]
                for n2 in ev2["notes"]:
                    if _pitch_of(n2)[0] == p and id(n2) not in prevs:
                        nexts[id(n)] = (mi2 - mi, t2 - t)
                        prevs[id(n2)] = (mi - mi2, t - t2)
                        break
    return nexts, prevs


def _write_location(w: _W, dm: int, df: Fraction):
    w.open("location")
    if dm:
        w.leaf("measures", dm)
    w.leaf("fractions", _frac(df))
    w.close("location")


def _write_pitched_part(w: _W, part: dict, staff_ids: list[int], nstaves_total: int, first: bool):
    for k, sid in enumerate(staff_ids):
        w.open("Staff", f'id="{sid}"')
        w.open("StaffType", 'group="pitched"')
        w.leaf("name", "stdNormal")
        w.close("StaffType")
        if first and k == 0 and nstaves_total > 1:
            w.line(f'<bracket type="1" span="{nstaves_total}" col="0"/>')
        w.close("Staff")
    w.leaf("trackName", part.get("name", ""))
    w.open("Instrument", 'id="piano"')
    w.leaf("longName", part.get("longName", part.get("name", "")))
    if part.get("shortName"):
        w.leaf("shortName", part["shortName"])
    w.leaf("trackName", part.get("name", ""))
    w.leaf("minPitchP", 21)
    w.leaf("maxPitchP", 108)
    w.leaf("minPitchA", 21)
    w.leaf("maxPitchA", 108)
    w.leaf("instrumentId", "keyboard.piano")
    w.open("Channel")
    w.line('<program value="0"/>')
    w.leaf("synti", "Fluid")
    w.close("Channel")
    w.close("Instrument")


def _write_drum_part(w: _W, part: dict, staff_ids: list[int], nstaves_total: int, first: bool):
    for k, sid in enumerate(staff_ids):
        w.open("Staff", f'id="{sid}"')
        w.open("StaffType", 'group="percussion"')
        w.leaf("name", "perc5Line")
        w.leaf("keysig", 0)
        w.close("StaffType")
        if first and k == 0 and nstaves_total > 1:
            w.line(f'<bracket type="1" span="{nstaves_total}" col="0"/>')
        w.leaf("defaultClef", "PERC")
        w.close("Staff")
    w.leaf("trackName", part.get("name", ""))
    w.open("Instrument", 'id="drumset"')
    w.leaf("longName", part.get("longName", part.get("name", "")))
    w.leaf("trackName", part.get("name", ""))
    w.leaf("instrumentId", "drum.group.set")
    w.leaf("useDrumset", 1)
    for line in (DATA / "drumset.xml").read_text(encoding="utf-8").splitlines():
        w.line(line)
    w.leaf("clef", "PERC")
    w.open("Channel")
    w.line('<controller ctrl="0" value="1"/>')
    w.line('<program value="0"/>')
    w.leaf("synti", "Fluid")
    w.leaf("midiChannel", 9)
    w.close("Channel")
    w.close("Instrument")


class _VoiceWriter:
    def __init__(self, w: _W, ties):
        self.w = w
        self.nexts, self.prevs = ties

    def chord_or_rest(self, ev: dict, mlen: Fraction, ts: str):
        w = self.w
        if ev["type"] == "rest":
            w.open("Rest")
            if ev.get("visible") is False:
                w.leaf("visible", 0)
            if ev.get("dots"):
                w.leaf("dots", ev["dots"])
            w.leaf("durationType", ev["duration"])
            if ev["duration"] == "measure":
                w.leaf("duration", _frac(mlen))
            w.close("Rest")
            return
        w.open("Chord")
        if ev.get("dots"):
            w.leaf("dots", ev["dots"])
        w.leaf("durationType", ev["duration"])
        if ev.get("stem") in ("up", "down"):
            w.leaf("StemDirection", ev["stem"])
        for ly in ev.get("lyrics", []):
            w.open("Lyrics")
            if ly.get("verse", 1) > 1:
                w.leaf("no", ly["verse"] - 1)
            if ly.get("syllabic", "single") != "single":
                w.leaf("syllabic", ly["syllabic"])
            w.leaf("text", ly["text"])
            w.close("Lyrics")
        for n in ev["notes"]:
            pitch, tpc = _pitch_of(n)
            w.open("Note")
            if id(n) in self.prevs:
                dm, df = self.prevs[id(n)]
                w.open("Spanner", 'type="Tie"')
                w.open("prev")
                _write_location(w, dm, df)
                w.close("prev")
                w.close("Spanner")
            if id(n) in self.nexts:
                dm, df = self.nexts[id(n)]
                w.open("Spanner", 'type="Tie"')
                w.open("Tie")
                w.close("Tie")
                w.open("next")
                _write_location(w, dm, df)
                w.close("next")
                w.close("Spanner")
            w.leaf("pitch", pitch)
            w.leaf("tpc", tpc)
            w.close("Note")
        w.close("Chord")

    def events(self, evs: list, mlen: Fraction, ts: str, t: Fraction, scale: Fraction, annots: list) -> Fraction:
        """イベント列を書き、注釈 (tick, writer) を適切な位置に差し込む。"""
        for ev in evs:
            if ev.get("type") == "tuplet":
                inner = ir.voice_length(ev["events"])
                base = inner / ev["actual"]
                bt = _duration_type(base)
                w = self.w
                w.open("Tuplet")
                w.leaf("normalNotes", ev["normal"])
                w.leaf("actualNotes", ev["actual"])
                w.leaf("baseNote", bt[0] if bt else "eighth")
                w.open("Number")
                w.leaf("style", "Tuplet")
                w.leaf("text", ev["actual"])
                w.close("Number")
                w.close("Tuplet")
                self._annots_at(t, annots)
                t = self.events(ev["events"], mlen, ts, t, scale * Fraction(ev["normal"], ev["actual"]), annots)
                w.line("<endTuplet/>")
                continue
            self._annots_at(t, annots)
            self.chord_or_rest(ev, mlen, ts)
            t += ir.event_length(ev, mlen) * scale
            self._annots_before(t, annots)
        return t

    def _annots_at(self, t: Fraction, annots: list):
        while annots and annots[0][0] <= t:
            at, fn = annots.pop(0)
            if at < t:
                self._jump(at - t, fn)
            else:
                fn()

    def _annots_before(self, t: Fraction, annots: list):
        while annots and annots[0][0] < t:
            at, fn = annots.pop(0)
            self._jump(at - t, fn)

    def _jump(self, d: Fraction, fn):
        _write_location(self.w, 0, d)
        fn()
        _write_location(self.w, 0, -d)

    def flush(self, t: Fraction, annots: list):
        while annots:
            at, fn = annots.pop(0)
            if at == t:
                fn()
            else:
                self._jump(at - t, fn)


def _note_tpc(s: str) -> int | None:
    s = s.replace("♭", "b").replace("♯", "#")
    if not s or s[0].upper() not in ir.STEP_TPC:
        return None
    alter = {"": 0, "b": -1, "#": 1, "bb": -2, "##": 2}.get(s[1:], None)
    return None if alter is None else ir.STEP_TPC[s[0].upper()] + 7 * alter


def parse_chord_symbol(text: str) -> tuple[int | None, str, int | None]:
    """'B♭Maj7/F' -> (root tpc, 'Maj7', bass tpc)。解釈できなければ root=None。"""
    t = text.replace("♭", "b").replace("♯", "#").strip()
    bass = None
    if "/" in t:
        head, tail = t.rsplit("/", 1)
        b = _note_tpc(tail.strip())
        if b is not None:
            t, bass = head, b
    n = 1
    while n < len(t) and n < 3 and t[n] in "b#":
        n += 1
    root = _note_tpc(t[:n])
    if root is None:
        return None, text, None
    return root, t[n:], bass


def _harmony_writer(w: _W, name: str):
    def fn():
        root, kind, bass = parse_chord_symbol(name)
        w.open("Harmony")
        if root is None:
            w.leaf("name", name)
        else:
            w.leaf("root", root)
            if kind:
                w.leaf("name", kind)
            if bass is not None:
                w.leaf("base", bass)
        w.close("Harmony")
    return fn


def _clef_writer(w: _W, clef: str):
    def fn():
        w.open("Clef")
        w.leaf("concertClefType", clef)
        w.leaf("transposingClefType", clef)
        w.close("Clef")
    return fn


def _key_writer(w: _W, key: int):
    def fn():
        w.open("KeySig")
        w.leaf("accidental", key)
        w.close("KeySig")
    return fn


def _text_writer(w: _W, tag: str, text: str, extra=None):
    def fn():
        w.open(tag)
        if extra:
            extra()
        w.leaf("text", text)
        w.close(tag)
    return fn


def to_mscx(score: dict) -> str:
    w = _W()
    w.line('<?xml version="1.0" encoding="UTF-8"?>')
    w.open("museScore", 'version="3.02"')
    w.leaf("programVersion", "3.6.2")
    w.leaf("programRevision", "3224f34")
    w.open("Score")
    w.leaf("Division", ir.DIVISION)
    w.open("Style")
    if score.get("spatium"):
        w.leaf("Spatium", round(score["spatium"], 4))
    w.close("Style")
    w.leaf("showInvisible", 1)
    w.leaf("showUnprintable", 1)
    w.leaf("showFrames", 1)
    w.leaf("showMargins", 0)
    meta = score.get("meta", {})
    w.leaf("metaTag", meta.get("title", ""), 'name="workTitle"')
    w.leaf("metaTag", meta.get("composer", ""), 'name="composer"')
    w.leaf("metaTag", meta.get("lyricist", ""), 'name="lyricist"')
    w.leaf("metaTag", meta.get("arranger", ""), 'name="arranger"')

    parts = score["parts"]
    staff_part = []  # staff index -> part
    sid = 1
    nstaves_total = sum(p.get("staves", 1) for p in parts)
    for pi, part in enumerate(parts):
        ids = list(range(sid, sid + part.get("staves", 1)))
        sid += len(ids)
        w.open("Part")
        if part.get("instrument") == "drumset":
            _write_drum_part(w, part, ids, nstaves_total, pi == 0)
        else:
            _write_pitched_part(w, part, ids, nstaves_total, pi == 0)
        w.close("Part")
        staff_part += [part] * len(ids)

    ties = _resolve_ties(score)
    measures = list(ir.iter_measure_context(score))
    for si in range(nstaves_total):
        w.open("Staff", f'id="{si + 1}"')
        if si == 0 and any(meta.get(k) for k in ("title", "subtitle", "composer", "lyricist")):
            w.open("VBox")
            w.leaf("height", 10)
            for key, style in (("title", "Title"), ("subtitle", "Subtitle"), ("composer", "Composer"), ("lyricist", "Lyricist")):
                if meta.get(key):
                    w.open("Text")
                    w.leaf("style", style)
                    w.leaf("text", meta[key])
                    w.close("Text")
            w.close("VBox")
        prev_ts = None
        prev_key = None
        for mi, m, mlen, ts in measures:
            st = m["staves"][si] if si < len(m["staves"]) else {}
            attrs = ""
            if "len" in m:
                attrs = f'len="{m["len"]}"'
            w.open("Measure", attrs)
            if m.get("repeatStart"):
                w.line("<startRepeat/>")
            if m.get("repeatEnd"):
                w.leaf("endRepeat", m.get("repeatCount", 2))
            voices = st.get("voices") or [[{"type": "rest", "duration": "measure"}]]
            for vi, events in enumerate(voices):
                w.open("voice")
                if vi == 0:
                    clef = st.get("clef")
                    if clef and not (mi == 0 and clef == "G"):
                        w.open("Clef")
                        w.leaf("concertClefType", clef)
                        w.leaf("transposingClefType", clef)
                        w.close("Clef")
                    key = m.get("key", prev_key if prev_key is not None else 0)
                    if m.get("keyAt", "0") != "0" and mi > 0:
                        key = prev_key  # 小節途中の変更は後で書く
                    if mi == 0 or ("key" in m and key != prev_key):
                        w.open("KeySig")
                        w.leaf("accidental", key)
                        w.close("KeySig")
                    prev_key = key
                    if mi == 0 or ("timeSig" in m and ts != prev_ts):
                        n, d = ts.split("/")
                        w.open("TimeSig")
                        w.leaf("sigN", n)
                        w.leaf("sigD", d)
                        w.close("TimeSig")
                    prev_ts = ts
                annots = []
                if vi == 0:
                    if si == 0 and m.get("tempo"):
                        tempo = m["tempo"]
                        bpm = tempo["bpm"]

                        def tempo_extra(bpm=bpm):
                            w.leaf("tempo", round(bpm / 60, 6))
                            w.leaf("followText", 1)
                        annots.append((Fraction(0), _text_writer(w, "Tempo", tempo.get("text", f"♩ = {bpm}"), tempo_extra)))
                    if si == 0 and m.get("rehearsal"):
                        annots.append((Fraction(0), _text_writer(w, "RehearsalMark", m["rehearsal"])))
                    for h in st.get("harmony", []):
                        annots.append((Fraction(h.get("at", "0")), _harmony_writer(w, h["name"])))
                    is_drum = staff_part[si].get("instrument") == "drumset"
                    st_keys = False
                    for ch in st.get("changes", []):
                        if "clef" in ch:
                            annots.append((Fraction(ch["at"]), _clef_writer(w, ch["clef"])))
                        if "key" in ch and not is_drum:
                            st_keys = True
                            annots.append((Fraction(ch["at"]), _key_writer(w, ch["key"])))
                    if m.get("keyAt", "0") != "0" and not st_keys and not is_drum:
                        annots.append((Fraction(m["keyAt"]), _key_writer(w, m["key"])))
                    if m.get("keyAt", "0") != "0":
                        prev_key = m["key"]
                    for tx in st.get("texts", []):
                        annots.append((Fraction(tx.get("at", "0")), _text_writer(w, "StaffText", tx["text"])))
                    annots.sort(key=lambda a: a[0])
                vw = _VoiceWriter(w, ties)
                t = vw.events(events, mlen, ts, Fraction(0), Fraction(1), annots)
                vw.flush(t, annots)
                if vi == 0 and m.get("barline") and m["barline"] != "normal":
                    w.open("BarLine")
                    w.leaf("subtype", m["barline"])
                    w.close("BarLine")
                w.close("voice")
            if si == 0 and m.get("lineBreak"):
                w.open("LayoutBreak")
                w.leaf("subtype", "line")
                w.close("LayoutBreak")
            if si == 0 and m.get("pageBreak"):
                w.open("LayoutBreak")
                w.leaf("subtype", "page")
                w.close("LayoutBreak")
            w.close("Measure")
        w.close("Staff")
    w.close("Score")
    w.close("museScore")
    return w.buf.getvalue()


def write_mscz(score: dict, path: str | Path) -> None:
    path = Path(path)
    name = path.stem + ".mscx"
    xml = to_mscx(score)
    if path.suffix == ".mscx":
        path.write_text(xml, encoding="utf-8")
        return
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<container>\n  <rootfiles>\n'
        f'    <rootfile full-path="{escape(name)}"/>\n  </rootfiles>\n</container>\n'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr(name, xml)
