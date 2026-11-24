"""Apply/restore strictly validated BL4 runtime code patches.

Current experimental features for the verified local build:
  * skill points are not consumed (normal point-availability check remains)
  * custom integer kill XP multipliers 1..100 (gameplay validation pending)

The tool changes only the running Borderlands4.exe process. It does not patch
files on disk. Restarting the game restores original code automatically.
Intended for offline single-player testing only; it does not bypass third-party
tool detection or online restrictions.
"""
import argparse
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import os
import struct
from pathlib import Path
import sys
import time

ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
OUTPUT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from process_reader import Reader, game_pids, modules, open_process, close
from code_patches import SKILL_NO_SPEND, XP_KILL_MULTIPLIER, identify_variant, VERIFIED_SHA256
from patch_transaction import transact
from windows_patches import FrozenThreads, WindowsMemory
from apply_verified_adjustments import backup_saves, locate
from reflection import Reflection
from reward_hooks import inspect as inspect_reward, plan as plan_reward

_verified_files = {}


def verified_hash(path, reader):
    """Reuse only a successful hash for an unchanged file and process lifetime."""
    resolved = Path(path).resolve()
    def fingerprint():
        stat = resolved.stat()
        return (str(resolved).casefold(), stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns, reader.pid, reader.created)
    reader.require_alive()
    key = fingerprint()
    if key in _verified_files:
        return _verified_files[key]
    digest = sha256(resolved)
    if fingerprint() != key:
        raise RuntimeError('Executable changed during version verification')
    if digest != VERIFIED_SHA256:
        raise RuntimeError(f'Unsupported executable SHA256: {digest}')
    _verified_files.clear()
    _verified_files[key] = digest
    return digest

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().upper()


def state(reader, base):
    result = {}
    for patch in (SKILL_NO_SPEND, XP_KILL_MULTIPLIER):
        raw = reader.read(base + patch.rva, len(patch.original))
        result[patch.name] = {
            "rva": hex(patch.rva),
            "address": hex(base + patch.rva),
            "bytes": raw.hex(),
            "variant": identify_variant(patch, raw),
        }
    result['legacy_kill_xp_multiplier'] = result.pop('kill_xp_multiplier')
    xp = inspect_reward(reader, base, 'xp')
    result['kill_xp_multiplier'] = {**xp, 'variant': xp['multiplier']}
    result['currency_bonus'] = inspect_reward(reader, base, 'currency')
    return result


def journal(report):
    (OUTPUT / "logs").mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "logs" / "runtime_patches.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def execute(args, report):
    cash, eridium = getattr(args, 'cash_add', 0), getattr(args, 'eridium_add', 0)
    cancel = getattr(args, 'cancel_currency', False)
    if args.xp is not None and not 1 <= args.xp <= 100:
        raise ValueError('XP multiplier must be between 1 and 100')
    if any(not 0 <= value <= 1000000 for value in (cash, eridium)):
        raise ValueError('Currency bonus must be between 1 and 1000000')
    if cancel and (cash or eridium):
        raise ValueError('Cannot queue and cancel currency in the same operation')
    if (args.skill or args.xp is not None or args.backpack_add or cash or eridium or cancel) and not args.apply:
        raise RuntimeError("Add --apply to change the running game process.")
    if not 0 <= args.backpack_add <= 1000:
        raise ValueError('Backpack increment must be between 0 and 1000')
    config_path = OUTPUT / "trainer_config.json"
    if not config_path.exists():
        config_path = ROOT / "trainer_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    exe = config["expected_executable"]
    pids = game_pids()
    if len(pids) != 1:
        raise RuntimeError(f"Expected exactly one Borderlands4.exe; found {len(pids)}")
    with Reader(pids[0], exe) as reader:
        phase = time.perf_counter()
        report["executable_sha256"] = verified_hash(exe, reader)
        report.setdefault('timings', {})['version_seconds'] = round(time.perf_counter() - phase, 3)
        mains = [m for m in modules(reader.pid) if m["path"].casefold() == exe.casefold()]
        if len(mains) != 1:
            raise RuntimeError("Main module is ambiguous")
        base = mains[0]["base"]
        report["process"] = reader.identity()
        report["before"] = state(reader, base)
        pending = report['before']['currency_bonus']
        if (cash and pending.get('cash_pending')) or (eridium and pending.get('eridium_pending')):
            raise RuntimeError('A currency bonus is already pending; earn that currency or cancel first')
        requests = []
        verify = None
        local_metadata = {}
        if args.backpack_add or cash or eridium or (args.xp is not None and args.xp > 1):
            signatures = json.loads((ROOT / 'research/reflection_signatures.local.json').read_text(encoding='utf-8-sig'))
            if len(signatures['objects']) != 1 or len(signatures['names_nonpgo']) != 1:
                raise RuntimeError('Ambiguous reflection signatures')
            phase = time.perf_counter()
            fields, verify, local_metadata = locate(Reflection(reader, base, signatures), include_metadata=True)
            report['timings']['locate_seconds'] = round(time.perf_counter() - phase, 3)
        if args.backpack_add:
            backpack = next(f for f in fields if f['name'] == 'backpack')
            target = backpack['value'] + args.backpack_add
            if not 0 < target <= 1000:
                raise ValueError('Backpack target exceeds limit of 1000')
            requests.append((backpack['address'], {struct.pack('<i', backpack['value'])}, struct.pack('<i', target)))
            report['backpack'] = dict(before=backpack['value'], target=target)
            report['backup'] = str(backup_saves(Path(config['save_root']), OUTPUT))
        if (cash or eridium or (args.xp is not None and args.xp > 1)) and 'backup' not in report:
            report['backup'] = str(backup_saves(Path(config['save_root']), OUTPUT))
        for patch, variant in ((SKILL_NO_SPEND, args.skill), (XP_KILL_MULTIPLIER, args.xp)):
            if variant is not None:
                # Legacy XP bytes are always restored; new multipliers run at
                # the completed uint64 reward call, after the conversion.
                target = patch.original if patch is XP_KILL_MULTIPLIER else patch.variants[variant]
                requests.append((base + patch.rva, set(patch.variants.values()), target))
        if requests or cash or eridium or cancel:
            journal({**report, "status": "intent"})
            handle = open_process(0x100438, False, reader.pid)
            if not handle:
                raise C.WinError(C.get_last_error())
            try:
                reader.require_alive()
                backend = WindowsMemory(handle, reader)
                extra_ranges = []
                report['reward_plans'] = {}
                if args.xp is not None:
                    additions, ranges, info = plan_reward(backend, reader, base, 'xp',
                        multiplier=args.xp, restore=args.xp == 1, owner=local_metadata.get('controller', 0))
                    requests.extend(additions)
                    extra_ranges.extend(ranges)
                    report['reward_plans']['xp'] = info
                if cash or eridium or cancel:
                    additions, ranges, info = plan_reward(backend, reader, base, 'currency',
                        cash=cash, eridium=eridium, restore=cancel, owner=local_metadata.get('currency_manager', 0))
                    requests.extend(additions)
                    extra_ranges.extend(ranges)
                    report['reward_plans']['currency'] = info
                ranges = [(a, a + len(t)) for a, _, t in requests]
                with FrozenThreads(reader.pid, ranges + extra_ranges, report):
                    reader.require_alive()
                    if verify is not None:
                        verify()
                    transact(backend, requests, report)
            finally:
                close(handle)
        report["after"] = state(reader, base)


def run_request(args):
    """Shared non-printing entry point for the desktop app and CLI."""
    started = time.perf_counter()
    report = {"observed_at": datetime.now(timezone.utc).isoformat(),
              "requested": {"skill": args.skill, "xp": args.xp, "backpack_add": args.backpack_add}, "writes": []}
    for key in ('cash_add', 'eridium_add', 'cancel_currency'):
        if getattr(args, key, 0):
            report['requested'][key] = getattr(args, key)
    try:
        execute(args, report)
        report['status'] = 'success'
    except (Exception, KeyboardInterrupt) as error:
        report.update(status='error', error=f'{type(error).__name__}: {error}')
    finally:
        report.setdefault('timings', {})['total_seconds'] = round(time.perf_counter() - started, 3)
        try:
            journal(report)
        except OSError as error:
            report.update(status='error', journal_error=str(error))
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true", help="show current runtime patch state")
    ap.add_argument("--skill", choices=("on", "off"), help="toggle no-spend skill points")
    ap.add_argument("--xp", type=int, help="kill XP multiplier 1..100; 1 restores original code")
    ap.add_argument('--cash-add', type=int, default=0, help='one-shot bonus on next cash gain (experimental)')
    ap.add_argument('--eridium-add', type=int, default=0, help='one-shot bonus on next eridium gain (experimental)')
    ap.add_argument('--cancel-currency', action='store_true', help='remove currency wrapper and cancel pending bonuses')
    ap.add_argument("--apply", action="store_true", help="required before any memory write")
    ap.add_argument("--backpack-add", type=int, default=0, help="increase backpack capacity; backs up saves first")
    args = ap.parse_args()
    if not (args.status or args.skill or args.xp is not None or args.backpack_add or args.cash_add or args.eridium_add or args.cancel_currency):
        ap.print_help()
        return 0
    report = run_request(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'success' else 1


if __name__ == "__main__":
    sys.exit(main())
