"""Static map-file metadata extraction (offline, no game).

The RA2/YR ``.map``/``.mpr`` file is a text INI.  This module keeps the **raw file**
(the complete source of truth) and builds a compact machine-readable index of it:

  * file size / sha256;
  * every section name + key count + byte span (so nothing is silently dropped);
  * selected small sections verbatim ([Header], [Map], [Basic], [SpecialFlags],
    [Lighting], [Digest]) — these carry the declared size/start/waypoint metadata;
  * [Header]/[Waypoints] waypoint values;
  * counts for the big gameplay sections (IsoMapPack5/OverlayPack/Structures/Terrain/
    Triggers/Actions/Events/Tags/AITriggerTypesEnable/Houses/Units/Infantry/...).

The raw map file is copied unchanged into the run directory, so "采集所有地图数据"
means: raw file + this index + the runtime map SoA captured after the game loads it.
"""

from __future__ import annotations

import hashlib
import pathlib
import re

SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$", re.M)
KEY_RE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*=\s*(.*)$", re.M)
VERBATIM_SECTIONS = ("Header", "Map", "Basic", "SpecialFlags", "Lighting", "Digest",
                     "Theater", "General")
COUNT_SECTIONS = ("IsoMapPack5", "OverlayPack", "OverlayDataPack", "PreviewPack",
                  "Structures", "Terrain", "Triggers", "Actions", "Events", "Tags",
                  "AITriggerTypesEnable", "Houses", "Units", "Infantry", "Waypoints",
                  "TeamTypes", "TaskForces", "ScriptTypes", "Special", "Neutral",
                  "CIV1", "Americans", "Russians", "YuriCountry", "Multimaps")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1", errors="replace")


def parse_sections(text: str) -> list[tuple[str, int, int]]:
    matches = list(SECTION_RE.finditer(text))
    out = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out.append((match.group(1).strip(), start, end))
    return out


def static_map_meta(path: pathlib.Path) -> dict:
    path = pathlib.Path(path)
    raw = path.read_bytes()
    text = _decode(raw)
    sections = parse_sections(text)
    section_index = []
    verbatim: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for name, start, end in sections:
        body = text[start:end]
        keys = KEY_RE.findall(body)
        section_index.append({"name": name, "key_count": len(keys), "bytes": end - start})
        if name in COUNT_SECTIONS:
            counts[name] = len(keys)
        if name in VERBATIM_SECTIONS:
            verbatim[name] = {key: value.strip() for key, value in keys}

    # header-declared size / starting points / waypoints
    header = verbatim.get("Header", {})
    map_section = verbatim.get("Map", {})
    basic = verbatim.get("Basic", {})
    waypoints = {}
    for key, value in header.items():
        if key.startswith("Waypoint"):
            waypoints[key] = value
    for key, value in verbatim.get("Waypoints", {}).items():
        waypoints.setdefault(f"W{key}", value)

    result = {
        "schema": "observer.static_map_meta.v1",
        "path": str(path),
        "file_name": path.name,
        "file_size_bytes": len(raw),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "encoding_used": "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "latin-1/utf-8",
        "section_count": len(sections),
        "sections": section_index,
        "verbatim_sections": verbatim,
        "section_key_counts": counts,
        "declared": {
            "header_width": header.get("Width"),
            "header_height": header.get("Height"),
            "header_start_x": header.get("StartX"),
            "header_start_y": header.get("StartY"),
            "number_starting_points": header.get("NumberStartingPoints"),
            "map_size": map_section.get("Size"),
            "map_theater": map_section.get("Theater"),
            "map_local_size": map_section.get("LocalSize"),
            "basic_name": basic.get("Name"),
            "basic_author": basic.get("Author"),
            "basic_max_player": basic.get("MaxPlayer"),
            "basic_min_player": basic.get("MinPlayer"),
            "basic_game_mode": basic.get("GameMode"),
            "basic_multiplayer_only": basic.get("MultiplayerOnly"),
        },
        "waypoints": waypoints,
        "waypoint_count": len(waypoints),
        "note": "declared Header Width/Height & [Map] Size are NOT the runtime cell grid; "
                "the runtime grid comes from map_data_soa.map_width/map_height (collected at "
                "INGAME) and must be used for connectivity/path checks.",
    }
    return result


def copy_raw_map(source: pathlib.Path, run_dir: pathlib.Path) -> dict:
    """Copy the raw map file into the run directory, returning size/sha."""
    source = pathlib.Path(source)
    run_dir = pathlib.Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / f"map_source{source.suffix or '.map'}"
    target.write_bytes(source.read_bytes())
    return {"path": str(target), "size": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


if __name__ == "__main__":  # manual dump
    import json
    import sys

    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(
        r"E:\RA2MCP\test-game\Maps\Yuri's Revenge\Battle\8_valley_of_the_exiles.map")
    meta = static_map_meta(target)
    print(json.dumps({k: meta[k] for k in ("file_name", "file_size_bytes", "section_count",
                                          "declared", "waypoint_count")},
                     ensure_ascii=False, indent=2))
