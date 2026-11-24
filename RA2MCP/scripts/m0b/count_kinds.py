"""M0-B 任务 1：直接聚合 M0-A 逐局 `*.result.json` 的 `kind` 字段（只读、可重复）。

口径（两层，必须分开看）：
1) `protocol` 层：`eval-<MODEL>/evaluation.json` 里 `episodes[]` 列出的那批局——即
   M0-A 正式计入汇总的样本（full-env 7 个模型各 N=10，R1_COMBAT_REF N=6）。
   这是"M0-A 报告总数"应当对应的样本。
2) `all_result_files` 层：磁盘上所有 `workers/*/episodes/*.result.json`——包含
   protocol 未计入的 interrupted 局，以及超出 N=10 配额但已跑完的局。

不根据任何报告文字推断计数；只读 result.json 的 `kind` 字段。
"""
import json
import pathlib
import time

ROOT = pathlib.Path(r"E:\RA2MCP")
M0A = ROOT / "reports" / "m0a-2026-11-01"
OUT = ROOT / "reports" / "m0b-2026-11-01" / "raw" / "kind_counts.json"

MODELS = ["R1", "R2", "R3", "203156", "183k", "OLD_REF", "BUILD_V27", "R1_COMBAT_REF"]
FULL_ENV_MODELS = ["R1", "R2", "R3", "203156", "183k", "OLD_REF", "BUILD_V27"]


def read_result(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return None


def bump(counter, key):
    counter[key] = counter.get(key, 0) + 1


def model_counts(model):
    run_dir = M0A / f"eval-{model}"
    record = {
        "model": model,
        "run_dir": str(run_dir.relative_to(ROOT)),
        "evaluation_json": None,
        "mode": None,
        "protocol_episodes": 0,
        "protocol_kinds": {},
        "all_result_files": 0,
        "all_kinds": {},
        "extra_completed_outside_protocol": {},
        "interrupted": 0,
        "missing_result_json": 0,
        "unreadable_result_json": 0,
        "episode_ids_protocol": [],
    }
    protocol_ids = []
    eval_path = run_dir / "evaluation.json"
    if eval_path.exists():
        record["evaluation_json"] = str(eval_path.relative_to(ROOT))
        try:
            data = json.loads(eval_path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            data = {}
        record["mode"] = data.get("mode")
        for ep in data.get("episodes") or []:
            protocol_ids.append(ep.get("episode_id"))
            bump(record["protocol_kinds"], ep.get("outcome") or "UNKNOWN")
        record["protocol_episodes"] = len(protocol_ids)
        record["episode_ids_protocol"] = protocol_ids

    seen = {}
    for result_path in sorted(run_dir.glob("workers/*/episodes/*.result.json")):
        data = read_result(result_path)
        if data is None:
            record["unreadable_result_json"] += 1
            continue
        episode_id = data.get("episode_id") or result_path.name.replace(".result.json", "")
        kind = data.get("kind") or "UNKNOWN"
        seen[episode_id] = kind
        bump(record["all_kinds"], kind)
        record["all_result_files"] += 1
    record["interrupted"] = int(record["all_kinds"].get("interrupted", 0))
    for episode_id, kind in seen.items():
        if episode_id not in set(protocol_ids):
            bump(record["extra_completed_outside_protocol"], kind)
    jsonl_missing = 0
    for episode_id in protocol_ids:
        jsonl = list(run_dir.glob(f"workers/*/episodes/{episode_id}.jsonl"))
        if not jsonl:
            jsonl_missing += 1
    record["missing_jsonl_for_protocol_episode"] = jsonl_missing
    return record


def totals(records, models):
    kinds = {}
    episodes = 0
    for model in models:
        record = records[model]
        episodes += record["protocol_episodes"]
        for kind, count in record["protocol_kinds"].items():
            kinds[kind] = kinds.get(kind, 0) + count
    return {"models": len(models), "episodes": episodes, "kinds": dict(sorted(kinds.items()))}


def main():
    records = {}
    for model in MODELS:
        if not (M0A / f"eval-{model}").exists():
            records[model] = {"model": model, "error": "run_dir_missing"}
            continue
        records[model] = model_counts(model)

    full_protocol = totals(records, FULL_ENV_MODELS)
    combat_protocol = totals(records, ["R1_COMBAT_REF"])
    all_protocol = totals(records, MODELS)

    all_files = {"episodes": 0, "kinds": {}}
    for model in MODELS:
        record = records[model]
        all_files["episodes"] += record.get("all_result_files", 0)
        for kind, count in (record.get("all_kinds") or {}).items():
            all_files["kinds"][kind] = all_files["kinds"].get(kind, 0) + count
    all_files["kinds"] = dict(sorted(all_files["kinds"].items()))

    full_files = {"episodes": 0, "kinds": {}}
    for model in FULL_ENV_MODELS:
        record = records[model]
        full_files["episodes"] += record.get("all_result_files", 0)
        for kind, count in (record.get("all_kinds") or {}).items():
            full_files["kinds"][kind] = full_files["kinds"].get(kind, 0) + count
    full_files["kinds"] = dict(sorted(full_files["kinds"].items()))

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": str(M0A.relative_to(ROOT)),
        "method": "scan workers/*/episodes/*.result.json and read the `kind` field; "
                  "protocol layer = episode ids listed in eval-<MODEL>/evaluation.json",
        "models": records,
        "totals": {
            "full_env_protocol": full_protocol,
            "combat_ref_protocol": combat_protocol,
            "all_models_protocol": all_protocol,
            "full_env_all_result_files": full_files,
            "all_models_all_result_files": all_files,
        },
        "kind_notes": {
            "defeat": "对局结束且判定失败（outcome_confirmed=true）",
            "timeout": "到达 300s 时限仍未分胜负（有效评测结局，单独统计）",
            "win": "胜利（本轮 0 例）",
            "crash": "进程/游戏崩溃（本轮 0 例）",
            "interrupted": "未确认结局（outcome_confirmed=false），不计入能力汇总",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["totals"], ensure_ascii=False, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
