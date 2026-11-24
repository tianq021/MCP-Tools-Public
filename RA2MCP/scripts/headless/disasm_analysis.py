"""Static analysis of the YR executable for the no-render (DirectDraw) question.

Uses capstone + pefile (installed under build/headless-tools) to:
  * locate the instruction containing the ra2yrcpp GameLoopBegin hook (0x55DE4F),
  * recover its enclosing function heuristically and list its call targets,
  * find every call site of the known render entry points
    (GScreenClass::Render 0x4F4480, GScreenClass::DoBlit 0x4F4780,
     TacticalClass::Render 0x6D3D10),
  * report which enclosing function each call site belongs to, so the one inside
    the per-frame game loop can be identified and patched reversibly.

Read-only: reads a copy of the exe, writes a JSON report.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "build" / "headless-tools"))

import capstone  # noqa: E402
import pefile  # noqa: E402

TARGETS = {
    0x4F4480: "GScreenClass::Render",
    0x4F4780: "GScreenClass::DoBlit",
    0x6D3D10: "TacticalClass::Render",
    0x55DDA5: "yr-patches MainLoop_AfterRender call site",
    0x55DE4F: "ra2yrcpp GameLoopBegin hook",
}
HOOK_ADDR = 0x55DE4F


class Exe:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.pe = pefile.PE(str(self.path), fast_load=True)
        self.image_base = int(self.pe.OPTIONAL_HEADER.ImageBase)
        self.sections = []
        for section in self.pe.sections:
            name = section.Name.rstrip(b"\x00").decode("ascii", "replace")
            self.sections.append({
                "name": name,
                "va": self.image_base + int(section.VirtualAddress),
                "vsize": int(section.Misc_VirtualSize),
                "raw_size": int(section.SizeOfRawData),
                "raw_ptr": int(section.PointerToRawData),
                "data": bytes(section.get_data()),
            })

    def section_for_va(self, va):
        for s in self.sections:
            if s["va"] <= va < s["va"] + max(s["raw_size"], s["vsize"]):
                return s
        return None

    def read_va(self, va, size):
        """Read from the mapped image (raw file bytes, zero padded)."""
        s = self.section_for_va(va)
        if s is None:
            return None
        offset = va - s["va"]
        data = s["data"]
        chunk = data[offset:offset + size]
        if len(chunk) < size:
            chunk += b"\x00" * (size - len(chunk))
        return chunk


def disassemble(exe):
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    md.skipdata = True
    insns = []
    for section in exe.sections:
        if section["name"] not in (".text", "CODE"):
            continue
        for insn in md.disasm(section["data"], section["va"]):
            try:
                mnemonic = insn.mnemonic
                op_str = insn.op_str
                raw = insn.bytes.hex()
            except capstone.CsError:
                mnemonic, op_str, raw = ".byte", "", ""
            insns.append({
                "addr": int(insn.address),
                "size": int(insn.size),
                "mnemonic": mnemonic,
                "op_str": op_str,
                "bytes": raw,
            })
    return insns


def find_insn_index(insns, va):
    for i, insn in enumerate(insns):
        if insn["addr"] <= va < insn["addr"] + insn["size"]:
            return i
    return None


def call_target(insn):
    if insn["mnemonic"] != "call":
        return None
    text = insn["op_str"].strip()
    if text.startswith("0x"):
        try:
            return int(text, 16)
        except ValueError:
            return None
    return None


def function_start(insns, index, exe):
    """Heuristic function start: nearest instruction after a ret/int3 padding run."""
    i = index
    while i > 0:
        prev = insns[i - 1]
        if prev["mnemonic"] in ("ret", "retn", "iret", "int3", "hlt"):
            # require some int3/padding between the boundary and current insn
            j = i
            gap = 0
            while j < index and insns[j]["mnemonic"] == "int3":
                j += 1
                gap += 1
            if gap >= 2 or prev["mnemonic"] in ("ret", "retn"):
                return insns[j]["addr"] if gap else insns[i]["addr"]
        i -= 1
    return insns[0]["addr"]


def enclosing_functions(insns, targets, exe):
    """Map target VA -> [(call_site, function_start, first_bytes)]."""
    result = {t: [] for t in targets}
    for i, insn in enumerate(insns):
        target = call_target(insn)
        if target is None or target not in targets:
            continue
        start = function_start(insns, i, exe)
        head = exe.read_va(start, 16)
        result[target].append({
            "call_site": insn["addr"],
            "call_op_str": insn["op_str"],
            "function_start": start,
            "function_head_hex": head.hex() if head else None,
        })
    return result


def function_disassembly(insns, fstart, max_bytes=0x600):
    out = []
    for insn in insns:
        if insn["addr"] < fstart:
            continue
        if insn["addr"] >= fstart + max_bytes:
            break
        if out and insn["mnemonic"] in ("ret", "retn") and len(out) > 8:
            # keep going a bit to allow multiple exit blocks
            pass
        out.append({"addr": insn["addr"], "bytes": insn["bytes"],
                    "mnemonic": insn["mnemonic"], "op_str": insn["op_str"]})
        if len(out) > 400:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(ROOT / "test-game-headless" /
                                        "gamemd-spawn-ra2yrcpp.exe"))
    ap.add_argument("--out", default=str(ROOT / "reports" / "headless" /
                                         "disasm-analysis.json"))
    args = ap.parse_args()
    exe = Exe(args.exe)
    print(f"image_base=0x{exe.image_base:x} sections=" +
          ",".join(s["name"] for s in exe.sections if s["name"] in (".text", "CODE")))
    insns = disassemble(exe)
    print(f"disassembled {len(insns)} instructions")
    report = {
        "exe": str(exe.path),
        "size": exe.path.stat().st_size,
        "image_base": exe.image_base,
        "instruction_count": len(insns),
        "targets": {f"0x{k:x}": v for k, v in TARGETS.items()},
        "call_sites": enclosing_functions(insns, TARGETS, exe),
    }
    # main loop function containing the ra2yrcpp GameLoopBegin hook
    hook_idx = find_insn_index(insns, HOOK_ADDR)
    if hook_idx is not None:
        hook = insns[hook_idx]
        fstart = function_start(insns, hook_idx, exe)
        report["game_loop_hook"] = {
            "hook_va": HOOK_ADDR,
            "instruction": hook,
            "function_start": fstart,
            "function_disassembly": function_disassembly(insns, fstart, 0x800),
        }
        # calls inside the loop function
        calls = []
        for insn in insns:
            if insn["addr"] < fstart or insn["addr"] >= fstart + 0x800:
                continue
            target = call_target(insn)
            if target is not None:
                calls.append({"call_site": insn["addr"], "target": target,
                              "target_name": TARGETS.get(target)})
        report["game_loop_hook"]["calls"] = calls
        # also list calls inside the neighboring function (hook may sit in a callee)
        prev_start = function_start(insns, max(0, hook_idx - 1), exe)
        report["previous_function"] = {
            "start": prev_start,
            "calls": [{"call_site": i["addr"], "target": call_target(i),
                       "target_name": TARGETS.get(call_target(i))}
                      for i in insns
                      if prev_start <= i["addr"] < fstart and call_target(i) is not None],
        }
    # dump raw bytes around the hook
    raw = exe.read_va(HOOK_ADDR - 32, 96)
    report["hook_region_hex"] = raw.hex() if raw else None
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out}")
    # concise stdout summary
    print("\nCall sites to render entry points:")
    for target, name in TARGETS.items():
        sites = report["call_sites"].get(target, [])
        print(f"  {name} (0x{target:x}): {len(sites)} call sites")
        for s in sites[:10]:
            print(f"    call@0x{s['call_site']:x} func@0x{s['function_start']:x} "
                  f"head={s['function_head_hex']}")
    if "game_loop_hook" in report:
        gh = report["game_loop_hook"]
        print(f"\nGameLoopBegin hook inside function 0x{gh['function_start']:x} "
              f"at 0x{gh['hook_va']:x}")
        print("  calls in function:")
        for c in gh["calls"]:
            print(f"    0x{c['call_site']:x} -> 0x{c['target']:x} {c['target_name'] or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
