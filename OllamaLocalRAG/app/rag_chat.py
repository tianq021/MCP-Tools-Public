from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ollama_rag.api import RAGService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="让 Gemma4 自动决定是否调用本地 RAG 知识库。"
    )
    parser.add_argument("question", nargs="?", help="要询问的问题；省略时进入交互模式。")
    parser.add_argument("--database", type=Path, help="SQLite 向量库路径。")
    parser.add_argument("--model", default="gemma4:12b-it-qat", help="Ollama 回答模型。")
    parser.add_argument("--top-k", type=int, default=5, help="默认返回文本块数量。")
    return parser.parse_args()


def print_answer(question: str, service: RAGService, *, top_k: int) -> None:
    answer = service.ask(question, top_k=top_k)
    route = "知识库检索" if answer.used_knowledge_base else "直接回答"
    forced = "（规则触发）" if answer.forced_search else "（模型判断）"
    print(f"\n路由：{route}{forced}")
    print(f"\n{answer.content or '[模型没有返回文本]'}")
    if answer.sources:
        print("\n检索来源：")
        seen: set[tuple[str, str]] = set()
        for source in answer.sources:
            key = (source["source_name"], source["locator"])
            if key in seen:
                continue
            seen.add(key)
            print(
                f"- {source['source_name']} | {source['locator']} "
                f"| score={source['score']:.4f}"
            )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    service = RAGService.create(
        PROJECT_ROOT,
        database=args.database,
        chat_model=args.model,
        default_top_k=max(1, min(args.top_k, 8)),
    )
    status = service.status()
    print(f"向量库：{status['database']}")
    print(f"文本块：{status['chunk_count']}")
    print(f"回答模型：{status['chat_model']}")
    print(f"可用工具：{', '.join(status['tools'])}")

    if args.question:
        print_answer(args.question, service, top_k=args.top_k)
        return

    print("输入问题开始对话，输入 exit 退出。")
    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question.lower() in {"exit", "quit", "退出"}:
            break
        if question:
            print_answer(question, service, top_k=args.top_k)


if __name__ == "__main__":
    main()
