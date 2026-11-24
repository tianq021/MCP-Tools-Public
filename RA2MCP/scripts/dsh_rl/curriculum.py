"""Curriculum stage configs for RA2MCP V4 (Phase 3 design artefact).

Stages (see docs/curriculum-v4.md):

  A  combat & kills      - easy AI, reduced AI absolute credits, big starting squad
  B  building attack     - easy AI that still builds a base
  C  full battle         - normal AI, normal player economy (full-control env)

The script only swaps the game-directory ``spawn.ini`` (with a timestamped
backup) and never touches checkpoints, episode data or reward configs.

Important facts verified against the real game (2026-10-30):
* the active ``gamemd-spawn-ra2yrcpp.exe`` uses the legacy CnCNet 5 spawner
  which only parses the ``[Settings]`` section; ``[CreditsFactor]`` is ignored
  and the AI always starts with ``Credits x 5``;
* ``[HouseHandicaps]`` IS parsed, with CnCNet client semantics
  2 = Easy / 1 = Medium / 0 = Hard.

Usage:
    python scripts/dsh_rl/curriculum.py --status
    python scripts/dsh_rl/curriculum.py --stage A            # dry run: show diff
    python scripts/dsh_rl/curriculum.py --stage A --apply    # write spawn.ini
"""
import argparse
import configparser
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CURRICULUM = ROOT / 'config' / 'curriculum'
PRIMARY = ROOT / 'test-game'
STAGES = {
    'A': CURRICULUM / 'stage-A-combat.spawn.ini',
    'B': CURRICULUM / 'stage-B-building.spawn.ini',
    'C': CURRICULUM / 'stage-C-full.spawn.ini',
}
# Verified from vendor sources (YRpp GeneralDefinitions.h / spawner UIName switch):
# AIDifficulty 0 = Hard, 1 = Normal, 2 = Easy.
DIFFICULTY = {0: 'Hard', 1: 'Normal', 2: 'Easy'}
# CnCNet client semantics for [HouseHandicaps] (PlayerInfo.cs):
# 2 = Easy, 1 = Medium, 0 = Hard.
HANDICAP = {2: 'Easy', 1: 'Medium', 0: 'Hard'}
SETTINGS_KEYS = ('PlayerCount', 'AIPlayers', 'AIDifficulty', 'Credits', 'UnitCount',
                 'Bases', 'ShortGame', 'Superweapons', 'SkipScoreScreen')
# AI starting credits multiplier of the legacy spawner, measured on the real game
# (Credits=100000 -> AI 500000; Credits=20000 -> AI 100000).
LEGACY_AI_CREDIT_MULTIPLIER = 5


def _read_ini(path):
    ini = configparser.ConfigParser(strict=False, interpolation=None,
                                    inline_comment_prefixes=(';',))
    ini.read_string(pathlib.Path(path).read_text(encoding='utf-8-sig'))
    return ini


def describe(path):
    """Return the settings that actually matter, including derived expectations."""
    path = pathlib.Path(path)
    if not path.exists():
        return {'missing': True}
    ini = _read_ini(path)
    out = {}
    if ini.has_section('Settings'):
        for key in SETTINGS_KEYS:
            if ini.has_option('Settings', key):
                out[key] = ini.get('Settings', key)
    difficulty = out.get('AIDifficulty')
    if difficulty is not None:
        try:
            out['AIDifficulty_meaning'] = DIFFICULTY[int(difficulty)]
        except ValueError:
            out['AIDifficulty_meaning'] = '?'
    credits = out.get('Credits')
    if credits is not None:
        try:
            human = int(credits)
            out['expected_human_start_credits'] = human
            out['expected_ai_start_credits'] = human * LEGACY_AI_CREDIT_MULTIPLIER
        except ValueError:
            pass
    for slot in ('Multi1', 'Multi2', 'Multi3'):
        if ini.has_section('HouseHandicaps') and ini.has_option('HouseHandicaps', slot):
            value = ini.get('HouseHandicaps', slot)
            out[f'HouseHandicaps_{slot}'] = value
            try:
                out[f'HouseHandicaps_{slot}_meaning'] = HANDICAP.get(int(value), '?')
            except ValueError:
                out[f'HouseHandicaps_{slot}_meaning'] = '?'
    if ini.has_section('CreditsFactor'):
        factors = {k: ini.get('CreditsFactor', k) for k in ini.options('CreditsFactor')}
        out['CreditsFactor_ignored_by_spawner'] = factors
    return out


def games_running():
    try:
        import subprocess
        out = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq gamemd-spawn-ra2yrcpp'],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return False
    return 'gamemd-spawn-ra2yrcpp' in out


def apply_stage(stage, apply):
    source = STAGES[stage]
    if not source.exists():
        raise SystemExit(f'missing curriculum config: {source}')
    primary_spawn = PRIMARY / 'spawn.ini'
    config_spawn = ROOT / 'config' / 'spawn.ini'
    current = describe(primary_spawn)
    target = describe(source)
    print(f'current primary spawn.ini : {current}')
    print(f'stage {stage} target config  : {target}')
    if not apply:
        print('\ndry run only; pass --apply to write the game-dir spawn.ini')
        return 0
    if games_running():
        raise SystemExit('a game is running; stop training/games before applying a stage')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup_dir = ROOT / 'backups' / 'spawn'
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in (primary_spawn, config_spawn):
        if path.exists():
            # Include the parent directory name: test-game/spawn.ini and
            # config/spawn.ini have the same basename and used to overwrite
            # each other's backup (fixed 2026-10-30).
            backup = backup_dir / f'{path.parent.name}-{path.stem}-{stamp}{path.suffix}'
            shutil.copy2(path, backup)
            print('backup:', backup)
    shutil.copy2(source, primary_spawn)
    shutil.copy2(source, config_spawn)
    # Training copies for instances > 0 are re-derived from the primary copy by
    # train_shared.prepare() at the next start, so they need no explicit write.
    print(f'applied stage {stage} -> {primary_spawn}')
    print('next training start will copy it to the replica game dirs')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage', choices=sorted(STAGES))
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--status', action='store_true')
    args = ap.parse_args(argv)
    if args.status or not args.stage:
        print('primary spawn.ini:', describe(PRIMARY / 'spawn.ini'))
        for name, path in STAGES.items():
            print(f'stage {name}:', describe(path) if path.exists() else 'missing')
        return 0
    return apply_stage(args.stage, args.apply)


if __name__ == '__main__':
    sys.exit(main())
