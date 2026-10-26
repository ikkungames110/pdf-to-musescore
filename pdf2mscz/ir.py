"""中間表現 (IR) のユーティリティ。

IR は JSON で表せる素の dict/list で、PDF 解析器・HITL(Claude Code)・評価ツールの
共通フォーマットになる。仕様は docs/IR_FORMAT.md を参照。
"""
from __future__ import annotations

import json
import re
from fractions import Fraction

DIVISION = 480  # 4分音符あたりの tick 数 (MuseScore と同じ)

DURATIONS = {  # 名前 -> 全音符を 1 とした長さ
    "long": Fraction(4),
    "breve": Fraction(2),
    "whole": Fraction(1),
    "half": Fraction(1, 2),
    "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8),
    "16th": Fraction(1, 16),
    "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64),
    "128th": Fraction(1, 128),
}
DURATION_NAMES = list(DURATIONS)

STEP_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
STEP_TPC = {"F": 13, "C": 14, "G": 15, "D": 16, "A": 17, "E": 18, "B": 19}
TPC_STEP = {v: k for k, v in STEP_TPC.items()}
ACC_ALTER = {"bb": -2, "b": -1, "": 0, "#": 1, "##": 2, "x": 2}
ALTER_ACC = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}

_PITCH_RE = re.compile(r"^([A-Ga-g])(bb|b|##|#|x)?(-?\d+)$")


def parse_pitch(s: str) -> tuple[str, int, int]:
    """'Bb4' -> ('B', -1, 4)。C4 = MIDI 60。"""
    m = _PITCH_RE.match(s.strip())
    if not m:
        raise ValueError(f"音名を解釈できません: {s!r}")
    return m.group(1).upper(), ACC_ALTER[m.group(2) or ""], int(m.group(3))


def pitch_name(step: str, alter: int, octave: int) -> str:
    return f"{step}{ALTER_ACC[alter]}{octave}"


def pitch_to_midi_tpc(s: str) -> tuple[int, int]:
    step, alter, octave = parse_pitch(s)
    return 12 * (octave + 1) + STEP_SEMI[step] + alter, STEP_TPC[step] + 7 * alter


def midi_tpc_to_name(midi: int, tpc: int) -> str:
    alter = 0
    while tpc < 13:
        tpc += 7
        alter -= 1
    while tpc > 19:
        tpc -= 7
        alter += 1
    step = TPC_STEP[tpc]
    octave = (midi - alter - STEP_SEMI[step]) // 12 - 1
    return pitch_name(step, alter, octave)


def event_length(ev: dict, time_sig: Fraction | None = None) -> Fraction:
    """イベント 1 つの長さ (全音符=1)。連符グループは中身の合計 × normal/actual。"""
    if ev.get("type") == "tuplet":
        inner = sum((event_length(e) for e in ev["events"]), Fraction(0))
        return inner * Fraction(ev["normal"], ev["actual"])
    d = ev["duration"]
    if d == "measure":
        return time_sig if time_sig is not None else Fraction(1)
    base = DURATIONS[d]
    dots = ev.get("dots", 0)
    return base * (2 - Fraction(1, 2**dots))


def voice_length(events: list, time_sig: Fraction | None = None) -> Fraction:
    return sum((event_length(e, time_sig) for e in events), Fraction(0))


def parse_timesig(s: str) -> Fraction:
    n, d = s.split("/")
    return Fraction(int(n), int(d))


def iter_measure_context(score: dict):
    """各小節について (index, measure, 拍子(Fraction), 拍子文字列) を返す。"""
    ts = "4/4"
    for i, m in enumerate(score["measures"]):
        ts = m.get("timeSig", ts)
        yield i, m, parse_timesig(m.get("len", ts)), ts


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(score: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(score, f, ensure_ascii=False, indent=1)


def validate(score: dict) -> list[str]:
    """IR の形式と小節の長さを検査し、問題点のリストを返す (空なら OK)。"""
    errs: list[str] = []
    if not isinstance(score.get("parts"), list) or not score["parts"]:
        errs.append("parts がありません")
        return errs
    nstaves = sum(p.get("staves", 1) for p in score["parts"])
    if not isinstance(score.get("measures"), list) or not score["measures"]:
        errs.append("measures がありません")
        return errs

    def check_events(evs, where):
        for i, e in enumerate(evs):
            w = f"{where} イベント{i + 1}"
            t = e.get("type")
            if t == "tuplet":
                for k in ("actual", "normal", "events"):
                    if k not in e:
                        errs.append(f"{w}: tuplet に {k} がありません")
                check_events(e.get("events", []), w + " (連符内)")
                continue
            if t not in ("chord", "rest"):
                errs.append(f"{w}: type は chord / rest / tuplet のいずれかです ({t!r})")
                continue
            d = e.get("duration")
            if d not in DURATIONS and not (t == "rest" and d == "measure"):
                errs.append(f"{w}: duration {d!r} が不正です")
            if t == "chord":
                if not e.get("notes"):
                    errs.append(f"{w}: chord に notes がありません")
                for n in e.get("notes", []):
                    if "drum" in n:
                        continue
                    try:
                        parse_pitch(n.get("pitch", ""))
                    except ValueError as ex:
                        errs.append(f"{w}: {ex}")

    for i, m, mlen, ts in iter_measure_context(score):
        where = f"小節{i + 1}"
        try:
            parse_timesig(ts)
        except Exception:  # noqa: BLE001
            errs.append(f"{where}: timeSig {ts!r} が不正です")
            continue
        sts = m.get("staves", [])
        if len(sts) != nstaves:
            errs.append(f"{where}: staves の数が {len(sts)} (五線の数 {nstaves} と一致しません)")
        for k, st in enumerate(sts):
            for vi, v in enumerate(st.get("voices", [])):
                check_events(v, f"{where} 五線{k + 1} 声部{vi + 1}")
                try:
                    tot = voice_length(v, mlen)
                except Exception:  # noqa: BLE001
                    continue
                if v and tot != mlen and not (vi > 0 and tot < mlen):
                    errs.append(f"{where} 五線{k + 1} 声部{vi + 1}: 音価の合計 {tot} が小節の長さ {mlen} と一致しません")
    return errs
