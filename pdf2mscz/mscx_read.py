"""MuseScore (.mscz/.mscx, 3.x/4.x) -> IR。評価 (正解データとの比較) と往復テストに使う。"""
from __future__ import annotations

import zipfile
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

from . import ir
from .mscx_write import DEFAULT_DRUM_TPC


def _load_root(path: str | Path) -> ET.Element:
    path = Path(path)
    if path.suffix == ".mscz":
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".mscx"))
            return ET.fromstring(z.read(name))
    return ET.parse(path).getroot()


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _harmony_name(h: ET.Element) -> str:
    root = h.findtext("root")
    name = h.findtext("name") or ""
    base = h.findtext("base")

    def tpc_name(t):
        return ir.midi_tpc_to_name(60, int(t))[:-1]
    s = (tpc_name(root) if root else "") + name
    if base:
        s += "/" + tpc_name(base)
    return s


def read(path: str | Path) -> dict:
    root = _load_root(path)
    score_el = root.find("Score")
    parts = []
    drum_staves = set()
    staff_no = 0
    for p in score_el.findall("Part"):
        staves = p.findall("Staff")
        inst = p.find("Instrument")
        drum = inst is not None and inst.findtext("useDrumset") == "1"
        parts.append({
            "name": p.findtext("trackName") or "",
            "longName": (inst.findtext("longName") if inst is not None else "") or "",
            "instrument": "drumset" if drum else "piano",
            "staves": len(staves),
        })
        for _ in staves:
            if drum:
                drum_staves.add(staff_no)
            staff_no += 1

    meta = {}
    for tag in score_el.findall("metaTag"):
        if tag.get("name") == "workTitle" and tag.text:
            meta["title"] = tag.text

    staves = score_el.findall("Staff")
    measures: list[dict] = []
    for si, staff in enumerate(staves):
        mi = 0
        ts = "4/4"
        for el in staff:
            if el.tag == "VBox" and si == 0:
                for t in el.findall("Text"):
                    style = (t.findtext("style") or "").lower()
                    if style in ("title", "subtitle", "composer", "lyricist"):
                        meta[style] = _text(t.find("text"))
                continue
            if el.tag != "Measure":
                continue
            if mi >= len(measures):
                measures.append({"staves": [{} for _ in staves]})
            m = measures[mi]
            if el.get("len"):
                m["len"] = el.get("len")
            if el.find("startRepeat") is not None:
                m["repeatStart"] = True
            if el.find("endRepeat") is not None:
                m["repeatEnd"] = True
            if si == 0:
                for lb in el.findall("LayoutBreak"):
                    if lb.findtext("subtype") == "line":
                        m["lineBreak"] = True
            st = m["staves"][si]
            voices = []
            for v in el.findall("voice"):
                events: list = []
                stack = [events]
                t = Fraction(0)
                tscale = [Fraction(1)]
                pending_lyrics_ticks = None
                for e in v:
                    tag = e.tag
                    if tag == "TimeSig":
                        ts = f"{e.findtext('sigN')}/{e.findtext('sigD')}"
                        if si == 0:
                            m["timeSig"] = ts
                    elif tag == "KeySig" and si == 0:
                        acc = e.findtext("accidental") or e.findtext("concertKey")
                        if acc is not None:
                            m["key"] = int(acc)
                            if t:
                                m["keyAt"] = str(t)
                    elif tag == "Clef":
                        c = e.findtext("concertClefType")
                        if c and not voices:
                            if t == 0:
                                st["clef"] = c
                            else:
                                st.setdefault("changes", []).append({"at": str(t), "clef": c})
                    elif tag == "Tempo" and si == 0:
                        m["tempo"] = {"bpm": round(float(e.findtext("tempo")) * 60, 3), "text": _text(e.find("text"))}
                    elif tag == "RehearsalMark" and si == 0:
                        m["rehearsal"] = _text(e.find("text"))
                    elif tag == "Harmony":
                        st.setdefault("harmony", []).append({"at": str(t), "name": _harmony_name(e)})
                    elif tag == "location":
                        f = e.findtext("fractions")
                        if f:
                            t += Fraction(f)
                    elif tag == "BarLine" and si == 0:
                        sub = e.findtext("subtype")
                        if sub:
                            m["barline"] = sub
                    elif tag == "Tuplet":
                        grp = {
                            "type": "tuplet",
                            "actual": int(e.findtext("actualNotes")),
                            "normal": int(e.findtext("normalNotes")),
                            "events": [],
                        }
                        stack[-1].append(grp)
                        stack.append(grp["events"])
                        tscale.append(tscale[-1] * Fraction(grp["normal"], grp["actual"]))
                    elif tag == "endTuplet":
                        if len(stack) > 1:
                            stack.pop()
                            tscale.pop()
                    elif tag in ("Chord", "Rest"):
                        dt = e.findtext("durationType")
                        ev = {"type": "chord" if tag == "Chord" else "rest", "duration": dt}
                        dots = int(e.findtext("dots") or 0)
                        if dots:
                            ev["dots"] = dots
                        if tag == "Rest" and e.findtext("visible") == "0":
                            ev["visible"] = False
                        if tag == "Chord":
                            if any(c.tag in ("acciaccatura", "appoggiatura") or c.tag.startswith("grace") for c in e):
                                continue  # 装飾音は対象外
                            notes = []
                            for n in e.findall("Note"):
                                pitch = int(n.findtext("pitch"))
                                tpc = int(n.findtext("tpc"))
                                note = {"drum": pitch} if si in drum_staves else {"pitch": ir.midi_tpc_to_name(pitch, tpc)}
                                for sp in n.findall("Spanner"):
                                    if sp.get("type") == "Tie" and sp.find("next") is not None:
                                        note["tie"] = True
                                if n.find("Tie") is not None:  # 2.x 形式
                                    note["tie"] = True
                                notes.append(note)
                            ev["notes"] = notes
                            lyrics = []
                            for ly in e.findall("Lyrics"):
                                item = {"text": _text(ly.find("text"))}
                                no = ly.findtext("no")
                                if no:
                                    item["verse"] = int(no) + 1
                                tk = ly.findtext("ticks")
                                if tk and int(tk) > 0:
                                    item["melisma"] = str(Fraction(int(tk), 4 * ir.DIVISION))
                                syl = ly.findtext("syllabic")
                                if syl and syl != "single":
                                    item["syllabic"] = syl
                                lyrics.append(item)
                            if lyrics:
                                ev["lyrics"] = lyrics
                        stack[-1].append(ev)
                        if dt == "measure":
                            t += Fraction(e.findtext("duration") or ts)
                        else:
                            t += ir.event_length(ev) * tscale[-1]
                voices.append(events)
            # 空の voice (中身なし) は落とす
            while len(voices) > 1 and not voices[-1]:
                voices.pop()
            st["voices"] = voices
            mi += 1
    return {"format": "pdf2mscz-ir/1", "meta": meta, "parts": parts, "measures": measures}
