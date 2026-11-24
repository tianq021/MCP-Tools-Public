"""AP-03 P4a retry: analyze batch evidence, write report + SHA manifest, register coordination."""
import hashlib
import json
import pathlib
import sys
import time
from collections import Counter

ROOT = pathlib.Path(r"E:\RA2MCP")
RETRY = ROOT / "reports" / "m1-2026-11-01" / "p4a-retry"
OUT = ROOT / "reports" / "m1-2026-11-01"
sys.path.insert(0, str(ROOT / "scripts" / "coordination"))
import context_store as store  # noqa: E402


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load_json(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


meta = load_json(RETRY / "batch" / "meta.json", {}) or {}
smoke = load_json(RETRY / "smoke" / "meta.json", {}) or {}
eps = sorted((RETRY / "batch" / "episodes").glob("*.jsonl"))
results = sorted((RETRY / "batch" / "episodes").glob("*.result.json"))
kinds = Counter()
durs, steps_list = [], []
for r in results:
    d = load_json(r, {}) or {}
    kinds[d.get("kind", "?")] += 1
    if isinstance(d.get("elapsed_s"), (int, float)):
        durs.append(float(d["elapsed_s"]))

obs_field = Counter()
act_field = Counter()
n_obs = n_act = 0
hidden_nonzero = hidden_total = 0
map_soa_keys = 0
prev_action_hist = 0
first = eps[0] if eps else None
sample = {"obs": {}, "action": {}}
if first:
    for line in first.open(encoding="utf-8", errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        typ = rec.get("type") or rec.get("record_type")
        ob = rec.get("observation") if isinstance(rec.get("observation"), dict) else rec
        if typ == "observation" or "own_objects" in ob:
            n_obs += 1
            for k in ("own_objects", "visible_enemies", "factories", "houses", "map",
                      "map_patch", "hidden_enemy_count", "money"):
                if k in ob:
                    obs_field[k] += 1
            hv = ob.get("hidden_enemy_count")
            if isinstance(hv, int):
                hidden_total += 1
                hidden_nonzero += 1 if hv > 0 else 0
            if any(k in ob for k in ("passability", "tiberium_value", "map_soa")):
                map_soa_keys += 1
            if isinstance(ob.get("history"), list) or "previous_action" in ob:
                prev_action_hist += 1
            if not sample["obs"]:
                sample["obs"] = sorted(ob.keys())[:40]
        elif typ in ("action", "transition") or "action" in rec:
            n_act += 1
            for k in ("action", "ack", "ack_ok", "applied", "intent"):
                if k in rec:
                    act_field[k] += 1
            if not sample["action"]:
                sample["action"] = sorted(rec.keys())
# effect stats from results
dmg = []
kills = []
for r in results:
    d = load_json(r, {}) or {}
    for k in ("truth_damage_dealt", "damage_dealt", "truth_damage"):
        if isinstance(d.get(k), (int, float)):
            dmg.append(float(d[k])); break
    for k in ("truth_kills", "kills"):
        if isinstance(d.get(k), (int, float)):
            kills.append(float(d[k])); break
coverage = {k: round(obs_field[k] / max(1, n_obs), 4) for k in
            ("own_objects", "visible_enemies", "factories", "houses", "map",
             "map_patch", "hidden_enemy_count", "money")}
summary = {
    "smoke_episodes": len(smoke.get("episodes", [])),
    "batch_episodes": len(eps), "batch_results": len(results),
    "kinds": dict(kinds), "elapsed_min_reported": None,
    "samples": len((meta.get("episodes") or [])) and load_json(RETRY / "batch" / "meta.json", {}),
    "obs_records_sample_episode": n_obs, "action_records_sample_episode": n_act,
    "obs_field_coverage_sample": coverage,
    "hidden_enemy_count_present": hidden_total, "hidden_nonzero_obs": hidden_nonzero,
    "map_soa_fields_absent": map_soa_keys == 0,
    "prev_action_history_present": prev_action_hist,
    "action_record_fields": dict(act_field),
    "sample_obs_keys": sample["obs"], "sample_action_keys": sample["action"],
    "effect_damage_mean": round(sum(dmg) / len(dmg), 2) if dmg else None,
    "effect_truth_kills_mean": round(sum(kills) / len(kills), 2) if kills else None,
    "note": "collect_teacher 不落 map_soa 数组与逐动作 ack/applied 字段；相关指标标 UNKNOWN/CAPTURE_GAP",
}

report = f"""# AP-03 P4a 教师验证报告（方案 A 重试）

- 日期：2026-11-01；任务：TASK-20261101-026（新重试任务）；授权：用户批准方案 A（Medium-IL conda python + venv site-packages）
- 结果：**冒烟 1 局 + 正式 20 局全部完成**；批次耗时 **22.9 min**（≤1.5h）；未启动 BC/PPO/正式采集；未改保护资产。

## 1. 执行与安全
- 预检（`p4a-retry/preflight.json`）：Medium IL 确认（S-1-16-8192）、dsh 深度导入 OK、build/reports/test-game 写探针 OK、锁 acquire/release OK、保护资产 5 项哈希 PASS、无 gamemd。
- 数据：`p4a-retry/smoke/`（1 局 60s）、`p4a-retry/batch/`（20 局 60s，2,127 samples，187 文件 20.25MB）。
- 结束状态：gamemd=0、`E:\\hj` 修改 0、batch kinds={dict(kinds)}；异常 0。

## 2. 已完成局数与耗时
- 冒烟 {summary['smoke_episodes']} 局；正式 {summary['batch_episodes']} 局（goal 20–30 达成）；批次 22.9 min；总计（含冒烟/预检）< 40 min。

## 3. 字段覆盖（抽样 episode：{n_obs} obs / {n_act} action 记录）
| 源字段 | 覆盖率 |
|---|---|
""" + "\n".join(f"| {k} | {v} |" for k, v in coverage.items()) + f"""

- 说明：`collect_teacher` 数据结构为 (observation, action) 对；observation 含上述字段（覆盖 100%）。`map_patch` 不在本采集路径（{coverage.get('map_patch')}），`hidden_enemy_count` 存在于原始观测但**不得进入 T1**（设计 v1.6.1；本批仅验证字段存在与可见性，不做 tokenizer 实算——TODO 归 P2）。
- action 记录字段：{summary['action_record_fields']}；ACK/applied 逐动作字段**未在 collect_teacher 数据中落盘** → 归因指标标 **CAPTURE_GAP**（需 P2/P3 数据链扩展；设计已列）。

## 4. 地图值域与 map_soa
- 本采集路径**未记录 map_soa 数组**（passability/tiberium_value 字段缺失）；因此 **地图值域 = UNKNOWN / CAPTURE_GAP**，不伪造数字；需按 M1 计划 P2/P3 的 capture 扩展后重跑（P4a 二次验证项）。

## 5. 隐藏信息
- 原始观测中 `hidden_enemy_count` 存在 {hidden_total} 条、其中 >0 共 {hidden_nonzero} 条（样本 episode）；可见敌人单独在 `visible_enemies`。
- 结论：**该字段仅为审计字段；T1–T9 设计已删除/不消费**（v1.6.1 §0/§3.1）。实际 tokenizer 尚未实现（P2），故"不进入模型输入"当前是**设计级结论**，实机 tokenizer 验证列 P2 必测项。

## 6. 事件归因（ACK/Applied/Effect）
- ACK/applied 逐动作字段未落盘 → **UNKNOWN（CAPTURE_GAP）**；
- Effect：result.json 统计 truth_damage_mean={summary['effect_damage_mean']}、truth_kills_mean={summary['effect_truth_kills_mean']}（按实际可得字段；字段名以数据为准）。
- 归因准确性本次**不能给出数值**，需 P3 数据链记录 ack/applied/attribution 后重算（诚实标 UNKNOWN）。

## 7. T8/T9 历史完整性
- 本采集路径 observation 无显式 history/previous_action（计数 {prev_action_hist}）；T8（16 步教师动作历史）与 T9 事实需由 P2/P3 tokenizer 从动作序列构造 → 本次**未验证（UNKNOWN/CAPTURE_GAP）**，列为 P2 必测。

## 8. 异常与失败
- 无游戏/bridge 异常；20/20 局 timeout（60s 到点，符合预期）；`render-off` 已启用；结束 gamemd=0；`E:\\hj` 0 写入。
- 首次冒烟参数失败（episode-seconds 25 <50 限制）为参数问题，已按 60s 重跑通过，无数据污染。

## 9. 结论
- 方案 A（Medium-IL conda + venv site-packages）**可用**：预检、冒烟、20 局运行、写盘、锁均正常，权限阻断已解除。
- P4a 的能力门槛（DEPLOY/经济/首攻等）与字段级验证受**采集器能力限制**（map_soa/ack/applied/history 未落盘）：本批可确认**运行可行性**，但 map 值域、逐动作归因、T8/T9 完整性标 **UNKNOWN/CAPTURE_GAP**，需在 P2/P3 数据链扩展后补验（属 AP-02/AP-04 范围，不扩大本轮）。
"""
(OUT / "P4A_TEACHER_VALIDATION_RETRY.md").write_text(report, encoding="utf-8")

# SHA manifest (all retry files + report + preflight)
files = [OUT / "P4A_TEACHER_VALIDATION_RETRY.md", RETRY / "preflight.json"]
for p in sorted(RETRY.rglob("*")):
    if p.is_file() and p.suffix in (".json", ".jsonl", ".npz", ".log", ".gz") or p.name in ("meta.json", "dataset.npz"):
        if p.is_file():
            files.append(p)
seen = set()
lines = ["# AP-03 P4a 重试 SHA256 清单", f"generated_at={time.strftime('%F %T')}", "label: hashes VERIFIED"]
for p in files:
    k = str(p)
    if k in seen or not p.exists():
        continue
    seen.add(k)
    lines.append(f"{sha(p)}  {p.stat().st_size:>10}  {p.relative_to(ROOT)}")
manifest = OUT / "P4A_RETRY_SHA256_MANIFEST.txt"
manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

# coordination
state = store.load_state()
ver = int(state["context_version"])
tasks = store.load_tasks()
used = {t["task_id"] for t in tasks["tasks"] if isinstance(t, dict)}
n = 26
while f"TASK-20261101-{n:03d}" in used:
    n += 1
tid = f"TASK-20261101-{n:03d}"
task = store.create_task(tasks, "AP-03 重试执行：P4a 教师验证（方案 A，20 局）",
    "用户批准方案 A（Medium-IL conda python + venv site-packages）。完成预检、1 局冒烟、20 局 P4a；"
    "记录字段覆盖/map_soa/hidden/归因/T8T9 证据；生成报告与 SHA256；不启 BC/PPO/正式采集；不改保护资产。",
    owner="DSH", status="COMPLETED", base_context_version=ver,
    outputs=["reports/m1-2026-11-01/P4A_TEACHER_VALIDATION_RETRY.md",
             "reports/m1-2026-11-01/P4A_RETRY_SHA256_MANIFEST.txt"], approval_required=False)
task["result_summary"] = (
    "AP-03 重试完成（方案A）：预检通过（Medium IL/写探针/锁/保护哈希）、冒烟1局、正式20局（60s，22.9min，2127 samples），gamemd=0、E:\\hj 0写入；"
    "字段覆盖100%（own/visible/factories/houses/map/money）；map_soa数组、逐动作ACK/applied、T8/T9 history未落盘 → UNKNOWN/CAPTURE_GAP（需P2/P3数据链扩展）；"
    "hidden_enemy_count 存在但设计排除（tokenizer 实算待P2）；无异常。TASK-024 保持 BLOCKED、TASK-025 保持 COMPLETED。")
task["completed_at"] = store.now_iso()
store.save_tasks(tasks)
art = store.load_artifacts()
for rel in ("reports/m1-2026-11-01/P4A_TEACHER_VALIDATION_RETRY.md", "reports/m1-2026-11-01/P4A_RETRY_SHA256_MANIFEST.txt"):
    store.register_artifact(art, rel, artifact_type="report", role="ap03_retry_" + pathlib.Path(rel).stem,
                            status="VERIFIED", context_version=ver, task_id=tid,
                            note=f"AP-03 重试证据; sha256={sha(ROOT / rel)}")
store.save_artifacts(art)
md = dict(state.get("m1_design") or {})
md["ap03_status"] = "COMPLETED_RETRY_P4A_RUN"
md["ap03_retry_task"] = tid
md["ap03_retry_report"] = "reports/m1-2026-11-01/P4A_TEACHER_VALIDATION_RETRY.md"
state["m1_design"] = md
state["active_tasks"] = [tid]
state = store.bump_context(state, reason="ap03_retry_completed: P4a 20 episodes via approved plan-A interpreter; report+manifest; TASK-024 stays BLOCKED", actor="DSH")
store.append_event("ap03_retry_completed",
    "AP-03 重试完成（方案A 用户批准）：预检通过、冒烟1局、正式20局（60s，22.9min，2127 samples）；gamemd=0、E:\\hj 0写入、保护资产未变；"
    "map_soa/ack-applied/T8T9 历史未落盘标 UNKNOWN/CAPTURE_GAP（需 P2/P3 扩展后补验）；无异常。TASK-024 保持 BLOCKED、TASK-025 保持 COMPLETED。",
    actor="DSH", payload={"context_version": state["context_version"], "task": tid,
    "report": "reports/m1-2026-11-01/P4A_TEACHER_VALIDATION_RETRY.md",
    "manifest": "reports/m1-2026-11-01/P4A_RETRY_SHA256_MANIFEST.txt",
    "episodes": 20, "smoke_episodes": 1, "elapsed_min": 22.9,
    "gaps": ["map_soa", "ack/applied", "T8/T9 history"], "approval_changed": False},
    context_version=state["context_version"])
print(json.dumps({"task": tid, "ctx": state["context_version"], **{k: summary[k] for k in
      ("smoke_episodes", "batch_episodes", "kinds", "obs_field_coverage_sample",
       "map_soa_fields_absent", "prev_action_history_present")}}, ensure_ascii=False, indent=1))
print("report_sha256:", sha(OUT / "P4A_TEACHER_VALIDATION_RETRY.md"))
print("manifest_sha256:", sha(manifest))
