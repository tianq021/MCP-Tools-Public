"""Generate config/rewards-combat.json from the full-game reward config (V4).

Kept for backwards compatibility with earlier workflows; the canonical V4
generator is ``scripts/dsh_rl/make_v4_rewards.py``.  Both produce the same
combat-stage contract.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
src = ROOT / "config/rewards.json"
dst = ROOT / "config/rewards-combat.json"

data = json.loads(src.read_text(encoding="utf-8-sig"))
rules = data["rules"]


def setr(key, enabled=None, score=None, step_cap="keep", episode_cap="keep",
         max_events="keep", unit="keep"):
    if enabled is not None:
        rules[key]["enabled"] = enabled
    if score is not None:
        rules[key]["score"] = score
    if step_cap != "keep":
        rules[key]["step_cap"] = step_cap
    if episode_cap != "keep":
        rules[key]["episode_cap"] = episode_cap
    if max_events != "keep":
        rules[key]["max_events"] = max_events
    if unit != "keep":
        rules[key]["unit"] = unit


# Stage A: combat is the signal; economy is scripted, exploration disabled.
# V4: damage is worth less, finishing kills/buildings is worth much more.
setr("opponent_hp", score=2.0, unit=100.0)
setr("killed_hp_credit", score=2.0, unit=100.0)
setr("enemy_kill_infantry", score=6.0, unit=1.0)
setr("enemy_kill_vehicle", score=12.0, unit=1.0)
setr("enemy_building_destroy", score=40.0, unit=1.0)
setr("first_kill", score=5.0, unit=1.0, max_events=1)
setr("first_building_destroy", score=10.0, unit=1.0, max_events=1)
setr("own_hp", score=-0.6, unit=100.0, episode_cap=20.0)
setr("victory", score=500.0, unit=1.0, max_events=1)
setr("defeat", score=-300.0, unit=1.0, max_events=1)
setr("timeout", score=-50.0, unit=1.0, max_events=1, enabled=False)  # experiment item
setr("time", score=-0.002, unit=1.0)
setr("advance", score=0.1, unit=1.0, step_cap=0.3, episode_cap=20.0)
setr("first_advance", score=5.0, unit=1.0, max_events=1)
setr("first_enemy_sight", score=2.0, unit=1.0, max_events=1)
setr("first_damage", score=3.0, unit=1.0, max_events=1)
setr("attack", score=0.1, unit=1.0, step_cap=0.1, episode_cap=1.0)
for key in ("progress", "exploration", "construction_yard", "first_building_type",
            "first_unit_type", "unit_produced", "income"):
    setr(key, enabled=False)
for key, rule in rules.items():
    if key not in {"opponent_hp", "killed_hp_credit", "enemy_kill_infantry",
                   "enemy_kill_vehicle", "enemy_building_destroy", "first_kill",
                   "first_building_destroy", "own_hp", "invalid", "victory",
                   "defeat", "time", "advance", "first_advance",
                   "first_enemy_sight", "attack", "first_damage"}:
        rule["enabled"] = False

dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", dst)
print("enabled:", [k for k, v in rules.items() if v["enabled"]])
