"""Read-only WDZS AI source inspection. No game, Bridge or training imports.

MIX header layout was checked against OpenRA's MixFile reader:
https://github.com/OpenRA/OpenRA/blob/bleed/OpenRA.Mods.Cnc/FileSystem/MixFile.cs
Only unencrypted archives are supported. AIMD.INI is exactly eight ASCII bytes,
so its RA2 CRC32 name hash needs no partial-word filename padding.
"""
from __future__ import annotations

import collections
import hashlib
import pathlib
import re
import struct
import zlib


def sha(data):
    return hashlib.sha256(data).hexdigest()


def decode_ini(data):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("INI encoding unknown; refusing silent replacement")


def parse_ini(data):
    text, encoding = decode_ini(data)
    sections, lines, current = {}, {}, None
    duplicates = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"\[([^]]+)\]", line)
        if match:
            current = match[1].upper()
            sections.setdefault(current, {})
            continue
        if current is not None and "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            key = key.casefold()
            if key in sections[current]:
                duplicates.append({"section": current, "key": key, "line": number})
            sections[current][key] = value
            lines[(current, key)] = number
    return {"sections": sections, "lines": lines, "encoding": encoding,
            "duplicates": duplicates, "sha256": sha(data)}


def read_mix_entry(path, name="aimd.ini"):
    """Return named entry bytes and source offsets; never changes the archive."""
    if len(name.encode("ascii")) % 4:
        raise ValueError("Only whole-word filenames supported; do not guess padding")
    target = zlib.crc32(name.upper().encode("ascii")) & 0xffffffff
    path = pathlib.Path(path)
    total = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(10)
        if len(header) < 10:
            raise ValueError("MIX header truncated")
        modern = struct.unpack_from("<H", header)[0] == 0
        if not modern:
            return {"status": "CLASSIC_HASH_UNSUPPORTED", "path": str(path)}
        flags = struct.unpack_from("<I", header)[0] if modern else 0
        if flags & 0x20000:
            return {"status": "ENCRYPTED_UNSUPPORTED", "path": str(path), "flags": flags}
        offset = 4 if modern else 0
        count, size = struct.unpack_from("<HI", header, offset)
        start = offset + 6 + 12 * count
        if start > total or start + size > total:
            raise ValueError("MIX bounds invalid")
        stream.seek(offset + 6)
        entries = [struct.unpack("<III", stream.read(12)) for _ in range(count)]
        for key, pos, length in entries:
            if pos + length > size or start + pos + length > total:
                raise ValueError("MIX member outside declared payload")
            if key == target:
                stream.seek(start + pos)
                data = stream.read(length)
                return {"status": "FOUND", "path": str(path), "entry": name,
                        "entry_hash": f"{key:08x}", "offset": start + pos,
                        "size": length, "sha256": sha(data), "data": data}
    return {"status": "NOT_FOUND", "path": str(path), "entries": count, "flags": flags}


def registered(sections, name):
    return list(dict.fromkeys(value.upper() for value in sections.get(name.upper(), {}).values()))


def numbered(section):
    return [(int(k), v) for k, v in sorted(section.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 10**9)
            if k.isdigit()]


def resolve(value, registry):
    if value.upper() in registry:
        return value.upper()
    if value.isdigit() and int(value) < len(registry):
        return registry[int(value)]
    return None


# Labels are advisory, not an implementation of the NPatch engine's interpreter.
MISSIONS = {0: "ATTACK_TARGET_CLASS", 5: "TIMED_GUARD", 6: "JUMP_SCRIPT_LINE",
            8: "UNLOAD", 9: "DEPLOY", 11: "SET_MISSION", 14: "LOAD_TRANSPORT",
            21: "SCATTER", 43: "WAIT_TRANSPORT", 46: "ATTACK_BUILDING_TYPE",
            47: "MOVE_TO_ENEMY_BUILDING_TYPE", 49: "COMPLETE_SUCCESS",
            53: "GATHER", 54: "REGROUP", 55: "IRON_CURTAIN",
            57: "CHRONOSHIFT", 58: "MOVE_TO_FRIENDLY_BUILDING_TYPE",
            61: "ENTER_TANK_BUNKER", 62: "ENTER_BIOREACTOR", 63: "ENTER_BATTLE_BUNKER"}
MAPPABLE = {0, 5, 9, 46, 47, 53, 54, 58}
FLOW = {6, 49}


def compile_library(parsed):
    sections = parsed["sections"]
    indices = {name: registered(sections, name) for name in ("TaskForces", "ScriptTypes", "TeamTypes")}
    forces, scripts, teams, issues = {}, {}, {}, []
    for ident in indices["TaskForces"]:
        section = sections.get(ident, {})
        units = []
        for number, value in numbered(section):
            parts = value.split(",")
            if len(parts) != 2 or not parts[0].isdigit():
                issues.append({"id": ident, "kind": "BAD_UNIT_COMPOSITION", "raw": value})
                continue
            units.append({"count": int(parts[0]), "type_id": parts[1].upper()})
        forces[ident] = {"id": ident, "name": section.get("name"), "units": units}
    for ident in indices["ScriptTypes"]:
        section = sections.get(ident, {})
        actions = []
        for number, value in numbered(section):
            parts = value.split(",", 1)
            try:
                code = int(parts[0]); parameter = int(parts[1])
            except (ValueError, IndexError):
                code = parameter = None
            actions.append({"step": number, "code": code, "parameter": parameter, "raw": value,
                            "label": MISSIONS.get(code, "UNKNOWN"),
                            "coverage": "ADVISORY_ONLY" if code in MAPPABLE else "CONTROL_FLOW_ONLY" if code in FLOW else "UNSUPPORTED",
                            "source_line": parsed["lines"].get((ident, str(number)))})
        scripts[ident] = {"id": ident, "name": section.get("name"), "actions": actions}
    for ident in indices["TeamTypes"]:
        section = sections.get(ident, {})
        force = resolve(section.get("taskforce", ""), indices["TaskForces"])
        script = resolve(section.get("script", ""), indices["ScriptTypes"])
        if not force or not script:
            issues.append({"id": ident, "kind": "BROKEN_TEAM_REFERENCE", "taskforce": section.get("taskforce"), "script": section.get("script")})
        teams[ident] = {"id": ident, "name": section.get("name"), "taskforce_id": force,
                        "script_id": script, "fields": section}
    triggers = []
    for ident, raw in sections.get("AITRIGGERTYPES", {}).items():
        fields = [p.strip() for p in raw.split(",")]
        primary = resolve(fields[1], indices["TeamTypes"]) if len(fields) > 1 else None
        secondary_raw = fields[14] if len(fields) > 14 else "<none>"
        secondary = resolve(secondary_raw, indices["TeamTypes"])
        if not primary:
            issues.append({"id": ident, "kind": "BROKEN_PRIMARY_TRIGGER_TEAM", "raw": raw})
        if secondary_raw.casefold() not in ("<none>", "none", "") and not secondary:
            issues.append({"id": ident, "kind": "BROKEN_SECONDARY_TRIGGER_TEAM", "raw": raw})
        triggers.append({"id": ident.upper(), "name": fields[0], "primary_team": primary,
                         "secondary_team": secondary, "condition_type": fields[4] if len(fields) > 4 else None,
                         "condition_object": fields[5] if len(fields) > 5 else None,
                         "raw": raw, "native_trigger_execution": "UNKNOWN_NOT_IMPLEMENTED"})
    actions = [a for s in scripts.values() for a in s["actions"]]
    return {"schema": "wdzs.advisory-library/1", "source_sha256": parsed["sha256"],
            "encoding": parsed["encoding"], "taskforces": forces, "scripts": scripts,
            "teams": teams, "triggers": triggers, "issues": issues,
            "counts": {"taskforces": len(forces), "scripts": len(scripts), "teams": len(teams),
                       "triggers": len(triggers), "action_lines": len(actions)},
            "action_codes": dict(collections.Counter(a["code"] for a in actions)),
            "coverage": dict(collections.Counter(a["coverage"] for a in actions)),
            "limitations": ["Advice is derived from definitions, not observed native commands.",
                            "Labels are not proof of NPatch mission semantics; unsupported actions remain explicit.",
                            "Native trigger conditions/weights and engine micro are not reimplemented."]}


def compile_rules(parsed):
    sections = parsed["sections"]
    kinds = {"INFANTRYTYPES": "infantry", "VEHICLETYPES": "vehicle",
             "AIRCRAFTTYPES": "aircraft", "BUILDINGTYPES": "building"}
    units = {}
    for registry, kind in kinds.items():
        for ident in registered(sections, registry):
            fields = sections.get(ident, {})
            units[ident] = {"id": ident, "kind": kind, "name": fields.get("name"),
                            "fields": fields, "rules_source_sha256": parsed["sha256"]}
    weapons = {weapon.upper(): sections.get(weapon.upper(), {}) for unit in units.values()
               for key in ("primary", "secondary", "eliteprimary", "elitesecondary")
               if (weapon := unit["fields"].get(key))}
    return {"schema": "wdzs.rules-reference/1", "source_sha256": parsed["sha256"],
            "units": units, "ai_build_roles": sections.get("AI", {}),
            "weapons": weapons,
            "warheads": {v["warhead"].upper(): sections.get(v["warhead"].upper(), {})
                         for v in weapons.values() if v.get("warhead")},
            "projectiles": {v["projectile"].upper(): sections.get(v["projectile"].upper(), {})
                            for v in weapons.values() if v.get("projectile")},
            "vocabulary": {ident: index+1 for index, ident in enumerate(sorted(units))},
            "unknown_type_id": 0,
            "status": "STATIC_RULES_NOT_RUNTIME_EFFECTIVE_STATS"}
