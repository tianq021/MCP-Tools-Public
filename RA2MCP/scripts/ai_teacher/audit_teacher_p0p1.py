#!/usr/bin/env python3
"""P0/P1 offline audit for the native-AI-script teacher research (TASK-20261102-023).

Read-only with respect to game copies, vendor/, coordination/ and historical
reports. It only writes its JSON evidence under the new report directory
reports/teacher-native-p0p1-2026-11-02/evidence/.

No game, Bridge, capture or training process is started.

Sub-audits
----------
A. hashes / process state / protected assets
B. MIX loading chain: which archives contain aimd.ini / rulesmd.ini /
   mpmodesmd.ini / mpgodstoymd.ini, and in which byte range.
C. AI libraries: vanilla aimd (from ra2md.mix), WDZS packaged aimd
   (ExpandMD02.mix), CnCNet Extreme AI (local file).
D. Rules / mode overlays: loose vs packaged RulesMD, mpgodstoymd.
E. Spawner env: spawn.ini keys vs legacy spawner source capabilities; whether
   the Extreme AI map-code file can be active (spawnmap.ini content check).
F. Teacher trace 301-mapping audit with the frozen action contract
   (full_ground_v2p1 / ACTION_N=301) against the real 719-decision run.
G. Runtime event evidence from the 2026-11-02 validation game
   (raw_game_events.jsonl): house coverage, event types, is_executed.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import os
import pathlib
import re
import struct
import subprocess
import sys
import time
import zlib

ROOT = pathlib.Path(r"E:\RA2MCP")
OUT = ROOT / "reports" / "teacher-native-p0p1-2026-11-02"
EVID = OUT / "evidence"
GLOBAL_CACHE: dict = {}

CAPTURE = ROOT / "test-game-capture-01"
WDZS = ROOT / "test-game-wdzs"
WDZS_TEACHER = ROOT / "test-game-wdzs-teacher-01"
TESTGAME = ROOT / "test-game"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rec(path: pathlib.Path, **extra) -> dict:
    path = pathlib.Path(path)
    out = {"path": str(path.relative_to(ROOT)) if path.is_absolute() else str(path),
           "exists": path.is_file()}
    if path.is_file():
        st = path.stat()
        out.update({"size": st.st_size, "mtime": time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)), "sha256": sha_file(path)})
    out.update(extra)
    return out


def write_json(name: str, obj) -> pathlib.Path:
    EVID.mkdir(parents=True, exist_ok=True)
    target = EVID / name
    target.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def mix_crc(name: str) -> int:
    """RA2/YR modern (unencrypted) mix filename hash: CRC32 over name padded
    with NUL bytes to a multiple of 4, upper-cased."""
    raw = name.upper().encode("ascii")
    if len(raw) % 4:
        raw += b"\x00" * (4 - len(raw) % 4)
    return zlib.crc32(raw) & 0xFFFFFFFF


MIX_HEADER_RE_CACHE: dict = {}

# Content markers used when the historical mix tool used a non-standard
# filename padding for names whose length is not a multiple of 4. The four
# 4-byte-aligned names (aimd.ini) resolve by CRC everywhere; for the others we
# identify the payload by its first bytes and never guess the filename hash.
MIX_MARKERS = {
    "aimd.ini": [b"[TaskForces]", b"[TeamTypes]"],
    "rulesmd.ini": [b"Red Alert 2: Yuri's Revenge Rules", b"[InfantryTypes]"],
    "mpmodesmd.ini": [b"Define the multiplayer game modes"],
    "mpgodstoymd.ini": [b"God's Toy", b"MultiplayerAICM=4000"],
}


def read_mix_entries_by_content(path: pathlib.Path, markers: dict):
    """Identify mix members by payload content markers (filename hash opaque).

    Returns {logical_name: {status, offset, size, sha256, data}}; only small
    unencrypted mixes are scanned and the whole archive must stay well below
    the RAM/disk budget. Read-only."""
    out = {}
    with path.open("rb") as fh:
        header = fh.read(10)
        if len(header) < 10:
            return {"_error": "header truncated"}
        modern = struct.unpack_from("<H", header)[0] == 0
        if not modern:
            return {"_error": "classic hash unsupported"}
        flags = struct.unpack_from("<I", header)[0]
        if flags & 0x20000:
            return {"_error": "encrypted MIX unsupported"}
        count, size = struct.unpack_from("<HI", header, 4)
        if count > 2000 or size > 60_000_000:
            return {"_error": "mix too large for content scan",
                    "count": count, "payload_size": size}
        start = 4 + 6 + 12 * count
        fh.seek(10)
        entries = [struct.unpack("<III", fh.read(12)) for _ in range(count)]
        for crc, pos, length in entries:
            if length == 0 or length > 4_000_000:
                continue
            fh.seek(start + pos)
            data = fh.read(length)
            for name, marks in markers.items():
                if name in out:
                    continue
                if all(m in data for m in marks):
                    out[name] = {"status": "FOUND_BY_CONTENT", "crc": f"{crc:08x}",
                                 "offset": start + pos, "size": length,
                                 "sha256": sha_bytes(data), "data": data}
    return out


def read_mix_entries(path: pathlib.Path, names):
    """Return {name: {status,...}} from a RA2/YR modern MIX. Never writes."""
    key = (str(path), tuple(names))
    if key in MIX_HEADER_RE_CACHE:
        return MIX_HEADER_RE_CACHE[key]
    targets = {mix_crc(n): n for n in names}
    out = {}
    with path.open("rb") as fh:
        header = fh.read(10)
        if len(header) < 10:
            out["_error"] = "header truncated"
            return out
        modern = struct.unpack_from("<H", header)[0] == 0
        if not modern:
            out["_error"] = "classic hash unsupported"
            return out
        flags = struct.unpack_from("<I", header)[0]
        if flags & 0x20000:
            out["_error"] = "encrypted MIX unsupported"
            return out
        count, size = struct.unpack_from("<HI", header, 4)
        start = 4 + 6 + 12 * count
        fh.seek(10)
        entries = [struct.unpack("<III", fh.read(12)) for _ in range(count)]
        out["_mix"] = {"count": count, "payload_size": size, "entries_bytes": start}
        for crc, pos, length in entries:
            if crc not in targets:
                continue
            name = targets[crc]
            fh.seek(start + pos)
            data = fh.read(length)
            out[name] = {"status": "FOUND", "crc": f"{crc:08x}",
                         "offset": start + pos, "size": length, "sha256": sha_bytes(data),
                         "data": data}
    for name in names:
        out.setdefault(name, {"status": "NOT_FOUND"})
    MIX_HEADER_RE_CACHE[key] = out
    return out


def decode_ini(data: bytes):
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("cannot decode ini")


def parse_ini_text(text: str) -> dict:
    sections: dict[str, dict[str, str]] = {}
    lines: dict[tuple, int] = {}
    dupes = []
    current = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"\[([^]]+)\]", line)
        if m:
            current = m.group(1).upper()
            sections.setdefault(current, {})
            continue
        if current is not None and "=" in line:
            k, v = (p.strip() for p in line.split("=", 1))
            k = k.casefold()
            if k in sections[current]:
                dupes.append({"section": current, "key": k, "line": number})
            sections[current][k] = v
            lines[(current, k)] = number
    return {"sections": sections, "lines": lines, "duplicates": dupes}


def parse_ini_bytes(data: bytes) -> dict:
    text, enc = decode_ini(data)
    parsed = parse_ini_text(text)
    parsed.update({"encoding": enc, "sha256": sha_bytes(data), "chars": len(text)})
    return parsed


def numbered(section):
    items = []
    for k, v in section.items():
        try:
            items.append((int(k), v))
        except ValueError:
            continue
    return sorted(items)


def registered(sections, name):
    return list(dict.fromkeys(v.upper() for v in sections.get(name.upper(), {}).values()))


# ---------------------------------------------------------------------------
# A. hashes / anchors
# ---------------------------------------------------------------------------
PROTECTED_EXPECT = {
    "reports/rl/stage-A-round1-20261030-153225/model.zip":
        "f650f4fd1a21f236116426bfe9a38b71d1c63334836e2303337f0ef7127cea8a",
    "reports/rl/stage-A-round2-20261030-155824/model.zip":
        "c340d9ccead7c9a1b9add2a37fee990e2caee5c3c7c2c316dc79d2010c2ea542",
    "reports/rl/stage-A-round3-20261030-162536/model.zip":
        "0c9c75d7ecc4c360b6c165b1b224f10491bc13dad9aeea4b3b84abd8d6511ce8",
    "reports/rl/ppo-shared-20261030-102633/model.zip":
        "f8380e8bd55d2454179ca1955682f1d869356f03d51fa19c11a2f6a4dc6f1b56",
    "config/rewards-combat.json":
        "2aced7dd305716737711da10febb16e33093467284569abaa7f52912ae021e8b",
}


def audit_hashes() -> dict:
    checks = {}
    for rel, expected in PROTECTED_EXPECT.items():
        path = ROOT / rel
        actual = sha_file(path) if path.is_file() else None
        checks[rel] = {"expected": expected, "actual": actual,
                       "match": actual == expected}
    # running game / bridge processes
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process -Name 'gamemd*','yuri*','python' -ErrorAction SilentlyContinue | "
         "Select-Object -ExpandProperty ProcessName) -join ','"],
        capture_output=True, text=True)
    procs = [p for p in ps.stdout.strip().split(",") if p.strip()]
    game_like = [p for p in procs if p.lower().startswith(("gamemd", "yuri"))]
    # E:/hj presence only (never written by this script)
    hj = pathlib.Path(r"E:\hj")
    return {"protected_hashes": checks,
            "all_protected_match": all(v["match"] for v in checks.values()),
            "processes_seen": procs,
            "game_process_running": bool(game_like),
            "e_hj_exists": hj.exists(),
            "no_game_launched_by_audit": not game_like}


# ---------------------------------------------------------------------------
# B. MIX loading chain
# ---------------------------------------------------------------------------
MIX_TARGETS = ["aimd.ini", "rulesmd.ini", "ai.ini", "rules.ini",
               "mpmodesmd.ini", "mpgodstoymd.ini", "artmd.ini", "ai(md).ini"]


def audit_capture_rules() -> dict:
    """Effective rules source of the spawner environment.

    test-game-capture-01 has no loose RulesMD; cncnet.mix ships an unencrypted
    rulesmd.ini (found by content marker) which shadows the encrypted ra2md.mix
    copy in the CnCNet load chain (exact MIX precedence = INFERRED, not proven
    here)."""
    result = {}
    hits = read_mix_entries_by_content(
        CAPTURE / "cncnet.mix", {"rulesmd.ini": MIX_MARKERS["rulesmd.ini"]})
    r = hits.get("rulesmd.ini")
    if isinstance(r, dict) and r.get("status") == "FOUND_BY_CONTENT":
        text, enc = decode_ini(r["data"])
        parsed = parse_ini_text(text)
        result["cncnet_mix_rulesmd"] = {
            "source": "test-game-capture-01/cncnet.mix!rulesmd.ini",
            "size": len(r["data"]), "sha256": sha_bytes(r["data"]),
            "encoding": enc, "sections": len(parsed["sections"]),
            "produce_cash_count": text.count("ProduceCash"),
            "general_ai": extract_general_ai(parsed),
            "software_version_line": next((ln.strip() for ln in text.splitlines()
                                           if "SoftwareVersion" in ln), None),
        }
    return result


def audit_mix_chain() -> dict:
    result = {}
    for label, gamedir in (("test-game-capture-01", CAPTURE),
                           ("test-game-wdzs", WDZS),
                           ("test-game-wdzs-teacher-01", WDZS_TEACHER),
                           ("test-game", TESTGAME)):
        if not gamedir.is_dir():
            result[label] = {"exists": False}
            continue
        mixes = sorted([p for p in gamedir.glob("*.mix") if p.is_file()],
                       key=lambda p: p.name.lower())
        entry = {"game_dir": str(gamedir), "mix_files": [], "found": {}}
        for mix in mixes:
            mixinfo = {"name": mix.name, "size": mix.stat().st_size,
                       "sha256_16": sha_file(mix)[:16]}
            try:
                hits = read_mix_entries(mix, MIX_TARGETS)
            except Exception as exc:  # noqa: BLE001
                mixinfo["error"] = f"{type(exc).__name__}: {exc}"
                entry["mix_files"].append(mixinfo)
                continue
            mixinfo["mix"] = hits.get("_mix")
            mixinfo["error"] = hits.get("_error")
            found_here = {}
            for name in MIX_TARGETS:
                rec_hit = hits.get(name)
                if not rec_hit or rec_hit.get("status") != "FOUND":
                    continue
                found_here[name] = {k: rec_hit[k] for k in
                                    ("offset", "size", "sha256", "crc")}
                entry["found"].setdefault(name, []).append(
                    {"mix": mix.name, "size": rec_hit["size"],
                     "sha256": rec_hit["sha256"], "offset": rec_hit["offset"],
                     "method": "CRC_NAME"})
            if not hits.get("_error"):
                try:
                    by_content = read_mix_entries_by_content(mix, MIX_MARKERS)
                except Exception as exc:  # noqa: BLE001
                    by_content = {"_error": f"{type(exc).__name__}: {exc}"}
                for name in MIX_MARKERS:
                    rec_hit = by_content.get(name)
                    if not rec_hit or not isinstance(rec_hit, dict):
                        continue
                    if name in found_here:
                        continue
                    found_here[name] = {
                        "offset": rec_hit["offset"], "size": rec_hit["size"],
                        "sha256": rec_hit["sha256"], "crc": rec_hit["crc"]}
                    entry["found"].setdefault(name, []).append(
                        {"mix": mix.name, "size": rec_hit["size"],
                         "sha256": rec_hit["sha256"],
                         "offset": rec_hit["offset"],
                         "method": "CONTENT_MARKER"})
                if by_content.get("_error"):
                    mixinfo["content_scan_error"] = by_content["_error"]
            if found_here:
                mixinfo["found"] = found_here
            entry["mix_files"].append(mixinfo)
        result[label] = entry
    return result


# ---------------------------------------------------------------------------
# C. AI libraries
# ---------------------------------------------------------------------------
# ModEnc ScriptTypes/ScriptActions semantics (public documentation, REPORTED;
# not runtime-verified against this engine build).
SCRIPT_ACTION_DOC = {
    0: ("ATTACK_QUARRY", "attack any target matching quarry type"),
    1: ("ATTACK_WAYPOINT", "attack structure at waypoint / infiltrate/capture"),
    2: ("OBSOLETE_BERZERK", "obsolete"),
    3: ("MOVE_WAYPOINT", "move to waypoint"),
    4: ("MOVE_CELL", "move into specific cell"),
    5: ("GUARD_AREA", "guard for time"),
    6: ("FLOW_JUMP", "jump to script line"),
    7: ("FORCE_WIN", "force owner win"),
    8: ("UNLOAD", "unload transport"),
    9: ("DEPLOY", "deploy units able to deploy"),
    10: ("FOLLOW_FRIENDLIES", "follow nearest friendly"),
    11: ("SET_MISSION", "assign mission parameter"),
    12: ("SET_GLOBAL_VAR", "set global variable"),
    13: ("IDLE_ANIM", "play idle animation"),
    14: ("LOAD_TRANSPORT", "load onto transport"),
    15: ("SPY_STRUCTURE", "spy on structure at waypoint"),
    16: ("PATROL_WAYPOINT", "patrol to waypoint"),
    17: ("FLOW_CHANGE_SCRIPT", "change script (obsolete/breakable)"),
    18: ("FLOW_CHANGE_TEAM", "change team (index-based, breakable)"),
    19: ("PANIC", "panic / prone"),
    20: ("CHANGE_HOUSE", "change house ownership"),
    21: ("SCATTER", "scatter"),
    22: ("AFRAID_RUN_SHROUD", "run to shroud"),
    23: ("FORCE_LOSS", "force owner loss"),
    24: ("PLAY_SPEECH", "play EVA speech"),
    25: ("PLAY_SOUND", "play sound"),
    26: ("PLAY_MOVIE", "play movie"),
    27: ("PLAY_THEME", "play theme"),
    28: ("REDUCE_ORE", "reduce ore in cell"),
    29: ("BEGIN_PRODUCTION", "start AI auto-production"),
    30: ("FORCE_SALE", "sell all structures"),
    31: ("SUICIDE", "destroy self"),
    32: ("START_STORM", "weather storm intro"),
    33: ("END_STORM", "end storm"),
    34: ("CENTER_MAP", "center camera on team"),
    35: ("SHROUD_MAP", "shroud map for time"),
    36: ("REVEAL_MAP", "reveal map for time"),
    37: ("DELETE_MEMBERS", "delete team members"),
    38: ("CLEAR_GLOBAL_VAR", "clear global variable"),
    39: ("SET_LOCAL_VAR", "set local variable"),
    40: ("CLEAR_LOCAL_VAR", "clear local variable"),
    41: ("UNPANIC", "unpanic"),
    42: ("CHANGE_FACING", "change facing"),
    43: ("WAIT_FULLY_LOADED", "wait until transport fully loaded"),
    44: ("TRUCK_B_TO_A", "graphics-only demo truck"),
    45: ("TRUCK_A_TO_B", "graphics-only demo truck"),
    46: ("ATTACK_STRUCTURE_BWP", "attack enemy structure by building-with-property id"),
    47: ("MOVE_TO_STRUCTURE_BWP", "move to enemy structure by bwp id"),
    48: ("SCOUT", "scout random direction"),
    49: ("REGISTER_SUCCESS", "register script success for AI trigger weight"),
    50: ("FLASH", "flash members"),
    51: ("PLAY_ANIM", "play animation"),
    52: ("TALK_BUBBLE", "display talk bubble"),
    53: ("GATHER", "gather at enemy base"),
    54: ("REGROUP", "regroup at friendly base"),
    55: ("IRON_CURTAIN", "activate Iron Curtain on team"),
    56: ("CHRONO_TO_STRUCTURE", "chronoshift to enemy structure"),
    57: ("CHRONO_TO_QUARRY", "chronoshift to target type"),
    58: ("MOVE_TO_FRIENDLY_STRUCTURE", "move adjacent to friendly structure"),
    59: ("ATTACK_STRUCTURE_WAYPOINT", "attack structure at waypoint"),
    60: ("ENTER_GRINDER", "enter grinder"),
    61: ("ENTER_TANK_BUNKER", "occupy tank bunker"),
    62: ("ENTER_BIOREACTOR", "enter bio reactor"),
    63: ("ENTER_BATTLE_BUNKER", "occupy battle bunker"),
    64: ("GARRISON", "garrison structure"),
}

# 301 `full_ground_v2p1` only models: DEPLOY(1) / PRODUCE / PLACE /
# selection-bound STOP / 8-dir x10 MOVE / ATTACK visible target slot /
# SCOUT / RETREAT. Classification below is about the *semantic family*.
SCRIPT_301_CLASS = {
    0: "ATTACK_FAMILY", 1: "ATTACK_FAMILY", 3: "MOVE_FAMILY", 4: "MOVE_FAMILY",
    5: "GUARD_FAMILY", 9: "DEPLOY", 10: "FOLLOW_FAMILY", 11: "MISSION_FAMILY",
    16: "MOVE_FAMILY", 21: "SCATTER_FAMILY", 42: "FACING_FAMILY",
    46: "ATTACK_FAMILY", 47: "MOVE_FAMILY", 48: "SCOUT_FAMILY",
    53: "GATHER_FAMILY", 54: "REGROUP_FAMILY", 58: "MOVE_FAMILY",
    59: "ATTACK_FAMILY",
}
SCRIPT_301_REPRESENTABLE = {
    "ATTACK_FAMILY": "PARTIAL (quarry/BwP target semantics not in 301)",
    "MOVE_FAMILY": "PARTIAL (301 = 8-dir x10 cells, no waypoints/exact cells)",
    "GUARD_FAMILY": "NO (no timed guard action)",
    "DEPLOY": "YES_IF_MCV (301 deploy = own MCV only)",
    "FOLLOW_FAMILY": "NO",
    "MISSION_FAMILY": "PARTIAL (only STOP/MOVE/ATTACK-ish missions)",
    "SCATTER_FAMILY": "NO",
    "FACING_FAMILY": "NO",
    "SCOUT_FAMILY": "PARTIAL (301 SCOUT = move to public enemy spawn)",
    "GATHER_FAMILY": "NO",
    "REGROUP_FAMILY": "NO",
    "FLOW": "NO (control flow not a student action)",
    "VARS": "NO",
    "NEWS_UX": "NO",
    "SPECIAL_ENGINE": "NO (transport/superweapon/infiltration/garrison)",
    "FORCE_END": "NO",
    "OBSOLETE": "NO",
    "UNKNOWN": "UNKNOWN",
}
CLASS_OF_CODE = {}
for code, (name, desc) in SCRIPT_ACTION_DOC.items():
    if code in SCRIPT_301_CLASS:
        cls = SCRIPT_301_CLASS[code]
    elif code in (6, 17, 18, 49):
        cls = "FLOW"
    elif code in (12, 38, 39, 40):
        cls = "VARS"
    elif code in (24, 25, 26, 27, 34, 35, 36, 50, 51, 52, 13, 19, 41):
        cls = "NEWS_UX"
    elif code in (7, 23):
        cls = "FORCE_END"
    elif code == 2:
        cls = "OBSOLETE"
    elif code in (8, 14, 15, 28, 29, 30, 31, 32, 33, 37, 43, 44, 45,
                  55, 56, 57, 60, 61, 62, 63, 64):
        cls = "SPECIAL_ENGINE"
    else:
        cls = "UNKNOWN"
    CLASS_OF_CODE[code] = cls


def compile_ai(sections: dict) -> dict:
    tf = registered(sections, "TaskForces")
    st = registered(sections, "ScriptTypes")
    tt = registered(sections, "TeamTypes")
    at = sections.get("AITRIGGERTYPES", {})
    out = {
        "counts": {"taskforces": len(tf), "scripts": len(st), "teams": len(tt),
                   "aitriggers": len(at)},
        "taskforces": [], "scripts": [], "teams": [], "triggers": [],
        "issues": [],
    }
    # TaskForces
    for ident in tf:
        sec = sections.get(ident, {})
        units = []
        for num, value in numbered(sec):
            parts = [p.strip() for p in value.split(",")]
            if len(parts) >= 2 and parts[0].isdigit():
                units.append({"count": int(parts[0]), "type_id": parts[1].upper()})
            else:
                out["issues"].append({"id": ident, "kind": "BAD_TASKFORCE_ROW",
                                      "raw": value, "line": None})
        out["taskforces"].append({"id": ident, "name": sec.get("name"),
                                  "group": sec.get("group"), "units": units})
    # Scripts + action inventory
    action_counter = collections.Counter()
    class_counter = collections.Counter()
    script_detail = []
    for ident in st:
        sec = sections.get(ident, {})
        actions = []
        for num, value in numbered(sec):
            parts = [p.strip() for p in value.split(",", 1)]
            try:
                code = int(parts[0])
                param = int(parts[1]) if len(parts) > 1 else None
            except (ValueError, IndexError):
                code = None
                param = None
            cls = CLASS_OF_CODE.get(code, "UNKNOWN")
            action_counter[code] += 1
            class_counter[cls] += 1
            actions.append({"step": num, "code": code, "param": param,
                            "class": cls,
                            "doc": SCRIPT_ACTION_DOC.get(code, ("UNKNOWN", ""))[0],
                            "line": sections.get("__lines__", {}).get((ident, str(num)))})
        script_detail.append({"id": ident, "name": sec.get("name"),
                              "actions": actions})
    out["scripts"] = script_detail
    out["action_codes"] = {str(k): v for k, v in sorted(
        action_counter.items(), key=lambda kv: (kv[0] is None, kv[0]))}
    out["action_classes"] = dict(class_counter)
    out["action_coverage"] = {
        cls: {"count": cnt, "301": SCRIPT_301_REPRESENTABLE.get(cls, "UNKNOWN")}
        for cls, cnt in class_counter.items()}
    # Teams
    team_field_counter = collections.Counter()
    for ident in tt:
        sec = sections.get(ident, {})
        tf_ref = resolve_ref(sec.get("taskforce"), tf)
        st_ref = resolve_ref(sec.get("script"), st)
        if not tf_ref or not st_ref:
            out["issues"].append({"id": ident, "kind": "BROKEN_TEAM_REFERENCE",
                                  "taskforce": sec.get("taskforce"),
                                  "script": sec.get("script")})
        out["teams"].append({
            "id": ident, "name": sec.get("name"),
            "taskforce": tf_ref or sec.get("taskforce"),
            "script": st_ref or sec.get("script"),
            "side": sec.get("side"), "house": sec.get("house"),
            "priority": sec.get("priority"), "max": sec.get("max"),
            "group": sec.get("group"), "autocreate": sec.get("autocreate"),
            "recruiter": sec.get("recruiter"), "aggressive": sec.get("aggressive"),
            "is_base_defense": sec.get("isbasedefense")})
        team_field_counter.update(sec.keys())
    out["team_fields"] = dict(team_field_counter)
    # AITriggerTypes: Name,Team1,OwnerHouse,TechLevel,ConditionType,
    # ConditionObject,Comparator,StartW,MinW,MaxW,IsForSkirmish,unused,Side,
    # IsBaseDefense,Team2,EnabledInE,EnabledInM,EnabledInH
    owner_house = collections.Counter()
    cond_type = collections.Counter()
    side_counter = collections.Counter()
    for ident, raw in at.items():
        parts = [p.strip() for p in raw.split(",")]
        name = parts[0] if parts else None
        team1 = parts[1] if len(parts) > 1 else None
        owner = parts[2] if len(parts) > 2 else None
        ctype = parts[4] if len(parts) > 4 else None
        cobj = parts[5] if len(parts) > 5 else None
        startw = parts[7] if len(parts) > 7 else None
        skew = parts[10] if len(parts) > 10 else None
        side = parts[12] if len(parts) > 12 else None
        en = [parts[15], parts[16], parts[17]] if len(parts) > 17 else []
        owner_house[owner] += 1
        cond_type[ctype] += 1
        side_counter[side] += 1
        resolved_team = resolve_ref(team1, tt)
        out["triggers"].append({
            "id": ident, "name": name, "team1": resolved_team or team1,
            "team1_resolved": bool(resolved_team),
            "owner_house": owner, "condition_type": ctype,
            "condition_object": cobj, "starting_weight": startw,
            "skirmish": skew, "side": side,
            "enabled_in_e/m/h": en})
        if not resolved_team:
            out["issues"].append({"id": ident, "kind": "BROKEN_TRIGGER_TEAM1",
                                  "raw": raw})
    out["trigger_owner_house"] = dict(owner_house)
    out["trigger_condition_types"] = dict(cond_type)
    out["trigger_sides"] = dict(side_counter)
    out["general_ai_keys"] = {k: v for k, v in sections.get("AI", {}).items()}
    out["general_keys"] = {k: v for k, v in sections.get("GENERAL", {}).items()}
    out["other_sections"] = sorted(
        s for s in sections
        if s not in {i.upper() for i in tf + st + tt}
        and s not in {"AITRIGGERTYPES", "TASKFORCES", "SCRIPTTYPES", "TEAMTYPES"})
    return out


def resolve_ref(value, registry):
    if not value:
        return None
    v = value.upper()
    if v in registry:
        return v
    if value.isdigit():
        idx = int(value)
        if 0 <= idx < len(registry):
            return registry[idx]
    return None


def section_lines_from_text(text: str) -> dict:
    """Return {(SECTION, key): line} for script source-line evidence."""
    lines = {}
    current = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"\[([^]]+)\]", line)
        if m:
            current = m.group(1).upper()
            continue
        if current is not None and "=" in line:
            k, _v = (p.strip() for p in line.split("=", 1))
            lines[(current, k.casefold())] = number
    return lines


def audit_ai_libraries(mix_chain: dict) -> dict:
    result = {}
    # --- CnCNet mix aimd (the effective AI library of the spawner env) --------
    for label, gamedir in (("cncnet_mix_aimd", CAPTURE), ("testgame_cncnet_mix_aimd", TESTGAME)):
        hits = read_mix_entries(gamedir / "cncnet.mix", ["aimd.ini"])
        v = hits.get("aimd.ini")
        if v and v.get("status") == "FOUND":
            data = v["data"]
            parsed = parse_ini_bytes(data)
            lib = compile_ai(parsed["sections"])
            lib.update({"source": f"{gamedir.name}/cncnet.mix!aimd.ini",
                        "size": len(data), "sha256": sha_bytes(data),
                        "encoding": parsed["encoding"],
                        "duplicates": parsed["duplicates"]})
            result[label] = lib
    # --- WDZS packaged aimd from ExpandMD02.mix ---
    w_hits = read_mix_entries(WDZS / "ExpandMD02.mix", ["aimd.ini"])
    w = w_hits.get("aimd.ini")
    if w and w.get("status") == "FOUND":
        data = w["data"]
        parsed = parse_ini_bytes(data)
        lib = compile_ai(parsed["sections"])
        lib.update({"source": "test-game-wdzs/ExpandMD02.mix!aimd.ini",
                    "size": len(data), "sha256": sha_bytes(data),
                    "encoding": parsed["encoding"],
                    "duplicates": parsed["duplicates"]})
        result["wdzs_packaged_aimd"] = lib
        # registered unit ids used by taskforces (mod-feature usage check)
        used = collections.Counter()
        for tf in lib["taskforces"]:
            for u in tf["units"]:
                used[u["type_id"]] += u["count"]
        result["wdzs_packaged_aimd"]["taskforce_unit_type_ids"] = dict(used.most_common())
    # --- cross-library comparison (cncnet vs WDZS packaged) ---
    a = result.get("cncnet_mix_aimd")
    b = result.get("wdzs_packaged_aimd")
    if a and b:
        def ids(lib, key):
            return {x["id"] for x in lib[key]}
        cmp = {}
        for key in ("taskforces", "scripts", "teams", "triggers"):
            sa, sb = ids(a, key), ids(b, key)
            cmp[key] = {"cncnet": len(sa), "wdzs": len(sb),
                        "shared": len(sa & sb), "only_cncnet": len(sa - sb),
                        "only_wdzs": len(sb - sa)}
        result["cncnet_vs_wdzs"] = cmp
        # unknown script action codes in the WDZS library
        unknown = collections.Counter()
        for s in b["scripts"]:
            for act in s["actions"]:
                if act["class"] == "UNKNOWN":
                    unknown[act["code"]] += 1
        result["wdzs_unknown_action_codes"] = {
            str(k): v for k, v in unknown.most_common()}
    # --- Extreme AI local file ---
    extreme = CAPTURE / "INI" / "Game Options" / "AI" / "Extreme AI.ini"
    if extreme.is_file():
        parsed = parse_ini_bytes(extreme.read_bytes())
        lib = compile_ai(parsed["sections"])
        lib.update({"source": "test-game-capture-01/INI/Game Options/AI/Extreme AI.ini",
                    "size": extreme.stat().st_size,
                    "sha256": sha_file(extreme), "encoding": parsed["encoding"],
                    "duplicates": parsed["duplicates"]})
        result["extreme_ai_local"] = lib
        # sections not belonging to the four AI registries (they must be
        # scoped into the map / rules / general overrides by the client)
        extra = sorted(s for s in parsed["sections"]
                       if s not in {"TASKFORCES", "SCRIPTTYPES", "TEAMTYPES",
                                    "AITRIGGERTYPES"})
        lib["non_registry_sections"] = extra
    for name in ("Brutal AI.ini", "No Change.ini", "Extreme AI Beta.ini"):
        p = CAPTURE / "INI" / "Game Options" / "AI" / name
        if p.is_file():
            result.setdefault("other_ai_options", {})[name] = {
                "size": p.stat().st_size, "sha256": sha_file(p)}
    return result


# ---------------------------------------------------------------------------
# D. rules / mode overlays
# ---------------------------------------------------------------------------
AI_RULE_KEYS = (
    "multiplayerAICM".casefold(), "disableAIParallelProduction".casefold(),
    "enableAIBuildLimitation".casefold(), "AIBasePlanningCount".casefold(),
    "teamDelays".casefold(), "AIHateDelays".casefold(),
    "AIVirtualPurifiers".casefold(), "AIFireSale".casefold(),
    "AIFireSaleDelay".casefold(), "AISafeDistance".casefold(),
    "AIMinorSuperReadyPercent".casefold(), "AITriggerSuccessWeightDelta".casefold(),
    "AITriggerFailureWeightDelta".casefold(), "AITriggerTrackRecordCoefficient".casefold(),
    "AISlaveMinerNumber".casefold(), "AIIonCannonConYardValue".casefold(),
)


def extract_general_ai(parsed: dict) -> dict:
    out = {}
    for sec in ("GENERAL", "AI"):
        for k, v in parsed["sections"].get(sec, {}).items():
            if k in AI_RULE_KEYS or k.startswith("aibaseplanningcount"):
                out[f"{sec}.{k}"] = v
    return out


def audit_rules_and_modes() -> dict:
    result = {}
    # packaged RulesMD in ExpandMD02.mix (filename padding is tool-specific:
    # identify by content marker, never guess the CRC input)
    hits = read_mix_entries_by_content(
        WDZS / "ExpandMD02.mix", {"rulesmd.ini": MIX_MARKERS["rulesmd.ini"]})
    r = hits.get("rulesmd.ini")
    if r and r.get("status") == "FOUND_BY_CONTENT":
        text, enc = decode_ini(r["data"])
        parsed = parse_ini_text(text)
        result["packaged_rules"] = {
            "source": "test-game-wdzs/ExpandMD02.mix!rulesmd.ini",
            "size": len(r["data"]), "sha256": sha_bytes(r["data"]),
            "encoding": enc,
            "produce_cash_count": text.count("ProduceCash"),
            "sections": len(parsed["sections"]),
            "general_ai": extract_general_ai(parsed),
        }
    # loose files
    loose = {}
    for label, path in (("wdzs_loose_rules", WDZS / "RulesMD.ini"),
                        ("wdzs_original_rules_copy", WDZS / "RulesMD_ini" / "RulesMD.ini"),
                        ("wdzs_cash_variant_copy",
                         WDZS / "RulesMD_ini" / "RulesMD_三阵营发电厂加钱版.ini"),
                        ("capture_loose_rules", CAPTURE / "RulesMD.ini")):
        if path.is_file():
            text, enc = decode_ini(path.read_bytes())
            parsed = parse_ini_text(text)
            loose[label] = {
                "path": str(path.relative_to(ROOT)), "size": path.stat().st_size,
                "sha256": sha_file(path), "encoding": enc,
                "produce_cash_count": text.count("ProduceCash"),
                "sections": len(parsed["sections"]),
                "general_ai": extract_general_ai(parsed),
            }
        else:
            loose[label] = {"path": str(path.relative_to(ROOT)), "exists": False}
    result["loose_rules"] = loose
    # compare packaged vs original vs loose
    pk = result.get("packaged_rules", {}).get("general_ai", {})
    orig = loose.get("wdzs_original_rules_copy", {}).get("general_ai", {})
    cur = loose.get("wdzs_loose_rules", {}).get("general_ai", {})
    diff = {}
    for k in sorted(set(pk) | set(orig) | set(cur)):
        if pk.get(k) != orig.get(k) or cur.get(k) != orig.get(k):
            diff[k] = {"packaged": pk.get(k), "original_loose": orig.get(k),
                       "current_loose": cur.get(k)}
    result["general_ai_diff"] = diff
    # mode overlay
    mode = {}
    p = WDZS / "mpgodstoymd.ini"
    if p.is_file():
        text, enc = decode_ini(p.read_bytes())
        parsed = parse_ini_text(text)
        mode["loose_mpgodstoymd"] = {
            "path": "test-game-wdzs/mpgodstoymd.ini", "size": p.stat().st_size,
            "sha256": sha_file(p), "encoding": enc,
            "sections": {k: v for k, v in parsed["sections"].items()},
        }
    hits = read_mix_entries_by_content(
        WDZS / "ExpandMD02.mix", {"mpgodstoymd.ini": MIX_MARKERS["mpgodstoymd.ini"]})
    m = hits.get("mpgodstoymd.ini")
    if m and m.get("status") == "FOUND_BY_CONTENT":
        mode["packaged_mpgodstoymd"] = {
            "source": "test-game-wdzs/ExpandMD02.mix!mpgodstoymd.ini",
            "size": m["size"], "sha256": m["sha256"],
            "matches_loose": mode.get("loose_mpgodstoymd", {}).get("sha256") == m["sha256"],
        }
    # mode registration table (mpmodesmd) by content marker
    reg = read_mix_entries_by_content(
        WDZS / "ExpandMD02.mix", {"mpmodesmd.ini": MIX_MARKERS["mpmodesmd.ini"]})
    reg_hit = reg.get("mpmodesmd.ini")
    if reg_hit and reg_hit.get("status") == "FOUND_BY_CONTENT":
        text, enc = decode_ini(reg_hit["data"])
        mode["packaged_mpmodesmd"] = {
            "source": "test-game-wdzs/ExpandMD02.mix!mpmodesmd.ini",
            "size": reg_hit["size"], "sha256": reg_hit["sha256"],
            "encoding": enc,
            "register_line_13": next((ln.strip() for ln in text.splitlines()
                                      if ln.strip().startswith("13=")), None),
            "lines": len(text.splitlines()),
        }
    # loose mode registration files (if any) would shadow the packaged one
    mode["loose_mpmodesmd_present"] = (WDZS / "mpmodesmd.ini").is_file()
    result["mode_overlay"] = mode
    return result


# ---------------------------------------------------------------------------
# E. spawner env: spawn.ini keys vs spawner source / map-code content
# ---------------------------------------------------------------------------
def binary_strings(path: pathlib.Path, needles) -> dict:
    if not path.is_file():
        return {"exists": False}
    raw = path.read_bytes()
    text = raw.decode("latin-1", errors="ignore")
    return {"exists": True, "size": len(raw),
            "contains": {n: (n in text) for n in needles}}


def audit_spawner_env(mix_chain: dict) -> dict:
    result = {}
    # byte-level evidence that the game exe looks for AIMD.INI and that the
    # CnCNet spawner DLL knows cncnet.mix (the archive that carries the
    # effective aimd/rules in this environment).
    result["binary_string_evidence"] = {
        "spawner_exe": binary_strings(
            CAPTURE / "gamemd-spawn-ra2yrcpp.exe", ["AIMD.INI", "aimd.ini"]),
        "cncnet_spawner_dll": binary_strings(
            CAPTURE / "CnCNet-Spawner.dll", ["cncnet.mix", "CNCNET.MIX"]),
        "bridge_dll": binary_strings(
            CAPTURE / "libra2yrcpp.dll", ["AIMD.INI", "cncnet.mix"]),
    }
    spawn = CAPTURE / "spawn.ini"
    if spawn.is_file():
        text, enc = decode_ini(spawn.read_bytes())
        parsed = parse_ini_text(text)
        result["spawn_ini"] = {
            "path": "test-game-capture-01/spawn.ini", "size": spawn.stat().st_size,
            "sha256": sha_file(spawn), "encoding": enc,
            "sections": sorted(parsed["sections"]),
            "settings_keys": sorted(parsed["sections"].get("SETTINGS", {})),
            "has_ai_modifier_key": any(
                "ai" in k or "mapcode" in k
                for k in parsed["sections"].get("SETTINGS", {})),
        }
    spawnmap = CAPTURE / "spawnmap.ini"
    if spawnmap.is_file():
        text, enc = decode_ini(spawnmap.read_bytes())
        result["spawnmap_ini"] = {
            "path": "test-game-capture-01/spawnmap.ini",
            "size": spawnmap.stat().st_size, "sha256": sha_file(spawnmap),
            "encoding": enc,
            "ignore_global_ai_triggers": _ini_value(text, "Basic", "IgnoreGlobalAITriggers"),
            "has_razer_sections": "RAZER" in text.upper(),
            "has_extreme_ai_marker": "EXTREME AI" in text.upper(),
            "aitriggertypesenable_count": len(
                re.findall(r"(?m)^[0-9A-Fa-f]{8}(?:-G)?\s*=",
                           text.split("[AITriggerTypesEnable]", 1)[-1]
                           if "[AITriggerTypesEnable]" in text else "")),
            "has_extreme_ai_general_override": "AllowBypassBuildLimit" in text,
        }
    # spawner source capability: only [Settings] read (evidence file lines)
    src = ROOT / "vendor" / "yr-patches-src" / "src_spawner_load_spawn.c"
    if src.is_file():
        code = src.read_text(encoding="utf-8", errors="replace")
        result["legacy_spawner_source"] = {
            "path": "vendor/yr-patches-src/src_spawner_load_spawn.c",
            "sha256": sha_file(src),
            "reads_only_settings": "INIClass__Get" in code and "Game Options" not in code,
            "reads_ai_option_ini": bool(re.search(
                r"Game Options|AIOptions|AI\\\\", code)),
            "loads_ai_map_code": "MapCode" in code,
        }
    return result


def _ini_value(text: str, section: str, key: str):
    current = None
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        m = re.fullmatch(r"\[([^]]+)\]", line)
        if m:
            current = m.group(1)
            continue
        if current and current.casefold() == section.casefold() and "=" in line:
            k, v = (p.strip() for p in line.split("=", 1))
            if k.casefold() == key.casefold():
                return v
    return None


# ---------------------------------------------------------------------------
# F. 301 mapping audit of the real WDZS teacher run
# ---------------------------------------------------------------------------
def load_teacher_run() -> dict:
    run = ROOT / "_runs_g0l1" / "run-wdzs-teacher-01"
    trace_path = run / "teacher-decisions.jsonl"
    ep_path = next((run / "episodes").glob("*.jsonl"), None)
    contract_path = run / "action-contract.json"
    catalog_path = run / "public-catalog.json"
    out = {"run_dir": str(run.relative_to(ROOT)),
           "trace": {"path": str(trace_path.relative_to(ROOT)),
                     "records": [], "size": trace_path.stat().st_size,
                     "sha256": sha_file(trace_path)},
           "episode": {"path": str(ep_path.relative_to(ROOT)),
                       "size": ep_path.stat().st_size,
                       "sha256": sha_file(ep_path)},
           "contract": json.loads(contract_path.read_text(encoding="utf-8")),
           "catalog": json.loads(catalog_path.read_text(encoding="utf-8"))}
    obs = {}
    for line in ep_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("type") == "observation":
            obs[int(rec["frame"])] = rec
    out["observations"] = obs
    with trace_path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out["trace"]["records"].append(json.loads(line))
    return out


DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def dir_bucket(dx: float, dy: float):
    """Map a vector to the 8 DIRECTIONS index (screen axis order as defined in
    full_spec.DIRECTIONS; y grows down in the engine cell grid)."""
    best = None
    for i, (ux, uy) in enumerate(DIRS):
        dot = dx * ux + dy * uy
        norm = math.hypot(dx, dy) * math.hypot(ux, uy)
        cos = dot / norm if norm else -2
        if best is None or cos > best[0]:
            best = (cos, i)
    return best[1], best[0]


def nearest_obs(observations, frame):
    if frame in observations:
        return observations[frame]
    keys = sorted(observations)
    import bisect
    idx = bisect.bisect_right(keys, frame) - 1
    return observations[keys[idx]] if idx >= 0 else None


def audit_301_mapping(run) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    os.environ.setdefault("DSH_ACTION_SPACE_VERSION", "full_ground_v2p1")
    from dsh_rl.full_spec import FullControlSpec  # noqa: E402

    cat = run["catalog"]
    contract = run["contract"]
    spec = FullControlSpec(
        catalog=cat["types"], groups=cat["groups"],
        country_index=int(cat.get("country_index", 0)),
        tech_level=int(cat.get("tech_level", 10)),
        action_space_version=contract["action_space_version"],
        target_candidate_range=contract["target_candidate_range"],
        target_nearest_k=contract["target_nearest_k"])
    obs = run["observations"]

    counts = collections.Counter()
    decisions = len(run["trace"]["records"])
    intents_total = 0
    multi = collections.Counter()
    per_action = collections.defaultdict(collections.Counter)
    unmapped_examples = []
    mapped_examples = []

    # intended mapping of teacher raw intent -> 301 action or None
    for rec in run["trace"]["records"]:
        plan = rec.get("plan") or {}
        intents = plan.get("intents") or []
        multi[len(intents)] += 1
        frame = int(rec.get("before_frame") or 0)
        row = nearest_obs(obs, frame)
        if row is None:
            counts["NO_OBSERVATION"] += 1
        for it in intents:
            intents_total += 1
            action = (it.get("action") or "?").upper()
            verdict, detail = map_intent_to_301(spec, row, it)
            per_action[action][verdict] += 1
            counts[verdict] += 1
            if verdict.startswith(("EXACT", "APPROX")) and len(mapped_examples) < 20:
                mapped_examples.append({"action": action, "verdict": verdict,
                                        "detail": detail, "frame": frame})
            if verdict.startswith(("NOT", "HIDDEN", "NO_TARGET")) and len(unmapped_examples) < 20:
                unmapped_examples.append({"action": action, "verdict": verdict,
                                          "detail": detail, "frame": frame})
    # noop decisions: 301 action 0 exists and is always legal, but it is "no
    # command" rather than a teacher allocation decision.
    total_decisions_301 = decisions
    mapping = {
        "source": str((ROOT / run["run_dir"] / "teacher-decisions.jsonl").relative_to(ROOT)),
        "action_space_version": contract["action_space_version"],
        "action_n": contract["action_n"],
        "production_catalog_slots": len(contract["production_types"]),
        "unit_slots": 8, "target_slots": 8, "pending_slots": 4,
        "decisions": decisions,
        "decisions_with_zero_intents": multi[0],
        "intents": intents_total,
        "intents_per_decision": dict(multi),
        "verdict_counts": dict(counts),
        "per_action": {k: dict(v) for k, v in per_action.items()},
        "intent_mapping_rate": round(
            (counts["EXACT"] + counts["APPROX"]) / intents_total, 4) if intents_total else None,
        "mapped_examples": mapped_examples,
        "unmapped_examples": unmapped_examples,
        "noop_note": "301 action 0 (NOOP) is legal but is not a teacher allocation decision; "
                     "zero-intent teacher decisions can only be labelled NOOP, which would bias BC.",
        "catalog_sha256": contract.get("catalog_sha256"),
        "contract_sha256": sha_bytes(
            json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()),
        "spec_types": len(spec.types),
    }
    return mapping


def map_intent_to_301(spec, row, intent):
    if row is None:
        return "NO_OBSERVATION", {}
    action = (intent.get("action") or "?").upper()
    if action == "DEPLOY":
        try:
            mcv = next((o for o in row.get("own_objects", [])
                        if "Construction Vehicle" in str(o.get("type_name"))), None)
            if mcv is None:
                return "NOT_APPLICABLE", {"reason": "no MCV in observation"}
            legal = bool(spec.masks(row, [], None)[1])
            return ("EXACT" if legal else "NOT_LEGAL"), {"301": 1, "legal": legal}
        except Exception as exc:  # noqa: BLE001
            return "ERROR", {"error": f"{type(exc).__name__}: {exc}"}
    if action == "PRODUCE":
        name = intent.get("production_type")
        slot = next((i for i, t in enumerate(spec.types)
                     if str(t.get("name")) == str(name)), None)
        if slot is None:
            return "NOT_IN_CATALOG", {"type": name}
        legal = bool(spec.masks(row, [], None)[2 + slot])
        detail = {"301": 2 + slot, "type": name, "legal": legal}
        return ("EXACT" if legal else "NOT_LEGAL"), detail
    if action == "PLACE":
        addr = intent.get("building_address")
        pending = [o for o in row.get("own_objects", [])
                   if o.get("abstract") == "ABSTRACT_TYPE_BUILDING"
                   and o.get("addr") == addr
                   and (o.get("in_limbo") or not o.get("on_map"))]
        if not pending:
            return "NOT_PENDING", {"building_address": addr}
        base = next((o.get("cell") for o in row.get("own_objects", [])
                     if o.get("on_map") and not o.get("in_limbo")
                     and "Construction Yard" in str(o.get("type_name"))), None)
        if base is None:
            return "NOT_APPLICABLE", {"reason": "no construction yard"}
        coords = (intent.get("coordinates") or intent.get("anchor") or [])
        if len(coords) < 2:
            return "NO_TARGET", {"reason": "no coordinates/anchor"}
        target = (coords[0] / 256.0, coords[1] / 256.0)
        vec = (target[0] - base[0], target[1] - base[1])
        idx, cos = dir_bucket(vec[0], vec[1])
        anchor = (base[0] + 5 * DIRS[idx][0], base[1] + 5 * DIRS[idx][1])
        dist_err = math.dist(target, anchor)
        detail = {"pending": len(pending), "direction": idx,
                  "target_cell": [round(target[0], 2), round(target[1], 2)],
                  "301_anchor": list(anchor), "anchor_dist_cells": round(dist_err, 2)}
        if dist_err <= 1.5:
            return "EXACT", detail
        if dist_err <= 5:
            return "APPROX", detail
        return "APPROX_LOCATION", detail
    if action in ("MOVE", "ATTACK", "STOP"):
        addrs = tuple(sorted(int(a) for a in intent.get("object_addresses") or []))
        if not addrs:
            return "NOT_SELECTABLE", {"reason": "empty unit selection"}
        ordered = spec.units(row)
        selection = None
        for sel in range(0, 9):
            controlled = spec.select_units(row, sel)
            if tuple(sorted(int(o["addr"]) for o in controlled)) == addrs:
                selection = sel
                break
        if selection is None:
            own = {int(o["addr"]) for o in row.get("own_objects", [])
                   if o.get("on_map") and not o.get("in_limbo")
                   and int(o.get("health") or 0) > 0}
            resolvable = all(a in own for a in addrs)
            detail = {"reason": "unit set is not one 301 selection (all-or-single)",
                      "units": len(addrs),
                      "sequence_expandable_as_single_unit_actions": resolvable}
            if resolvable:
                if action == "ATTACK":
                    top8 = [int(t["addr"]) for t in spec.targets(row)]
                    detail["target_in_top8"] = int(intent.get("target_address")) in top8
                return "SEQUENCE_EXPANDABLE", detail
            return "NOT_SELECTABLE", detail
        if action == "STOP":
            return "EXACT", {"301": "UNIT_START+sel*19+0", "selection": selection}
        coords = intent.get("coordinates") or []
        if action == "MOVE":
            if len(coords) < 2:
                return "NO_TARGET", {"reason": "no coordinates"}
            center = [sum(o["cell"][i] for o in spec.select_units(row, selection)) /
                      len(spec.select_units(row, selection)) for i in (0, 1)]
            target = (coords[0] / 256.0, coords[1] / 256.0)
            vec = (target[0] - center[0], target[1] - center[1])
            idx, cos = dir_bucket(vec[0], vec[1])
            desired = (center[0] + DIRS[idx][0] * 10, center[1] + DIRS[idx][1] * 10)
            err = math.dist(target, desired)
            detail = {"selection": selection, "direction": idx,
                      "target": [round(v, 2) for v in target],
                      "301_goal": [round(v, 2) for v in desired],
                      "goal_err_cells": round(err, 2)}
            if err <= 2:
                return "EXACT", detail
            if err <= 10:
                return "APPROX", detail
            return "APPROX_WAYPOINT_ONLY", detail
        # ATTACK
        target_addr = intent.get("target_address")
        targets = spec.targets(row)
        addrs_t = [int(t["addr"]) for t in targets]
        if target_addr in addrs_t:
            return "EXACT", {"selection": selection,
                             "target_slot": addrs_t.index(target_addr),
                             "301": "UNIT_START+sel*19+9+slot"}
        visible = [o for o in row.get("visible_enemies", [])]
        target_visible = any(int(o.get("addr", -1)) == int(target_addr) for o in visible)
        if target_visible:
            return "NOT_IN_TOP8_TARGETS", {"selection": selection,
                                           "target_visible": True}
        return "HIDDEN_TARGET", {"selection": selection, "target_visible": False}
    return "UNSUPPORTED_ACTION", {"action": action}


# ---------------------------------------------------------------------------
# G. runtime event evidence
# ---------------------------------------------------------------------------
EVENT_NAMES = {0x0: "Empty", 0x1: "PowerOn", 0x2: "PowerOff", 0x3: "Ally",
               0x4: "MegaMission", 0x5: "MegaMissionF", 0x6: "Idle", 0x7: "Scatter",
               0x8: "Destruct", 0x9: "Deploy", 0xA: "Detonate", 0xB: "Place",
               0xC: "Options", 0xD: "GameSpeed", 0xE: "Produce", 0xF: "Suspend",
               0x10: "Abandon", 0x11: "Primary", 0x12: "SpecialPlace",
               0x13: "Exit", 0x14: "Animation", 0x15: "Repair", 0x16: "Sell",
               0x17: "SellCell", 0x18: "Special", 0x19: "FrameSync",
               0x1A: "Message", 0x1B: "ResponseTime", 0x1C: "FrameInfo"}


def audit_runtime_events() -> dict:
    result = {"runs": {}}
    candidates = {
        "validation3": ROOT / "_runs_g0l1" / "run-validation-03" / "episode-v3",
        "wdzs_teacher": ROOT / "_runs_g0l1" / "run-wdzs-teacher-01" / "episode-v3",
    }
    for label, base in candidates.items():
        if not base.exists():
            continue
        ev_paths = sorted(base.glob("*/raw_game_events.jsonl"))
        for ev_path in ev_paths:
            rows = [json.loads(l) for l in ev_path.read_text(encoding="utf-8").splitlines()
                    if l.strip()]
            actions_path = ev_path.parent / "actions.jsonl"
            actions = []
            if actions_path.is_file():
                actions = [json.loads(l) for l in actions_path.read_text(
                    encoding="utf-8").splitlines() if l.strip()]
            label_counts = collections.Counter()
            for a in actions:
                label_counts[(a.get("label") or a.get("action") or "?")] += 1
            result["runs"][f"{label}:{ev_path.parent.name}"] = {
                "raw_events_path": str(ev_path.relative_to(ROOT)),
                "raw_events_size": ev_path.stat().st_size,
                "raw_events_sha256": sha_file(ev_path),
                "events": len(rows),
                "lists": dict(collections.Counter(r.get("list") for r in rows)),
                "house_index": dict(collections.Counter(r.get("house_index")
                                                        for r in rows)),
                "is_executed": dict(collections.Counter(r.get("is_executed")
                                                        for r in rows)),
                "event_types": {f"0x{k:02x} {EVENT_NAMES.get(k, '?' )}": v
                                for k, v in collections.Counter(
                                    r.get("event_type") for r in rows).items()},
                "payload_present": dict(collections.Counter(
                    r.get("payload_kind") is not None for r in rows)),
                "frames": [min((r.get("frame") for r in rows), default=None),
                           max((r.get("frame") for r in rows), default=None)],
                "action_labels": dict(label_counts),
                "produce_actions": label_counts.get("produce", 0),
                "place_actions": label_counts.get("place", 0),
                "produce_events": collections.Counter(
                    r.get("event_type") for r in rows).get(0xE, 0),
                "place_events": collections.Counter(
                    r.get("event_type") for r in rows).get(0xB, 0),
            }
    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    EVID.mkdir(parents=True, exist_ok=True)
    summary = {"task_id": "TASK-20261102-023",
               "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
               "offline_only": True, "game_launches": 0}
    print("[1/7] hashes & process state")
    summary["hashes"] = audit_hashes()
    print("[2/7] mix loading chain")
    mix = audit_mix_chain()
    summary["mix_chain"] = mix
    print("[3/7] AI libraries")
    summary["ai_libraries"] = audit_ai_libraries(mix)
    print("[4/7] rules / mode overlays")
    summary["rules_modes"] = audit_rules_and_modes()
    summary["capture_rules"] = audit_capture_rules()
    print("[5/7] spawner env")
    summary["spawner_env"] = audit_spawner_env(mix)
    print("[6/7] 301 mapping of real teacher run")
    run = load_teacher_run()
    summary["teacher_run_301_mapping"] = audit_301_mapping(run)
    summary["teacher_run_files"] = {
        "trace": {k: v for k, v in run["trace"].items() if k != "records"},
        "episode": run["episode"],
        "observations": len(run["observations"]),
        "actions_total": sum(len(r.get("plan", {}).get("intents") or [])
                             for r in run["trace"]["records"]),
    }
    print("[7/7] runtime event evidence")
    summary["runtime_events"] = audit_runtime_events()
    summary["audit_seconds"] = round(time.time() - t0, 1)
    out = write_json("P0_P1_AUDIT.json", summary)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
