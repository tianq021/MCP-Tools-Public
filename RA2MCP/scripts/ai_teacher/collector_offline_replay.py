"""Offline replay of CollectorTeacher against real, already-captured observations.

No game / Bridge / training process is started. The historical observation rows
come from an existing episode file; the action contract + catalog come from the
existing validation run. For every decision we verify:

1. the emitted action is inside the real legal mask;
2. the action decodes to a ``full_spec`` intent (or is action 0 = NOOP);
3. no hidden-information field is read;
4. a second fresh teacher on the same inputs emits the identical action sequence.

Usage (control venv, no game):
  .\\.venv\\Scripts\\python.exe scripts\\ai_teacher\\collector_offline_replay.py \
      --out reports/teacher-collector-skeleton-2026-11-02/OFFLINE_REPLAY.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "ai_teacher"))

import numpy as np  # noqa: E402

from dsh_rl.full_spec import FullControlSpec  # noqa: E402
from ai_teacher.collector_teacher import (  # noqa: E402
    CollectorConfig, CollectorTeacher, POLICY_VERSION)

DEFAULT_RUN = ROOT / "_runs_g0l1" / "run-validation-03"
DEFAULT_EPISODE = (ROOT / "_runs_g0l1" / "run-wdzs-teacher-01" / "episodes"
                   / "ppo-20261102-154125-363ce7.jsonl")


def sha_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ReplayHost:
    """Host shim with the same read-only surface as the real HostAdapter."""

    def __init__(self, row, spec, starts=(), map_soa=None):
        self.current = row
        self.starts = list(starts)
        self.full = spec
        self.map_soa = map_soa
        self.catalog = []

    def available_production(self):
        out = [bool(self.full.can_produce(i, self.current))
               for i in range(len(self.full.types))]
        return out + [False] * (96 - len(out))

    def production_types(self):
        return [str(t.get("name")) for t in self.full.types]

    def legal_actions(self):
        mask = self.full.masks(self.current, self.starts, self.map_soa)
        return np.flatnonzero(mask).tolist()


def load_spec(run_dir: pathlib.Path) -> tuple[FullControlSpec, dict, dict]:
    contract = json.loads((run_dir / "action-contract.json").read_text(encoding="utf-8"))
    catalog = json.loads((run_dir / "public-catalog.json").read_text(encoding="utf-8"))
    spec = FullControlSpec(
        catalog=catalog["types"], groups=catalog["groups"],
        country_index=int(catalog.get("country_index", 0)),
        tech_level=int(catalog.get("tech_level", 10)),
        action_space_version=contract["action_space_version"],
        target_candidate_range=contract["target_candidate_range"],
        target_nearest_k=contract["target_nearest_k"])
    name_to_ptr = {str(t.get("name")): int(t["pointer_self"])
                   for t in catalog["types"] if t.get("name")}
    return spec, contract, name_to_ptr


def remap_type_pointers(obs: dict, name_to_ptr: dict) -> dict:
    """Cross-session offline replay only.

    The catalog and the historical observations come from different game
    launches, so raw ``type_ptr`` values differ even for the same type. Rebind
    them by type name so the frozen contract's pointer-keyed lookups
    (prerequisites/array_index/factory-family) work. A real collection run does
    not need this: catalog and observation come from the same session.
    """
    out = dict(obs)
    for key in ("own_objects", "visible_enemies", "neutral_objects"):
        items = []
        for obj in (obs.get(key) or []):
            obj = dict(obj)
            name = str(obj.get("type_name") or "")
            if name in name_to_ptr:
                obj["type_ptr"] = name_to_ptr[name]
            items.append(obj)
        if key in obs:
            out[key] = items
    return out


def load_observations(episode: pathlib.Path) -> list[dict]:
    out = []
    with episode.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("type") == "observation":
                out.append(rec)
    return out


def row_of(obs: dict) -> dict:
    """Keep only the local, legal observation fields the teacher may read."""
    keys = ("own_objects", "visible_enemies", "factories", "houses",
            "money", "t", "frame", "map")
    return {k: obs.get(k) for k in keys if k in obs}


def map_soa_of(obs: dict):
    info = obs.get("map") or {}
    return types.SimpleNamespace(map_width=int(info.get("width") or 182),
                                 map_height=int(info.get("height") or 182))


def run_once(spec, observations, config) -> dict:
    teacher = CollectorTeacher(config=config, spec=spec, seed=0)
    actions, records = [], []
    violations = []
    for index, obs in enumerate(observations):
        row = row_of(obs)
        host = ReplayHost(row, spec, starts=(), map_soa=map_soa_of(obs))
        legal = host.legal_actions()
        try:
            dec = teacher.decide(host)
        except Exception as exc:  # noqa: BLE001
            violations.append({"index": index, "kind": "EXCEPTION",
                               "error": f"{type(exc).__name__}: {exc}"})
            actions.append(-1)
            continue
        if dec["action"] not in legal:
            violations.append({"index": index, "kind": "MASKED_ACTION",
                               "action": dec["action"], "legal_n": len(legal)})
        if dec["action"] != 0 and dec["intent"] is None:
            violations.append({"index": index, "kind": "UNMAPPED_ACTION",
                               "action": dec["action"]})
        if dec["hidden_info_used"]:
            violations.append({"index": index, "kind": "HIDDEN_INFO_USED"})
        actions.append(dec["action"])
        records.append({
            "index": index, "frame": obs.get("frame"), "t": dec["t"],
            "task": dec["task"], "label": dec["label"], "action": dec["action"],
            "mapping": dec["mapping"], "phase": dec["phase"],
            "own_buildings": dec["own_buildings"], "combat_units": dec["combat_units"],
            "visible_enemies": dec["visible_enemies"],
            "intent": None if dec["intent"] is None else {
                "action": dec["intent"].get("action"),
                "production_type": dec["intent"].get("production_type"),
                "target_type": dec["intent"].get("target_type"),
                "coordinates": dec["intent"].get("coordinates"),
            },
            "digest": dec["digest"],
        })
    return {"actions": actions, "records": records,
            "violations": violations, "teacher": teacher}


def summarize(result: dict) -> dict:
    records = result["records"]
    labels, tasks, phases = {}, {}, {}
    for rec in records:
        labels[rec["label"]] = labels.get(rec["label"], 0) + 1
        tasks[rec["task"]] = tasks.get(rec["task"], 0) + 1
        phases[rec["phase"]] = phases.get(rec["phase"], 0) + 1
    mapped = sum(1 for rec in records if rec["mapping"] == "MAPPED")
    noop = sum(1 for rec in records if rec["mapping"] == "NOOP")
    return {"decisions": len(records), "labels": labels, "tasks": tasks,
            "phases": phases, "mapped": mapped, "noop": noop,
            "mapping_rate": round((mapped + noop) / max(1, len(records)), 6),
            "action_hash": hashlib.sha256(
                json.dumps(result["actions"]).encode()).hexdigest(),
            "violations": len(result["violations"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", default=str(DEFAULT_RUN))
    ap.add_argument("--episode", default=str(DEFAULT_EPISODE))
    ap.add_argument("--limit", type=int, default=0, help="0 = all observations")
    ap.add_argument("--out", default=str(ROOT / "reports/teacher-collector-skeleton-2026-11-02/OFFLINE_REPLAY.json"))
    ap.add_argument("--save-records", action="store_true")
    a = ap.parse_args()

    run_dir = pathlib.Path(a.run_dir)
    episode = pathlib.Path(a.episode)
    spec, contract, name_to_ptr = load_spec(run_dir)
    observations = [remap_type_pointers(o, name_to_ptr)
                    for o in load_observations(episode)]
    if a.limit:
        observations = observations[:a.limit]
    config = CollectorConfig()

    started = time.time()
    first = run_once(spec, observations, config)
    second = run_once(spec, observations, config)
    summary_first = summarize(first)
    summary_second = summarize(second)
    deterministic = first["actions"] == second["actions"]

    out = {
        "schema": "collector-teacher/offline-replay/1",
        "policy_version": POLICY_VERSION,
        "policy_metadata": CollectorTeacher(config=config, spec=spec).policy_metadata(),
        "offline_only": True,
        "game_launches": 0,
        "inputs": {
            "run_dir": str(run_dir.relative_to(ROOT)),
            "action_contract": str((run_dir / "action-contract.json").relative_to(ROOT)),
            "action_contract_sha256": sha_file(run_dir / "action-contract.json"),
            "catalog": str((run_dir / "public-catalog.json").relative_to(ROOT)),
            "catalog_sha256": sha_file(run_dir / "public-catalog.json"),
            "episode": str(episode.relative_to(ROOT)),
            "episode_sha256": sha_file(episode),
            "action_space_version": contract["action_space_version"],
            "action_n": contract["action_n"],
            "observations": len(observations),
        },
        "first_pass": summary_first,
        "second_pass": summary_second,
        "deterministic": deterministic,
        "duration_s": round(time.time() - started, 2),
        "sample_records": first["records"][:20],
        "violations": first["violations"][:50],
    }
    if a.save_records:
        out["records"] = first["records"]
    out_path = pathlib.Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out_path), "decisions": summary_first["decisions"],
                      "labels": summary_first["labels"],
                      "mapping_rate": summary_first["mapping_rate"],
                      "violations": summary_first["violations"],
                      "deterministic": deterministic}, ensure_ascii=False))
    return 0 if (summary_first["violations"] == 0 and deterministic) else 2


if __name__ == "__main__":
    sys.exit(main())
