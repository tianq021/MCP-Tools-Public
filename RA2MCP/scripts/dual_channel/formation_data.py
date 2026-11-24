"""Raw multi-command demonstration records; no automatic PPO or BC admission."""
from __future__ import annotations

import collections
import json
import pathlib

from dual_channel.data import sha, write_json, sanitized, resolve_terminal

SCHEMA = "ra2.raw-formation-transition/1"


def validate(record):
    errors = []
    if record.get("schema") != SCHEMA or record.get("source") != "formation_teacher":
        errors.append("source_schema")
    if not record.get("decision_id") or not record.get("teacher_sha256"):
        errors.append("lineage")
    if record["observation"]["frame"] != record["observation_frame"] or record["next_frame"] < record["observation_frame"]:
        errors.append("time_alignment")
    if "hidden_enemy_count" in record["observation"]:
        errors.append("hidden_input")
    commands = record["plan"]["commands"]
    used = set()
    own = {o["addr"]: o for o in record["observation"].get("own_objects", [])}
    enemies = {o["addr"] for o in record["observation"].get("visible_enemies", [])}
    for c in commands:
        intent = c["intent"]
        addresses = intent.get("object_addresses") or []
        if intent["action"] in ("MOVE", "ATTACK", "STOP"):
            for address in addresses:
                if address not in own or address in used:
                    errors.append("actor_conflict_or_not_own")
                elif any(n in own[address]["type_name"] for n in ("Miner", "Harvester", "Construction Vehicle", "Engineer", "Spy")):
                    errors.append("economy_actor_in_combat")
                used.add(address)
        if intent["action"] == "ATTACK" and intent["target_address"] not in enemies:
            errors.append("invisible_target")
        if c["mapping_301"]["classification"] != "EXACT" and c["mapping_301"]["action_idx"] is not None:
            errors.append("fabricated_301_label")
        if c["mapping_301"]["classification"] == "EXACT":
            index = c["mapping_301"]["action_idx"]
            if not isinstance(index, int) or not (0 <= index < 301):
                errors.append("invalid_exact_301_index")
    if len(commands) != 1 and record.get("decision_301_eligible"):
        errors.append("multi_command_as_single_301")
    if len(commands) == 1 and record.get("decision_301_eligible") and commands[0]["mapping_301"]["classification"] != "EXACT":
        errors.append("unsupported_command_as_single_301")
    if record.get("actor_eligible") or record.get("effect_label_eligible") or record.get("formal_training_eligible"):
        errors.append("unapproved_learning_admission")
    return errors


class FormationWriter:
    def __init__(self, out, metadata):
        self.out = pathlib.Path(out)
        self.out.mkdir(exist_ok=False)
        self.metadata = metadata
        self.handle = (self.out / "transitions.jsonl").open("x", encoding="utf-8")
        write_json(self.out / "metadata.json", metadata)
        self.n = 0
        self.command_count = 0
        self.max_army = 0
        self.mapping = collections.Counter()
        self.skills = collections.Counter()
        self.actual_actors = set()
        self.actual_outside_slots = set()
        self.coverage_rows = 0
        self.coverage_complete = 0
        self.errors = []
        self.outcomes = collections.Counter()

    def append(self, row):
        errors = validate(row)
        if errors:
            raise ValueError(errors)
        self.handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.handle.flush()
        self.n += 1
        self.max_army = max(self.max_army, row["plan"]["combat_units"])
        self.command_count += len(row["plan"]["commands"])
        if row["plan"]["assignments"]:
            self.coverage_rows += 1
            self.coverage_complete += row["plan"]["assigned_units"] == row["plan"]["combat_units"]
        for c in row["plan"]["commands"]:
            self.mapping[c["mapping_301"]["classification"]] += 1
            self.skills[c["skill_id"]] += 1
        outcomes = {o["action_id"]: o["classification"] for o in row.get("command_outcomes", [])}
        self.outcomes.update(outcomes.values())
        for cmd in row["native_commands"]:
            accepted = outcomes.get(cmd.get("action_id")) == "ACCEPTED"
            if cmd.get("command_type") == "UNIT_ORDER" and cmd.get("transport_result", {}).get("ok") and accepted:
                ids = cmd.get("actor_unit_ids") or []
                self.actual_actors.update(ids)
                self.actual_outside_slots.update(set(ids) & set(row["plan"]["outside_legacy_slots"]))

    def finish(self, obs, kind, flags=None, failure=None):
        self.handle.close()
        terminal = resolve_terminal(obs, kind)
        # Outcome flags are audit-only after the run, never supplied to teacher.
        flags = flags or {}
        if kind == "win" and (flags.get("human_is_winner") or flags.get("enemies_all_defeated")):
            terminal.update(kind="win", source="engine_terminal_flags", terminated=True)
        elif kind == "defeat" and (flags.get("human_defeated") or flags.get("human_is_loser")):
            terminal.update(kind="defeat", source="engine_terminal_flags", terminated=True)
        terminal["engine_flags_audit_only"] = flags
        summary = {"schema": "ra2.formation-summary/1", "teacher": self.metadata,
                   "capture_complete": failure is None and kind in ("win", "defeat", "timeout"),
                   "failure": failure, "terminal": terminal, "steps": self.n,
                   "planned_commands": self.command_count, "mapping_301": dict(self.mapping),
                   "native_command_outcomes": dict(self.outcomes),
                   "skill_counts": dict(self.skills), "max_observed_combat_units": self.max_army,
                   "formation_assignment_rows": self.coverage_rows,
                   "complete_assignment_rows": self.coverage_complete,
                   "distinct_sent_unit_actors": len(self.actual_actors),
                   "distinct_actors_sent_while_outside_8slots": sorted(self.actual_outside_slots),
                   "formal_training_eligible": False, "ppo_eligible": False,
                   "teacher_strength": "UNKNOWN", "promotion_status": "CANDIDATE",
                   "limitations": ["Raw formation demonstrations are not single 301 actions",
                                   "Transport send/ACK is not APPLIED or causal effect evidence",
                                   "Single run is not an improvement or win-rate evaluation"]}
        write_json(self.out / "summary.json", summary)
        write_json(self.out / "manifest.json", {"schema": "ra2.formation-manifest/1",
            "files": {p.name: {"sha256": sha(p), "size": p.stat().st_size}
                      for p in self.out.iterdir() if p.is_file()}})
        return summary


def verify(out):
    out = pathlib.Path(out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    bad = [name for name, v in manifest["files"].items()
           if not (out / name).is_file() or sha(out / name) != v["sha256"] or (out / name).stat().st_size != v["size"]]
    errors = []
    seen = set()
    with (out / "transitions.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            errors += validate(r)
            if r["decision_id"] in seen:
                errors.append("duplicate_decision_id")
            seen.add(r["decision_id"])
    return {"pass": not bad and not errors, "hash_mismatches": bad, "validation_errors": errors, "decisions": len(seen)}
