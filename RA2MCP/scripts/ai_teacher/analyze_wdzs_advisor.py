"""Build a new immutable WDZS source audit and replay optional advice offline."""
from __future__ import annotations

import argparse
import collections
import copy
import json
import pathlib
import sys
from types import SimpleNamespace

import numpy as np

from wdzs_source import compile_library, compile_rules, parse_ini, read_mix_entry, sha
from wdzs_advisor import WdzsAdvisor

ROOT = pathlib.Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--game-dir", default=str(ROOT / "test-game-wdzs"))
    args = parser.parse_args()
    out, game = pathlib.Path(args.out).resolve(), pathlib.Path(args.game_dir).resolve()
    assert out.is_relative_to(ROOT / "reports"), "new project report directory required"
    assert not (out / "compiled_ai.json").exists(), "never overwrite an existing analysis"
    out.mkdir(parents=True, exist_ok=True)
    (out / "sources").mkdir()
    source_files = [game / "ExpandMD02.mix", game / "RulesMD.ini", game / "mpgodstoymd.ini",
                    pathlib.Path(r"E:\hj\Utilities\aimd.ini")]
    # Reuse the earlier comprehensive protection list read-only; the ledger has
    # legitimately changed after the newly authorized game and is not rechecked.
    baseline = json.loads((ROOT / "reports/g0l1-offline-fix-2026-11-02/historical_before.json").read_text(encoding="utf-8"))
    protected = {str(ROOT / rel): item["sha256"] for rel, item in baseline.items()
                 if rel != "coordination/approvals/RUN_AUTHORIZATION_LEDGER.json"}
    protected.update({str(path): sha(path.read_bytes()) for path in source_files})
    write_json(out / "protected_before.json", protected)
    scans, found = [], []
    for archive in sorted(game.glob("*.mix")):
        try:
            result = read_mix_entry(archive)
        except (ValueError, OSError) as exc:
            result = {"path": str(archive), "status": "UNKNOWN_READ_ERROR", "error": str(exc)}
        if result["status"] == "FOUND":
            found.append(result)
        scans.append({k: v for k, v in result.items() if k != "data"})
    assert len(found) == 1, "multiple/no archive candidates; do not guess load priority"
    candidate = found[0]
    raw = candidate["data"]
    (out / "sources/aimd_from_ExpandMD02.ini").write_bytes(raw)
    rules_raw = (game / "RulesMD.ini").read_bytes()
    mode_raw = (game / "mpgodstoymd.ini").read_bytes()
    (out / "sources/RulesMD.ini").write_bytes(rules_raw)
    (out / "sources/mpgodstoymd.ini").write_bytes(mode_raw)
    parsed = parse_ini(raw)
    library = compile_library(parsed)
    assert not library["issues"], library["issues"]
    rules = compile_rules(parse_ini(rules_raw))
    external = compile_library(parse_ini(source_files[-1].read_bytes()))
    mode = parse_ini(mode_raw)
    combined = copy.deepcopy(parsed)
    for section, values in mode["sections"].items():
        combined["sections"].setdefault(section, {}).update(values)
    layered = compile_library(combined)
    provenance = {"status": "VERIFIED_ARCHIVE_CONTENT_STATIC_LOAD_CANDIDATE",
                  "candidate": {k: v for k, v in candidate.items() if k != "data"},
                  "archive_sha256": sha(pathlib.Path(candidate["path"]).read_bytes()),
                  "archive_scan": scans,
                  "external_utilities_sha256": external["source_sha256"],
                  "external_matches_archive": external["source_sha256"] == library["source_sha256"],
                  "packaged_counts": library["counts"], "external_counts": external["counts"],
                  "mode_layer_reference_issues": layered["issues"],
                  "mode_general": mode["sections"].get("GENERAL", {}),
                  "runtime_load_trace": "UNKNOWN_NOT_LAUNCHED",
                  "limitations": ["Encrypted/classic archives are not resolved by this reader.",
                                  "Map overrides and actual NPatch runtime load precedence require a separate approved check."]}
    write_json(out / "SOURCE_PROVENANCE.json", provenance)
    write_json(out / "compiled_ai.json", library)
    write_json(out / "rules_reference.json", rules)
    advisor = WdzsAdvisor(library, rules)

    run = ROOT / "_runs_g0l1/run-validation-03"
    episode_id = "ppo-20261102-134041-cb15db"
    raw_episode = run / "episodes" / (episode_id + ".jsonl")
    meta = json.loads(raw_episode.open(encoding="utf-8").readline())["meta"]
    catalog = meta["policy"]["params"]["action_contract"]["production_types"]
    context_file = run / "episode-v3" / episode_id / "decision_context.jsonl"
    contexts = [json.loads(line) for line in context_file.read_text(encoding="utf-8").splitlines() if line]
    masks = np.load(run / "dataset.npz", allow_pickle=False)["masks"]
    assert len(contexts) == len(masks), "historical mask/decision mismatch"
    trace, counts, proposed = [], collections.Counter(), 0
    for number, (context, mask) in enumerate(zip(contexts, masks)):
        obs = context.get("obs_before")
        if obs is None:
            obs = context.get("before")
        assert isinstance(obs, dict) and obs.get("frame") is not None, list(context)
        before = copy.deepcopy(obs)
        host = SimpleNamespace(current=obs, full=SimpleNamespace(types=catalog), action_masks=lambda m=mask: m)
        advice = advisor.suggest_from_env(host)
        assert before == obs, "advisory mutated input"
        action = advice["candidate_301"]
        assert action is None or 0 <= action < 301 and bool(mask[action]), "illegal advice candidate"
        advice["replay_index"] = number
        advice["input_kind"] = "REAL_HISTORICAL_OBSERVATION_WITH_RECORDED_MASK"
        trace.append(advice)
        counts[advice["goal"]] += 1
        proposed += action is not None
    (out / "advice_replay.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in trace), encoding="utf-8")
    # A hypothetical state isolates the team-guidance branch; explicitly synthetic.
    baseline_obs = copy.deepcopy(contexts[0].get("obs_before") or contexts[0].get("before"))
    baseline_obs["own_objects"] = []
    for ident in ("GACNST", "GAPOWR", "GAREFN", "GAPILE", "GAWEAP"):
        baseline_obs["own_objects"].append({"addr": len(baseline_obs["own_objects"])+1,
            "type_name": rules["units"][ident]["name"], "health": 1000, "on_map": True})
    synthetic_mask = [False] * 301
    for slot in range(len(catalog)):
        synthetic_mask[2+slot] = True
    example = advisor.suggest(baseline_obs, catalog, synthetic_mask)
    example["input_kind"] = "SYNTHETIC_ECONOMY_COMPLETE_NOT_REAL_GAME"
    write_json(out / "team_guidance_example.json", example)
    combat = advisor._combat_template(catalog)
    mismatches = [path for path, expected in protected.items() if sha(pathlib.Path(path).read_bytes()) != expected]
    replay = {"status": "VERIFIED_OFFLINE_HOST_INTERFACE_ONLY", "observations": len(trace),
              "goals": dict(counts), "mask_legal_candidates": proposed, "illegal_candidates": 0,
              "mutations": 0, "games_started": 0, "bridge_connections": 0,
              "network_input_changed": False, "training_started": False,
              "protected_files_checked": len(protected), "protected_mismatches": mismatches,
              "team_example_available": combat is not None,
              "live_connection": "UNKNOWN_NOT_EXECUTED"}
    assert not mismatches
    write_json(out / "OFFLINE_INTEGRATION.json", replay)
    write_json(out / "attribute_examples.json", [advisor.property_card(ident)
                for ident in ("E1", "GGI", "MTNK", "CMIN", "GAREFN", "GAPILE", "GAWEAP")])
    print(json.dumps({"counts": library["counts"], "external_same": provenance["external_matches_archive"],
                      "mode_issues": len(layered["issues"]), "replay": replay,
                      "team_example": example.get("team_template", {}).get("name") if example.get("team_template") else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
