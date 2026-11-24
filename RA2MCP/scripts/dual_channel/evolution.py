"""Channel B: evidence -> bounded parameter-only candidate, never auto-promotion."""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from ai_teacher.strategic_teacher import StrategyConfig, digest
from dual_channel.data import verify, sha, write_json


def propose(run, out):
    run, out = pathlib.Path(run), pathlib.Path(out)
    if out.exists():
        raise FileExistsError("candidate outputs are immutable")
    verification = verify(run / "dual-channel")
    if not verification["pass"]:
        raise ValueError("source evidence failed integrity")
    summary_path = run / "dual-channel/summary.json"
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    parent = s["teacher"]["config"]
    cfg = dict(parent)
    observations = []
    trace_path = run / "dual-channel/transitions.jsonl"
    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    bound = {int(b["identity"][0]) for b in last["slot_bindings"]}
    combat = [o for o in last["observation"].get("own_objects", [])
              if o.get("on_map") and not o.get("in_limbo") and o.get("health", 0) > 0
              and o.get("abstract") in ("ABSTRACT_TYPE_UNIT", "ABSTRACT_TYPE_INFANTRY")
              and not any(n in o.get("type_name", "") for n in ("Miner", "Harvester", "Construction Vehicle", "Engineer", "Spy", "Dog"))]
    outside_slots = sum(o["addr"] not in bound for o in combat)
    steps = max(1, s["steps"])
    moves = sum(v for k, v in s["skill_counts"].items() if k in ("combat.advance", "combat.rally"))
    if moves / steps > .35:
        cfg["move_cooldown_seconds"] = min(20, cfg["move_cooldown_seconds"] + 2)
        observations.append("high movement share; test longer command persistence")
    if s["terminal"]["kind"] == "defeat":
        cfg["attack_after_seconds"] = min(180, cfg["attack_after_seconds"] + 15)
        observations.append("observed defeat; test delayed commitment, cause unproven")
    if not all(s["tech_economy_observed"][k] for k in ("Refinery", "Barracks", "War Factory")):
        cfg["max_economy_gap_ticks"] = max(1, cfg["max_economy_gap_ticks"] - 1)
        observations.append("incomplete core economy; test tighter economy service interval")
    if outside_slots:
        observations.append(f"{outside_slots} current combat units outside the eight endpoint slots; parameter tuning alone cannot repair control coverage")
    validated = StrategyConfig(**cfg)
    candidate = {"schema": "ra2.strategy-candidate/1", "status": "CANDIDATE_NOT_PROMOTED",
                 "proposal_source": "deterministic_diagnostic_rules; hypotheses are unproven, not learned strategic intelligence",
                 "parent_config_sha256": digest(parent), "config": validated.as_dict(),
                 "config_sha256": digest(validated.as_dict()), "source_summary_sha256": sha(summary_path),
                 "hypotheses": observations or ["no supported corrective hypothesis; retain parent control"],
                 "control_coverage": {"observed_combat_units": len(combat), "outside_current_slots": outside_slots},
                 "requires_contract_review": bool(outside_slots),
                 "allowed_changes": [f.name for f in dataclasses.fields(StrategyConfig)],
                 "promotion": {"approved": False, "requires": "independent paired evaluation and applicable authorization"},
                 "model_weights_changed": False, "generated_python_executed": False}
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, candidate)
    return candidate


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    print(json.dumps(propose(a.run, a.out), ensure_ascii=True))
