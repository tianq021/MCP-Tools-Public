"""把 SR2 的 MCP server 条目追加进 DSH profile 的 cordis.patch.yml（带备份 + YAML 校验）。
用法：python -B tool\\register_dsh_mcp.py [--config <path>]
"""
from __future__ import annotations

import argparse
import datetime
import shutil
import sys
import os
import tempfile
from pathlib import Path

DEFAULT_PATH = r"F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml"

BLOCK = """
# Slime Rancher 2 bridge: MCP stdio front-end -> local HTTP (127.0.0.1:18762, token)
# -> in-game MelonLoader plugin. P0 tools: sr2_health / sr2_gameinfo / sr2_saves / sr2_echo.
- insert:
    - id: mcp-slime-rancher-2
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: slime_rancher2
        transport: stdio
        command: 'C:\\Program Files\\Python310\\python.exe'
        args:
          - '-B'
          - 'E:\\SlimeRancher2MCP\\mcp\\server.py'
        cwd: 'E:\\SlimeRancher2MCP\\mcp'
        toolCallTimeoutMs: 30000
        failOnStartupError: false
        env:
          PYTHONUTF8: '1'
          PYTHONIOENCODING: 'utf-8'
"""


WATCH_ID = "sr2-durable-chat-watch"
WATCH_MODULE = (Path(__file__).resolve().parents[1] / "mcp" / "dsh-chat-watch.mjs").as_uri()
WATCH_BLOCK = f"""
- insert:
    - id: {WATCH_ID}
      name: '{WATCH_MODULE}'
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_PATH)
    ap.add_argument("--apply", action="store_true", help="真正写入；默认只预演")
    args = ap.parse_args()

    path = args.config
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")

    addition = ("" if "id: mcp-slime-rancher-2" in text else BLOCK)
    if f"id: {WATCH_ID}" not in text:
        addition += WATCH_BLOCK
    if not addition:
        print("ALREADY_PRESENT")
        return 0

    if not text.endswith("\n"):
        text += "\n"
    new_text = text + addition

    try:
        import yaml

        before = yaml.safe_load(text)
        after = yaml.safe_load(new_text)
        if not isinstance(after, list):
            raise SystemExit("校验失败：根节点不是列表")
        ids = []
        for entry in after:
            if isinstance(entry, dict) and "insert" in entry:
                for ins in entry["insert"]:
                    if isinstance(ins, dict) and "id" in ins:
                        ids.append(ins["id"])
        if len(ids) != len(set(ids)):
            raise SystemExit("校验失败：insert id 有重复")
        print(f"yaml ok: entries {len(before)} -> {len(after)}")
        print("insert ids:", ", ".join(ids))
    except ImportError:
        print("警告：没有 PyYAML，跳过结构校验")

    if not args.apply:
        print("预演：未写入。加 --apply 才会修改。")
        print("将追加：")
        print(addition)
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path + ".bak-" + stamp
    shutil.copy2(path, backup)
    print("backup:", backup)

    data = new_text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(os.path.abspath(path)), delete=False) as temp:
        temp.write(data)
    try:
        if Path(path).read_bytes() != raw:
            raise RuntimeError("DSH profile changed during update")
        os.replace(temp.name, path)
    finally:
        Path(temp.name).unlink(missing_ok=True)
    print("written:", path, len(data), "bytes; bom=", bom)
    print("提示：DSH 会话启动时才加载 MCP 配置，需要重开会话生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
