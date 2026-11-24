"""Build and install an observer-mode skirmish config in an isolated game copy.

Direction (confirmed by user 2026-11-03):
  * observer seat (IsSpectator=Yes) + AI players only;
  * 8-player map, 1 observer + 7 AI = 8 houses (spawner has host + Other1..Other7);
  * GameSpeed=1 (engine fastest, ~62 fps);
  * all AI at Hard (AIDifficulty=0, HouseHandicaps=0);
  * standard economy: Credits=10000, UnitCount=0 (MCV only), Superweapons off.

The module never launches a game.  It only writes spawn.ini / spawnmap.ini into an
already-isolated copy (directory name MUST start with ``test-game``), backing up the
previous files first (append-only, no silent overwrite of history).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import time

# CnCNet spawner colour palette (0 Gold, 1 Red, 2 Blue, 3 Green, 4 Orange, 5 Cyan, 6 Purple, 7 Pink)
DEFAULT_COLORS = (0, 1, 3, 4, 5, 6, 7)
# YR country ids: 0 America, 1 Korea, 2 France, 3 Germany, 4 Great Britain,
# 5 Libya, 6 Iraq, 7 Cuba, 8 Russia, 9 Yuri.
DEFAULT_COUNTRIES = (0, 1, 2, 3, 4, 8, 9)
DEFAULT_MAP = pathlib.Path(
    r"E:\RA2MCP\test-game\Maps\Yuri's Revenge\Battle\8_valley_of_the_exiles.map"
)
# Land-only backups for the 8-player FFA direction (visual + runtime check required):
#   8_cliffside_slide.map, 8_invasion.map, 8_oilvalley.map
# Rejected: 8_mayflower_ffa.map (island/water map; P1 smoke 2026-11-03: zero combat),
#           8_andalusia.map / 8_octo_bay.map / 8_malaboo_bay.map (water/canal maps).

MAX_HOUSES = 8  # spawner registers host (Settings) + Other1..Other7


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def build_observer_spawn_ini(
    seed: int,
    ai_count: int = 7,
    difficulty: int = 0,
    credits: int = 10000,
    unit_count: int = 0,
    game_speed: int = 1,
    superweapons: bool = False,
    colors=DEFAULT_COLORS,
    countries=DEFAULT_COUNTRIES,
    ui_map_name: str = "8_mayflower_ffa",
    name: str = "DSH-Observer",
) -> str:
    """Return spawn.ini text for 1 observer + ``ai_count`` AI on ``1+ai_count`` houses."""
    _require(1 <= ai_count <= MAX_HOUSES - 1, f"ai_count must be 1..{MAX_HOUSES - 1}")
    _require(len(colors) >= ai_count, "not enough colours")
    _require(len(countries) >= ai_count, "not enough countries")
    _require(difficulty in (0, 1, 2), "difficulty: 0=Hard 1=Normal 2=Easy")
    _require(game_speed >= 1, "game_speed=1 is the fastest; larger numbers are slower")
    total = 1 + ai_count
    _require(total <= MAX_HOUSES, f"total houses {total} > {MAX_HOUSES}")

    lines = [
        "; DSH observer data collection config (generated, do not edit by hand)",
        "; 1 observer (host, IsSpectator=Yes) + %d AI = %d houses on an 8-player map" % (ai_count, total),
        "; GameSpeed=1 is the engine maximum (~62 fps); larger values are slower.",
        "[Settings]",
        f"Name={name}",
        "Side=0",
        "Color=2",
        "IsSpectator=Yes",
        "Scenario=spawnmap.ini",
        f"UIMapName={ui_map_name}",
        f"PlayerCount={total}",
        f"AIPlayers={ai_count}",
        f"AIDifficulty={difficulty}",
        "GameMode=1",
        "Bases=Yes",
        f"Credits={credits}",
        f"UnitCount={unit_count}",
        f"GameSpeed={game_speed}",
        "TechLevel=10",
        "ShortGame=Yes",
        "Superweapons=" + ("Yes" if superweapons else "No"),
        "BuildOffAlly=Yes",
        "Crates=No",
        "BridgeDestroy=Yes",
        "MultiEngineer=No",
        "AlliesAllowed=No",
        "HarvesterTruce=No",
        "FogOfWar=No",
        "MCVRedeploy=Yes",
        f"Seed={int(seed)}",
        "Protocol=0",
        "FrameSendRate=2",
        "ReconnectTimeout=1400",
        "SidebarHack=Yes",
        "SpecialHouseIsAlly=No",
        "DefeatedBecomesObserver=Yes",
        "AINamesByDifficulty=Yes",
        "Observer.ShowAIOnSidebar=Yes",
        "SkipScoreScreen=Yes",
        "RunAutoSS=No",
        "AutoSaveCount=0",
        "AutoSaveInterval=0",
        "DisableChat=Yes",
        "",
        "; AI house k (k=1..%d) is Multi(k+1); host/observer is Multi1." % ai_count,
        "[HouseColors]",
        "Multi1=2",
    ]
    for i in range(ai_count):
        lines.append(f"Multi{i + 2}={colors[i]}")
    lines += ["", "[HouseCountries]", "Multi1=0"]
    for i in range(ai_count):
        lines.append(f"Multi{i + 2}={countries[i]}")
    lines += ["", "[HouseHandicaps]", "Multi1=0"]
    for i in range(ai_count):
        lines.append(f"Multi{i + 2}={difficulty}")
    lines.append("")
    return "\n".join(lines)


def validate_inventory(ai_count: int, colors, countries) -> None:
    """Extra validation shared with callers that supply custom palette lists."""
    _require(1 <= ai_count <= MAX_HOUSES - 1, "ai_count out of range")
    _require(len(set(colors[:ai_count])) == ai_count, "colours must be unique")
    _require(len(set(countries[:ai_count])) == ai_count, "countries must be unique")


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_isolated_copy(game_dir: pathlib.Path) -> None:
    game_dir = game_dir.resolve()
    _require(game_dir.name.lower().startswith("test-game"),
             f"refusing to write config outside an isolated test-game* copy: {game_dir}")
    _require("hj" not in game_dir.name.lower(), "refusing to touch E:\\hj")
    _require((game_dir / "gamemd-spawn-ra2yrcpp.exe").is_file(),
             f"not a spawner copy (executable missing): {game_dir}")


def install_observer_config(
    game_dir: pathlib.Path,
    map_source: pathlib.Path = DEFAULT_MAP,
    spawn_ini_text: str | None = None,
    backup_root: pathlib.Path | None = None,
    **spawn_kwargs,
) -> dict:
    """Write spawn.ini + spawnmap.ini into an isolated copy, backing up old files.

    Returns a fingerprint dict suitable for a match/run record.
    """
    game_dir = pathlib.Path(game_dir)
    map_source = pathlib.Path(map_source)
    _assert_isolated_copy(game_dir)
    _require(map_source.is_file(), f"map not found: {map_source}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = pathlib.Path(backup_root or (game_dir / "observer-backup")) / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ("spawn.ini", "spawnmap.ini"):
        src = game_dir / name
        if src.is_file():
            shutil.copy2(src, backup_dir / name)

    text = spawn_ini_text if spawn_ini_text is not None else build_observer_spawn_ini(**spawn_kwargs)
    (game_dir / "spawn.ini").write_text(text, encoding="utf-8")
    shutil.copyfile(map_source, game_dir / "spawnmap.ini")

    fp = {
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "game_dir": str(game_dir),
        "spawn_ini_sha256": sha256_file(game_dir / "spawn.ini"),
        "spawnmap_sha256": sha256_file(game_dir / "spawnmap.ini"),
        "map_source": str(map_source),
        "map_source_sha256": sha256_file(map_source),
        "exe_sha256": sha256_file(game_dir / "gamemd-spawn-ra2yrcpp.exe"),
        "bridge_dll_sha256": sha256_file(game_dir / "libra2yrcpp.dll"),
        "backup_dir": str(backup_dir),
        "spawn_ini_text": text,
    }
    (game_dir / "observer-config.fingerprint.json").write_text(
        json.dumps(fp, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


if __name__ == "__main__":  # tiny manual preview (offline)
    print(build_observer_spawn_ini(seed=20261103))
