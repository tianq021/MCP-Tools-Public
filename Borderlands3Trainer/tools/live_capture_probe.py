#!/usr/bin/env python3
"""Elevated live probe: capture currency pointer without modifying a value."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import runtime_features as features


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: live_capture_probe.py OUTPUT.json")
    output = Path(sys.argv[1])
    report: dict = {"ok": False}
    manager = features.FeatureManager(
        r"H:\SteamLibrary\steamapps\common\Borderlands 3\OakGame\Binaries\Win64\Borderlands3.exe"
    )
    try:
        with manager.open(True) as process:
            pointer = manager._capture_pointer(process, "currency", 20.0)
            definition = features.CAPTURES["currency"]
            match = manager._resolve_aob(
                process, "probe:currency", str(definition["pattern"])
            )
            hook = match + int(definition["hook_offset"])
            restored = process.read(hook, len(definition["original"])) == definition["original"]
            report = {
                "ok": True,
                "pid": manager.pid,
                "money": process.read_i32(pointer + 0x18),
                "eridium": process.read_i32(pointer + 0x58),
                "original_code_restored": restored,
            }
    except Exception as exc:
        report = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
