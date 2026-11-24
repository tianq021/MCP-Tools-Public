"""生成 M0-A / M6 S0 的修改/新增文件清单与 SHA256 清单（只读扫描）。"""
import hashlib
import json
import pathlib
import time

ROOT = pathlib.Path(r"E:\RA2MCP")
S0 = pathlib.Path(r"H:\RA2MCP_S0_VERA20K")
OUT = ROOT / "reports" / "m0a-2026-11-01" / "CHANGES_AND_HASHES.md"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def entry(p, note=""):
    p = pathlib.Path(p)
    if not p.exists():
        return {"path": str(p), "exists": False, "note": note}
    return {"path": str(p), "exists": True, "size": p.stat().st_size,
            "sha256": sha(p), "note": note}


def main():
    modified = [
        (ROOT / "scripts/dsh_learning/trace_parser.py",
         "指标时间映射修复（唯一修改的既有源文件；备份见 backups/m0a-pre-2026-11-01）"),
    ]
    new_project = [
        ROOT / "scripts/eval_m0a/__init__.py",
        ROOT / "scripts/eval_m0a/protocol.py",
        ROOT / "scripts/eval_m0a/analyze.py",
        ROOT / "scripts/eval_m0a/test_analyze.py",
        ROOT / "scripts/eval_m0a/run_eval.py",
        ROOT / "scripts/eval_m0a/reanalyze.py",
        ROOT / "scripts/eval_m0a/recompute_history.py",
        ROOT / "scripts/eval_m0a/run_all_models.py",
        ROOT / "scripts/eval_m0a/summarize_batch.py",
        ROOT / "reports/m0a-2026-11-01/M0A_REPORT.md",
        ROOT / "reports/m0a-2026-11-01/batch_summary.json",
        ROOT / "reports/m0a-2026-11-01/history_recompute.json",
        ROOT / "reports/m0a-2026-11-01/summary_tables.md",
        ROOT / "reports/m0a-2026-11-01/HISTORICAL_MODEL_REEVALUATION.md",
        ROOT / "reports/m0a-2026-11-01/CHANGES_AND_HASHES.md",
        ROOT / "reports/vera20k-s0-2026-11-01/VERA20K_S0_REPORT.md",
        ROOT / "reports/vera20k-s0-2026-11-01/RULE_CONSISTENCY.md",
        ROOT / "backups/m0a-pre-2026-11-01/SHA256_MANIFEST.json",
    ]
    for extra in ("R1", "R2", "R3", "203156", "183k", "OLD_REF", "BUILD_V27", "R1_COMBAT_REF"):
        ev = ROOT / f"reports/m0a-2026-11-01/eval-{extra}/evaluation.json"
        if ev.exists():
            new_project.append(ev)
    new_s0 = [
        S0 / "build_sim_bench.meta.json",
        S0 / "build_sim_bench.log",
        S0 / "s0_metrics.json",
        S0 / "run_s0_tests.ps1",
        S0 / "run_t4_multi.ps1",
        S0 / "run_clean_baseline.ps1",
        S0 / "parse_s0_results.py",
        S0 / "cargo_fetch.log",
        S0 / "vera20k/target/release/sim-bench.exe",
        S0 / "s0_runs/t1_skirmish_9000.log",
        S0 / "s0_runs/t6_skirmish_30000.log",
        S0 / "s0_runs/t7_skirmish_9000_repeat.log",
        S0 / "s0_runs/t2_match_1800.log",
        S0 / "s0_runs/t3_skirmish_client_timing.log",
        S0 / "s0_runs/t1_skirmish_9000.hashes.jsonl",
        S0 / "s0_runs/t7_skirmish_9000_repeat.hashes.jsonl",
        S0 / "run_clean_baseline.ps1",
        S0 / "s0_runs/t8_clean_9000_a.log",
        S0 / "s0_runs/t8_clean_9000_b.log",
        S0 / "s0_runs/t9_clean_client_timing.log",
        S0 / "s0_runs/t4_2instances.meta.json",
        S0 / "s0_runs/t4_4instances.meta.json",
        S0 / "s0_runs/t4_8instances.meta.json",
        S0 / "s0_runs/t4_8inst_0.log",
        S0 / "clean_console.log",
        S0 / "t4_console.log",
    ]
    protected = [
        ROOT / "reports/rl/stage-A-round1-20261030-153225/model.zip",
        ROOT / "reports/rl/stage-A-round2-20261030-155824/model.zip",
        ROOT / "reports/rl/stage-A-round3-20261030-162536/model.zip",
        ROOT / "reports/rl/ppo-shared-20261030-102633/model.zip",
        ROOT / "reports/rl/ppo-shared-20261030-203156/model.zip",
        ROOT / "reports/rl/experimentA-auto-20261031-081809/model.zip",
        ROOT / "reports/rl/p0-4-smoke-port14621-20261031-131720/model.zip",
        ROOT / "experiments/build_training_v2/build_training_v2_model/model.zip",
        ROOT / "config/rewards.json",
        ROOT / "config/rewards-combat.json",
        ROOT / "config/spawn.ini",
        ROOT / "coordination/PROJECT_STATE.json",
        ROOT / "coordination/EVENTS.jsonl",
    ]
    data = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "modified_existing": [entry(p, n) for p, n in modified],
            "new_project_files": [entry(p) for p in new_project],
            "new_s0_files": [entry(p) for p in new_s0],
            "protected_assets": [entry(p) for p in protected]}
    lines = ["# M0-A / M6 S0 修改与新增文件清单（自动生成）", "",
             f"- 生成时间：{data['generated_at']}",
             "- 说明：SHA256 小写；修改的既有源文件只有 trace_parser.py（其余均为新增）；"
             "原版 `E:\\hj` 未修改，RA2MCP 核心桥接/PPO/奖励未修改。", ""]
    for key, title in (("modified_existing", "一、修改的既有文件（项目内）"),
                       ("new_project_files", "二、新增文件（E:\\RA2MCP 内）"),
                       ("new_s0_files", "三、新增文件（H:\\RA2MCP_S0_VERA20K，S0 独立目录）"),
                       ("protected_assets", "四、受保护资产哈希复核（本次未改动）")):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| 路径 | 大小 | SHA256 | 备注 |")
        lines.append("|---|---|---|---|")
        for e in data[key]:
            if not e.get("exists"):
                lines.append(f"| `{e['path']}` | MISSING | - | {e.get('note','')} |")
                continue
            lines.append(f"| `{e['path']}` | {e['size']} | `{e['sha256']}` | {e.get('note','')} |")
        lines.append("")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "reports/m0a-2026-11-01/changes_hashes.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()


