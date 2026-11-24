"""Append-only V3 sidecar writer, separate label and effect admission, no training."""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib

SCHEMA = "ra2.dual-channel.transition/1"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def write_json(path, obj):
    pathlib.Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sanitized(obs):
    from ai_teacher.strategic_teacher import StrategicTeacher
    return StrategicTeacher.visible(obs)


def resolve_terminal(obs, kind):
    houses = obs.get("houses") or []
    hero = next((h for h in houses if h.get("current_player") and h.get("is_human")), None)
    if hero and hero.get("is_winner"):
        observed = "win"
    elif hero and (hero.get("is_loser") or hero.get("defeated")):
        observed = "defeat"
    else:
        observed = "UNKNOWN"
    conflict = observed != "UNKNOWN" and kind in ("win", "defeat") and observed != kind
    return {"kind": observed, "environment_kind": kind,
            "source": "own_house_flags" if observed != "UNKNOWN" else "UNKNOWN",
            "evidence": hero, "frame": obs.get("frame"), "conflict": conflict,
            "truncated": kind == "timeout", "terminated": observed in ("win", "defeat") and not conflict}


def validate_transition(row):
    if not isinstance(row, dict):
        return ["record_not_object"]
    errors = []
    if row.get("schema") != SCHEMA:
        errors.append("schema")
    action, mask = row.get("action_idx"), row.get("action_mask", [])
    if not isinstance(action, int) or not (0 <= action < 301) or len(mask) != 301 or not mask[action]:
        errors.append("action_mask")
    if row.get("source") != "collector_teacher" or not row.get("teacher_sha256"):
        errors.append("source")
    if row.get("observation_frame") != row.get("observation", {}).get("frame"):
        errors.append("observation_frame")
    if row.get("next_frame", -1) < row.get("observation_frame", 0):
        errors.append("time_reversal")
    if "hidden_enemy_count" in row.get("observation", {}):
        errors.append("hidden_information")
    if action and not row.get("intent"):
        errors.append("unmapped_intent")
    if not row.get("decision_id") or not row.get("skill_instance_id"):
        errors.append("lineage")
    return errors


class SidecarWriter:
    def __init__(self, out, episode_id, metadata):
        self.out = pathlib.Path(out)
        self.out.mkdir(exist_ok=False)
        self.episode_id = episode_id
        self.metadata = metadata
        self.trace = (self.out / "transitions.jsonl").open("x", encoding="utf-8")
        self.skill_trace = (self.out / "skills.jsonl").open("x", encoding="utf-8")
        self.closed = False
        self.records = []
        self.buildings_seen = set()
        write_json(self.out / "metadata.json", metadata)

    def append(self, row):
        errors = validate_transition(row)
        if errors:
            raise ValueError(errors)
        self.trace.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.trace.flush()
        self.buildings_seen.update(own_names(row["observation"], buildings=True))
        self.buildings_seen.update(own_names(row["next_observation"], buildings=True))
        # Store bounded summary fields, not duplicate all observations in RAM.
        self.records.append({k: row.get(k) for k in ("action_idx", "skill_id", "phase", "source", "feedback",
                              "admission", "observation_frame", "next_frame")})

    def skills(self, events):
        for event in events:
            self.skill_trace.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.skill_trace.flush()

    def finish(self, observation, kind, failure=None):
        if self.closed:
            return json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.trace.close()
        self.skill_trace.close()
        self.closed = True
        terminal = resolve_terminal(observation, kind)
        skills = collections.Counter(r["skill_id"] for r in self.records)
        labels = [r for r in self.records if r["admission"]["bc_label_eligible"]]
        buildings = own_names(observation, buildings=True)
        summary = {"schema": "ra2.dual-channel.summary/1", "episode_id": self.episode_id,
                   "teacher": self.metadata, "steps": len(self.records), "terminal": terminal,
                   "failure": failure, "complete": failure is None and kind in ("win", "defeat", "timeout"),
                   "skill_counts": dict(skills), "bc_candidate_labels": len(labels),
                   "ppo_actor_eligible": False, "effect_supervision_eligible": False,
                   "end_buildings": buildings,
                   "observed_building_types": sorted(self.buildings_seen),
                   "tech_economy_observed": {w: any(w in n for n in self.buildings_seen) for w in
                                             ("Refinery", "Barracks", "War Factory", "Battle Lab")},
                   "quality_status": "PENDING_REVIEW", "training_started": False,
                   "note": "BC candidates are teacher decisions, not DIRECT effects or proof of strength"}
        if failure or terminal["conflict"]:
            summary["quality_status"] = "REJECTED_ENVIRONMENT"
        elif not all(summary["tech_economy_observed"][k] for k in ("Refinery", "Barracks", "War Factory")):
            summary["quality_status"] = "LOCAL_SKILLS_ONLY"
        write_json(self.out / "summary.json", summary)
        files = {p.name: {"size": p.stat().st_size, "sha256": sha(p)} for p in sorted(self.out.iterdir()) if p.is_file()}
        write_json(self.out / "manifest.json", {"schema": "ra2.dual-channel.manifest/1", "files": files})
        return summary


def own_names(obs, buildings=False):
    return sorted(o.get("type_name", "") for o in obs.get("own_objects", [])
                  if o.get("on_map") and not o.get("in_limbo") and o.get("health", 0) > 0
                  and (not buildings or o.get("abstract") == "ABSTRACT_TYPE_BUILDING"))


def verify(out):
    out = pathlib.Path(out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    mismatches = [name for name, v in manifest["files"].items()
                  if not (out / name).is_file() or (out / name).stat().st_size != v["size"] or sha(out / name) != v["sha256"]]
    errors = []
    ids = set()
    for line in (out / "transitions.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append("malformed_json")
            continue
        row_errors = validate_transition(row)
        errors += row_errors
        if row_errors:
            continue
        if row["decision_id"] in ids:
            errors.append("duplicate_decision_id")
        ids.add(row["decision_id"])
    return {"pass": not mismatches and not errors, "hash_mismatches": mismatches,
            "validation_errors": errors, "decisions": len(ids)}
