"""Disassemble selected VA ranges of the YR executable (phase 4 analysis).

Examples:
  .venv\\Scripts\\python.exe scripts\\headless\\disasm_dump.py \
      --ranges 0x4F4470-0x4F47D0 0x6D3D10-0x6D3F00 0x55D878-0x55E020
  # find calls whose target is in a set, inside a range
  .venv\\Scripts\\python.exe scripts\\headless\\disasm_dump.py \
      --ranges 0x55D878-0x55E200 --show-calls
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "build" / "headless-tools"))
sys.path.insert(0, str(ROOT / "scripts" / "headless"))

import disasm_analysis as da  # noqa: E402


def parse_range(text):
    lo, _, hi = text.partition("-")
    return int(lo, 0), int(hi, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(ROOT / "test-game-headless" /
                                        "gamemd-spawn-ra2yrcpp.exe"))
    ap.add_argument("--ranges", nargs="+", required=True)
    ap.add_argument("--show-calls", action="store_true",
                    help="annotate call targets with symbol name when known")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    exe = da.Exe(args.exe)
    insns = da.disassemble(exe)
    ranges = [parse_range(r) for r in args.ranges]
    lines = []
    for lo, hi in ranges:
        lines.append(f"\n===== 0x{lo:08x} - 0x{hi:08x} =====")
        for insn in insns:
            if insn["addr"] < lo:
                continue
            if insn["addr"] >= hi:
                break
            target = da.call_target(insn)
            note = ""
            if target is not None:
                name = da.TARGETS.get(target)
                note = f"   ; -> 0x{target:08x} {name or ''}"
            lines.append(f"0x{insn['addr']:08x}: {insn['bytes']:<18} "
                         f"{insn['mnemonic']:<8} {insn['op_str']}{note}")
    text = "\n".join(lines)
    print(text)
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
