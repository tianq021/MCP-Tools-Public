from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ollama_rag.api.service import RAGService
from ollama_rag.tools import TOOL_NAME


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 128 * 1024


class RAGHTTPServer(ThreadingHTTPServer):
    """Threaded loopback HTTP adapter around the stable RAGService API."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: RAGService,
    ) -> None:
        self.rag_service = service
        super().__init__(server_address, RAGRequestHandler)


class RAGRequestHandler(BaseHTTPRequestHandler):
    server: RAGHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "OllamaLocalRAG/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_object(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except ValueError as error:
            raise ValueError("Content-Length 必须是整数。") from error
        if content_length <= 0:
            raise ValueError("请求体不能为空。")
        if content_length > MAX_REQUEST_BYTES:
            raise OverflowError(f"请求体不能超过 {MAX_REQUEST_BYTES} 字节。")
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"请求体不是合法 UTF-8 JSON：{error}") from error
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象。")
        return payload

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlsplit(self.path).path != "/health":
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error_code": "NOT_FOUND", "error": "接口不存在。"},
            )
            return

        try:
            status = self.server.rag_service.status()
            embedding_client = self.server.rag_service.embedding_client
            ollama_version = embedding_client.version()
            embedding_ready = embedding_client.model in embedding_client.available_models()
            ready = bool(status["chunk_count"]) and embedding_ready
            self._send_json(
                HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": ready,
                    "service": "ollama-local-rag",
                    "ollama_version": ollama_version,
                    "embedding_model_ready": embedding_ready,
                    **status,
                },
            )
        except Exception as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "error_code": "RAG_UNAVAILABLE",
                    "error": f"{type(error).__name__}: {error}",
                },
            )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlsplit(self.path).path != "/search":
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error_code": "NOT_FOUND", "error": "接口不存在。"},
            )
            return

        try:
            payload = self._read_json_object()
        except OverflowError as error:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error_code": "REQUEST_TOO_LARGE", "error": str(error)},
            )
            return
        except ValueError as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error_code": "INVALID_JSON", "error": str(error)},
            )
            return

        result = self.server.rag_service.tools.execute(TOOL_NAME, payload)
        if result.get("ok"):
            self._send_json(HTTPStatus.OK, result)
            return

        error_text = str(result.get("error", "知识库检索失败。"))
        execution_failed = error_text.startswith("工具执行失败")
        self._send_json(
            HTTPStatus.SERVICE_UNAVAILABLE if execution_failed else HTTPStatus.BAD_REQUEST,
            {
                **result,
                "error_code": "RAG_UNAVAILABLE" if execution_failed else "INVALID_ARGS",
            },
        )

    def log_message(self, message_format: str, *args: object) -> None:
        message = message_format % args
        if sys.stderr is not None:
            sys.stderr.write(f"[rag-http] {self.client_address[0]} {message}\n")


def create_server(
    service: RAGService,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> RAGHTTPServer:
    return RAGHTTPServer((host, port), service)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动本地 RAG HTTP 检索服务。")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("为保护本地文档，RAG HTTP 服务只允许监听回环地址。")
    service = RAGService.create(
        args.project_root,
        database=args.database,
        default_top_k=max(1, min(args.top_k, 8)),
    )
    server = create_server(service, host=args.host, port=args.port)
    run_directory = args.project_root.resolve() / "storage" / "run"
    run_directory.mkdir(parents=True, exist_ok=True)
    pid_path = run_directory / "rag_api.pid"
    process_id = os.getpid()
    pid_path.write_text(str(process_id), encoding="ascii")
    status = service.status()
    if sys.stdout is not None:
        print(
            json.dumps(
                {
                    "event": "rag_http_started",
                    "pid": process_id,
                    "url": f"http://{args.host}:{server.server_port}",
                    **status,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if pid_path.read_text(encoding="ascii").strip() == str(process_id):
                pid_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
