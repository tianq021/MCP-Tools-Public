from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from .config import AgentError, load_config
from .engine import Budget, run_analysis
from .providers import CodexProvider, DeepSeekProvider, MockProvider, bounded_process
from .security import Redactor, reject_link_ancestors

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def doctor(config):
    codex = CodexProvider(config)
    info = {"python": sys.version.split()[0], "codex_executable": codex.executable,
            "deepseek_key_configured": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()),
            "codex_compatible": False, "codex_login": "unknown", "network_tested": False}
    try:
        codex.preflight()
        info["codex_compatible"] = True
        code, out, err = bounded_process([codex.executable, "login", "status"], None,
                                        codex.environment(), "", 15, 100000)
        status = (out + err).lower()
        info["codex_login"] = "logged_in" if code == 0 and "logged in" in status else "not_confirmed"
        if "access is denied" in status or "os error 5" in status:
            info["codex_cache_warning"] = "当前进程无权写 Codex 缓存；在正常用户终端运行可再次验证。"
    except AgentError as exc:
        info["codex_error"] = str(exc)
    print(json.dumps(Redactor().clean(info), ensure_ascii=False, indent=2))
    return 0 if info["codex_compatible"] and info["deepseek_key_configured"] else 2


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Codex + DeepSeek 有界只读协作分析")
    parser.add_argument("--config", type=Path, help="可选 JSON 配置文件（不存放 Key）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查环境、登录与凭证是否存在；不发送模型请求")
    for name in ("run", "preview"):
        command = sub.add_parser(name, help="运行分析" if name == "run" else "预览脱敏快照，不访问网络")
        command.add_argument("--project", required=True, type=Path)
        command.add_argument("--question", required=True)
        command.add_argument("--output", type=Path, default=PROJECT_ROOT / "runs")
        command.add_argument("--mock", action="store_true", help="使用明确标注的离线模拟模型")
    smoke = sub.add_parser("smoke-codex", help="仅使用内置合成片段验证一次真实 Codex 调用，会消耗账号额度")
    smoke.add_argument("--output", type=Path, default=PROJECT_ROOT / "runs")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "doctor":
            return doctor(config)
        if args.command == "smoke-codex":
            provider = CodexProvider(config)
            provider.preflight()
            reject_link_ancestors(args.output.absolute())
            run_dir = args.output.resolve() / ("codex-smoke-" + uuid.uuid4().hex[:8])
            run_dir.mkdir(parents=True)
            prompt = json.dumps({"question": "只说明这段函数的返回值，最多一个 claim，简短回答。",
                                 "assigned_role": provider.role, "round": 1, "total_rounds": 1,
                                 "untrusted_snapshot": [{"path": "sample.py", "numbered_lines": ["1: def answer():", "2:     return 42"]}],
                                 "previous_round": []}, ensure_ascii=False)
            budget = Budget(config)
            budget.reserve(provider.name, prompt)
            try:
                reply = provider.generate(prompt, run_dir)
            except AgentError as exc:
                failure = {"status": "failed", "error": str(exc), "budget": budget.state()}
                (run_dir / "result.json").write_text(json.dumps(Redactor().clean(failure), ensure_ascii=False, indent=2), encoding="utf-8")
                raise
            budget.record(provider.name, reply)
            from .protocol import verify
            record = {"answer": reply.answer, "model": reply.model, "budget": budget.state(),
                      "verification": verify(reply.answer, {"sample.py": "def answer():\n    return 42\n"})}
            (run_dir / "result.json").write_text(json.dumps(Redactor().clean(record), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Codex 单独验证完成：{run_dir}")
            return 0
        providers = ([MockProvider("codex", CodexProvider.role), MockProvider("deepseek", DeepSeekProvider.role)]
                     if args.mock else [CodexProvider(config), DeepSeekProvider(config)])
        run_dir, result = run_analysis(args.project, args.output, args.question, config, providers,
                                      mock=args.mock, preview=args.command == "preview")
        print(f"状态：{result['status']}\n报告：{run_dir / 'report.md'}")
        if result["error"]:
            print(result["error"], file=sys.stderr)
        return 0 if result["status"] in ("completed", "preview") else 1
    except (AgentError, OSError) as exc:
        message = str(exc) if isinstance(exc, AgentError) else "本地路径不可用或没有访问权限。"
        print(Redactor().text(message), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
