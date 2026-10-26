"""PDF から認識した結果を、同名の .mscz (正解) と比較して精度を出す。

使い方: python tools/evaluate.py [PDF または フォルダ ...] [-v]
"""
import glob
import os
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2mscz import ir, mscx_read  # noqa: E402
from pdf2mscz.recognize import recognize  # noqa: E402


# 見た目 (線の位置と符頭) が同じドラムは区別できないので同一視する
DRUM_SAME = {36: 35, 40: 38, 43: 41, 50: 48, 55: 52, 57: 52}


def flatten(score):
    notes, rests, lyrics, harm = Counter(), Counter(), Counter(), Counter()
    for mi, m, mlen, _ in ir.iter_measure_context(score):
        for si, st in enumerate(m["staves"]):
            for h in st.get("harmony", []):
                harm[(mi, si, Fraction(h["at"]), h["name"].replace("♭", "b").replace("♯", "#"))] += 1
            for v in st.get("voices", []):
                def walk(evs, t, scale):
                    for e in evs:
                        if e.get("type") == "tuplet":
                            walk(e["events"], t, scale * Fraction(e["normal"], e["actual"]))
                            t += ir.event_length(e) * scale
                            continue
                        ln = ir.event_length(e, mlen) * scale
                        if e["type"] == "chord":
                            for n in e["notes"]:
                                p = DRUM_SAME.get(n["drum"], n["drum"]) if "drum" in n else n["pitch"]
                                notes[(mi, si, t, ln, p)] += 1
                            for ly in e.get("lyrics", []):
                                lyrics[(mi, si, t, ly.get("verse", 1), ly["text"], ly.get("syllabic", "single"))] += 1
                        elif e.get("visible", True):
                            rests[(mi, si, t, ln)] += 1
                        t += ln
                    return t
                walk(v, Fraction(0), Fraction(1))
    return notes, rests, lyrics, harm


def f1(a: Counter, b: Counter):
    if not a and not b:
        return 1.0, 1.0, 1.0
    tp = sum((a & b).values())
    p = tp / max(1, sum(a.values()))
    r = tp / max(1, sum(b.values()))
    return 2 * p * r / max(1e-9, p + r), p, r


def evaluate(pdf, verbose=False):
    gt = mscx_read.read(pdf[:-4] + ".mscz")
    out = recognize(pdf)
    A, B = flatten(out), flatten(gt)
    res = {"measures": (len(out["measures"]), len(gt["measures"]))}
    for name, a, b in zip(("notes", "rests", "lyrics", "harmony"), A, B):
        res[name] = f1(a, b)
    if verbose:
        miss = B[0] - A[0]
        extra = A[0] - B[0]
        print("  欠落した音符 (正解にあって出力に無い) 例:", sorted(miss)[:15])
        print("  余分な音符 例:", sorted(extra)[:15])
        print("  警告数:", len(out.get("warnings", [])))
    return res, out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    pdfs = []
    for a in args or ["/mnt/c/Users/user/Desktop/楽譜"]:
        if os.path.isdir(a):
            pdfs += sorted(glob.glob(os.path.join(a, "**", "*.pdf"), recursive=True))
        else:
            pdfs.append(a)
    pdfs = [p for p in dict.fromkeys(pdfs) if os.path.exists(p[:-4] + ".mscz")]
    tot = Counter()
    for pdf in pdfs:
        try:
            res, out = evaluate(pdf, verbose)
        except Exception as e:  # noqa: BLE001
            print(f"{Path(pdf).name}: エラー {e!r}")
            tot["error"] += 1
            continue
        print(f"{Path(pdf).name[:28]:30s} 小節 {res['measures'][0]}/{res['measures'][1]}  "
              + "  ".join(f"{k} {res[k][0]:.3f}" for k in ("notes", "rests", "lyrics", "harmony"))
              + f"  警告 {len(out.get('warnings', []))}")
        for k in ("notes", "rests", "lyrics", "harmony"):
            tot[k] += res[k][0]
        tot["n"] += 1
    if tot["n"]:
        print("平均: " + "  ".join(f"{k} {tot[k] / tot['n']:.3f}" for k in ("notes", "rests", "lyrics", "harmony")),
              f"(n={tot['n']}, エラー {tot['error']})")


if __name__ == "__main__":
    main()
