"""コマンドライン: PDF 楽譜 -> MuseScore (.mscz)。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ir
from .mscx_write import write_mscz


def _print_warnings(score: dict, limit: int = 30):
    ws = score.get("warnings", [])
    if not ws:
        print("  警告なし")
        return
    print(f"  要確認 {len(ws)} 件:")
    for w in ws[:limit]:
        loc = " ".join(f"{k}={w[k]}" for k in ("page", "measure", "staff", "voice") if k in w)
        print(f"   - [{loc}] {w['msg']}")
    if len(ws) > limit:
        print(f"   ... ほか {len(ws) - limit} 件 (--ir で JSON を出力すると全件見られます)")


def cmd_convert(a):
    from .recognize import recognize
    for src in a.pdf:
        src = Path(src)
        out = Path(a.output) if a.output and len(a.pdf) == 1 else src.with_suffix(".mscz")
        if a.outdir:
            out = Path(a.outdir) / (src.stem + ".mscz")
        print(f"{src.name} を解析中...")
        score = recognize(str(src))
        if not score["measures"]:
            print("  五線が見つかりませんでした。スキャン画像の PDF の可能性があります。")
            print("  -> `pdf2mscz images` でページ画像を作り、prompts/hitl_omr.md の手順で Claude Code に読ませてください。")
            continue
        if a.ir:
            irp = Path(a.ir) if len(a.pdf) == 1 else out.with_suffix(".json")
            ir.dump(score, str(irp))
            print(f"  IR: {irp}")
        write_mscz(score, out)
        print(f"  出力: {out}  ({len(score['parts'])} パート, {len(score['measures'])} 小節)")
        _print_warnings(score)


def cmd_from_ir(a):
    score = ir.load(a.json)
    errs = ir.validate(score)
    if errs:
        print(f"IR に {len(errs)} 件の問題があります:")
        for e in errs[:50]:
            print("  -", e)
        if not a.force:
            print("修正してから再実行してください (--force で警告を無視して出力)。")
            sys.exit(1)
    out = Path(a.output) if a.output else Path(a.json).with_suffix(".mscz")
    write_mscz(score, out)
    print(f"出力: {out}")


def cmd_validate(a):
    errs = ir.validate(ir.load(a.json))
    if not errs:
        print("OK: 問題は見つかりませんでした")
        return
    print(f"{len(errs)} 件の問題:")
    for e in errs:
        print("  -", e)
    sys.exit(1)


def cmd_images(a):
    import pymupdf
    src = Path(a.pdf)
    outdir = Path(a.outdir or src.with_suffix(""))
    outdir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(src)
    for i, page in enumerate(doc):
        p = outdir / f"page{i + 1:02d}.png"
        page.get_pixmap(dpi=a.dpi).save(p)
        print(p)


def cmd_mscz_to_ir(a):
    from .mscx_read import read
    score = read(a.mscz)
    out = a.output or str(Path(a.mscz).with_suffix(".json"))
    ir.dump(score, out)
    print(f"出力: {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="pdf2mscz", description="PDF 楽譜を MuseScore (.mscz) に変換します")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="PDF (MuseScore 等から書き出したベクター PDF) を .mscz に変換")
    c.add_argument("pdf", nargs="+")
    c.add_argument("-o", "--output", help="出力 .mscz (PDF が 1 つの時)")
    c.add_argument("-d", "--outdir", help="出力フォルダ")
    c.add_argument("--ir", nargs="?", const=True, help="中間表現 JSON も出力する (パス省略可)")
    c.set_defaults(func=cmd_convert)

    c = sub.add_parser("from-ir", help="中間表現 JSON (HITL で作ったものなど) から .mscz を作る")
    c.add_argument("json")
    c.add_argument("-o", "--output")
    c.add_argument("--force", action="store_true")
    c.set_defaults(func=cmd_from_ir)

    c = sub.add_parser("validate", help="中間表現 JSON を検査する")
    c.add_argument("json")
    c.set_defaults(func=cmd_validate)

    c = sub.add_parser("images", help="PDF の各ページを PNG にする (HITL 用)")
    c.add_argument("pdf")
    c.add_argument("-d", "--outdir")
    c.add_argument("--dpi", type=int, default=200)
    c.set_defaults(func=cmd_images)

    c = sub.add_parser("mscz-to-ir", help=".mscz/.mscx を中間表現 JSON にする (お手本・比較用)")
    c.add_argument("mscz")
    c.add_argument("-o", "--output")
    c.set_defaults(func=cmd_mscz_to_ir)

    a = p.parse_args(argv)
    if getattr(a, "ir", None) is True:
        a.ir = None if len(getattr(a, "pdf", [])) != 1 else str(Path(a.output or Path(a.pdf[0]).with_suffix(".mscz")).with_suffix(".json"))
    a.func(a)


if __name__ == "__main__":
    main()
