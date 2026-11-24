"""重新分析已有 eval 目录（分析器更新后无需重跑对局）。

用法：
  python scripts/eval_m0a/reanalyze.py --eval-dir reports/m0a-2026-11-01/eval-R1
对 eval-dir 下 workers/*/episodes/*.result.json 逐局重跑 analyze，重写 summary。
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "dsh_train"))

from eval_m0a import analyze as analyze_mod  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-dir", required=True)
    a = ap.parse_args()
    out = pathlib.Path(a.eval_dir)
    if not out.is_absolute():
        out = (ROOT / out).resolve()
    eval_path = out / "evaluation.json"
    if not eval_path.exists():
        raise SystemExit(f"no evaluation.json in {out}")
    evaluation = json.loads(eval_path.read_text(encoding="utf-8"))
    episodes = []
    # 只重分析 evaluation.json 中登记的有效评测局（避免把 auto-reset 的额外对局算进去）
    selected = evaluation.get("episodes") or []
    for entry in selected:
        jsonl = pathlib.Path(entry.get("jsonl", ""))
        result_path = pathlib.Path(entry.get("result", ""))
        if not jsonl.exists() or not result_path.exists():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        episodes.append(analyze_mod.analyze_episode(jsonl, result))
    if not episodes:  # 退化：旧格式没有逐局路径时回退扫描
        for result_path in sorted((out / "workers").glob("*/episodes/*.result.json"),
                                  key=lambda p: p.stat().st_mtime):
            jsonl = result_path.with_name(result_path.name.replace(".result.json", ".jsonl"))
            if not jsonl.exists():
                continue
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
            episodes.append(analyze_mod.analyze_episode(jsonl, result))
    # 复用 run_eval.aggregate（延迟导入，避免运行期 argparse）
    import importlib
    run_eval = importlib.import_module("eval_m0a.run_eval")
    evaluation["episodes"] = episodes
    evaluation["summary"] = run_eval.aggregate(episodes, evaluation.get("summary", {}).get("game_slots"))
    evaluation["reanalyzed_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
    evaluation["analyzer_version"] = "m0a-analyze-v2"
    eval_path.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "summary.json").write_text(
        json.dumps(evaluation["summary"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evaluation["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
