from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentError
from .protocol import SYSTEM, validate, verify
from .security import Redactor, safe_output
from .snapshot import collect


class Budget:
    def __init__(self, config):
        self.config, self.calls, self.tokens, self.codex_calls = config, 0, 0, 0
        self.deepseek_reserved_usd = 0.0
        self.deepseek_reported_usd = 0.0

    def reserve(self, name, prompt):
        size = len((SYSTEM + prompt).encode("utf-8"))
        if size > self.config["max_prompt_bytes"]:
            raise AgentError("本轮上下文超过大小限制；请缩小快照或减少模型输出。")
        if self.calls >= self.config["max_calls"] or self.tokens >= self.config["max_total_tokens"]:
            raise AgentError("达到总调用或 token 上限，已停止。")
        if name == "codex" and self.codex_calls >= self.config["codex"]["max_calls"]:
            raise AgentError("达到 Codex 调用次数上限。")
        if name == "deepseek":
            cfg = self.config["deepseek"]
            # UTF-8 bytes + envelope allowance conservatively overestimates ordinary tokenizer input.
            upper_input = size + 4096
            if self.tokens + upper_input + cfg["max_output_tokens"] > self.config["max_total_tokens"]:
                raise AgentError("剩余 token 预算不足以预留本次 DeepSeek 请求。")
            charge = (upper_input * cfg["input_usd_per_million"] +
                      cfg["max_output_tokens"] * cfg["output_usd_per_million"]) / 1000000
            if self.deepseek_reserved_usd + charge > cfg["max_usd"]:
                raise AgentError("DeepSeek 剩余费用预算不足，已在发送请求前停止。")
            self.deepseek_reserved_usd += charge  # No refund even if the request times out.
        self.calls += 1
        if name == "codex":
            self.codex_calls += 1

    def record(self, name, reply):
        self.tokens += reply.input_tokens + reply.output_tokens
        if name == "deepseek":
            cfg = self.config["deepseek"]
            self.deepseek_reported_usd += (reply.input_tokens * cfg["input_usd_per_million"] +
                                          reply.output_tokens * cfg["output_usd_per_million"]) / 1000000

    def state(self):
        return {"attempted_calls": self.calls, "codex_calls": self.codex_calls, "reported_tokens": self.tokens,
                "deepseek_reserved_usd": round(self.deepseek_reserved_usd, 8),
                "deepseek_usage_estimate_usd": round(self.deepseek_reported_usd, 8),
                "codex_usd": None, "token_limit_is_post_call_for_codex": True}


class Journal:
    def __init__(self, path, redactor):
        self.path, self.redactor = path, redactor

    def write(self, name, value):
        target = self.path / name
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(self.redactor.clean(value), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)

    def event(self, kind, **data):
        record = self.redactor.clean({"time": datetime.now(timezone.utc).isoformat(), "type": kind, **data})
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()


def make_report(result):
    rows = ["# 多模型只读分析报告", "", f"状态：{result['status']}；模拟模式：{result['mock']}", "",
            f"问题：{result['question']}", "", "引用核验仅确认原文与行号匹配，不证明结论正确；没有执行项目测试。", ""]
    if result["mock"]:
        rows += ["本报告使用离线模拟参与者；以下 token 和费用均为预算逻辑的演示数值，实际没有模型请求或费用。", ""]
    if result.get("error"):
        rows += [f"停止原因：{result['error']}", ""]
    rows += ["## 用量与范围", "", f"调用次数：{result['budget']['attempted_calls']}；"
             f"已报告 token：{result['budget']['reported_tokens']}。", "",
             f"DeepSeek 预留费用：${result['budget']['deepseek_reserved_usd']:.6f}；"
             "Codex 美元费用未知，按账号计费或额度处理。", "",
             "只有 manifest.json 列出的 files 进入分析，skipped 包含未覆盖范围。", ""]
    latest = {}
    for turn in result["turns"]:
        latest[turn["provider"]] = turn
    for name, turn in latest.items():
        answer = turn["answer"]
        rows += [f"## {name} 最新结论（第 {turn['round']} 轮）", "", answer["summary"], ""]
        for claim, check in zip(answer["claims"], turn["verification"]):
            rows += [f"- [{claim['stance']}] {claim['statement']}", f"  依据：{claim['reason']}"]
            for ref in check["references"]:
                label = "原文匹配" if ref["reference_matches_snapshot"] else "引用未通过核验"
                rows += [f"  引用：{ref['path']}:{ref['line_start']}-{ref['line_end']}（{label}）", f"  原文：{ref['quote']}"]
            if not check["references"]:
                rows += ["  无文件引用，不能视为已证实。"]
        rows += ["", "对质回应：", ""] + ["- " + s for s in answer["responses"]]
        rows += ["", "未决问题与限制：", ""] + ["- " + s for s in answer["questions"] + answer["limitations"]] + [""]
    rows += ["## 共同判断与分歧", "", "只将最终轮中完全相同的陈述做机械归组；语义相近的结论不自动合并。", ""]
    grouped = {}
    for name, turn in latest.items():
        for claim in turn["answer"]["claims"]:
            grouped.setdefault(claim["statement"].strip(), {})[name] = claim["stance"]
    for statement, stances in grouped.items():
        unanimous = len(stances) == len(latest) and len(latest) >= 2 and set(stances.values()) == {"supported"}
        label = "模型共同支持（仍需人工核验）" if unanimous else "存在分歧、未知或尚未共同确认"
        rows += [f"- {label}：{statement}；" + "，".join(f"{n}={v}" for n, v in stances.items())]
    if result.get("source_changes"):
        rows += ["", "采集后的源文件发生变化，结果仅针对已保存的快照：", ""] + ["- " + s for s in result["source_changes"]]
    rows += ["", "## 记录", "", "完整各轮观点、引用核验与用量见 discussion.json；"
             "events.jsonl 记录开始、完成或中断；snapshot.json 保存脱敏快照。", ""]
    return "\n".join(rows)


def run_analysis(source, output, question, config, providers, mock=False, preview=False):
    if not question.strip() or len(question) > 4000:
        raise AgentError("问题不得为空，且最多 4000 个字符。")
    if len(providers) < 2 or len({p.name for p in providers}) != len(providers):
        raise AgentError("至少需要两个名称不同的模型参与者。")
    source, output = safe_output(Path(source), Path(output))
    redactor = Redactor()
    snapshot = collect(source, config["snapshot"], redactor)
    if not preview:
        # Check both interfaces before the first billable request; missing DeepSeek key cannot burn Codex quota.
        for provider in sorted(providers, key=lambda p: p.name == "codex"):
            provider.preflight()
    run_dir = output / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True)
    journal, budget = Journal(run_dir, redactor), Budget(config)
    journal.write("manifest.json", snapshot.manifest())
    journal.write("snapshot.json", snapshot.files)
    journal.write("config.json", config)
    result = {"status": "preview" if preview else "running", "mock": mock, "question": redactor.text(question),
              "turns": [], "budget": budget.state(), "source_changes": [], "error": None}
    journal.event("started", mode="preview" if preview else "mock" if mock else "live")
    journal.write("discussion.json", result)
    previous = []
    try:
        if not preview:
            for round_index in range(1, config["rounds"] + 1):
                current = []
                # Barrier: every participant sees the SAME previous round, never this round's earlier reply.
                for provider in providers:
                    prompt_data = {"question": result["question"], "assigned_role": provider.role,
                                   "round": round_index, "total_rounds": config["rounds"],
                                   "untrusted_snapshot": snapshot.context(), "previous_round": previous}
                    prompt = json.dumps(redactor.clean(prompt_data), ensure_ascii=False)
                    budget.reserve(provider.name, prompt)
                    journal.event("call_started", provider=provider.name, round=round_index, budget=budget.state())
                    call_dir = run_dir / f"call-{budget.calls:02d}"
                    call_dir.mkdir()
                    start = time.monotonic()
                    reply = provider.generate(prompt, call_dir)
                    budget.record(provider.name, reply)
                    answer = validate(redactor.clean(reply.answer))
                    turn = {"round": round_index, "provider": provider.name, "role": provider.role,
                            "model": reply.model, "answer": answer, "verification": verify(answer, snapshot.files),
                            "usage": {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens},
                            "seconds": round(time.monotonic() - start, 3)}
                    current.append(turn)
                    result["turns"].append(turn)
                    result["budget"] = budget.state()
                    journal.write("discussion.json", result)
                    journal.event("call_completed", provider=provider.name, round=round_index, usage=turn["usage"])
                    if budget.tokens > config["max_total_tokens"]:
                        raise AgentError("本次回复使累计 token 超过上限，已保存结果并停止后续调用。")
                    if budget.deepseek_reported_usd > config["deepseek"]["max_usd"]:
                        raise AgentError("服务实际用量估算超出 DeepSeek 预算，已停止后续调用。")
                previous = [{"provider": t["provider"], "answer": t["answer"], "verification": t["verification"]}
                            for t in current]
            result["status"] = "completed"
    except KeyboardInterrupt:
        result["status"], result["error"] = "interrupted", "用户取消；不会自动重试未完成请求。"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = redactor.text(str(exc)) if isinstance(exc, AgentError) else "发生内部错误；请运行测试及 doctor，不自动重试。"
    finally:
        result["source_changes"] = snapshot.changed_files()
        if result["source_changes"] and result["status"] == "completed":
            result["status"] = "source_changed"
        result["budget"] = budget.state()
        journal.write("discussion.json", result)
        (run_dir / "report.md").write_text(redactor.text(make_report(result)), encoding="utf-8")
        journal.event("finished", status=result["status"], error=result["error"], budget=budget.state())
    return run_dir, result
