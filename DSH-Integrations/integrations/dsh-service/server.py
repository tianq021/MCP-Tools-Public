"""DSH service lifecycle MCP for Codex. Uses the existing installed DSH launcher."""
import argparse
import json
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from service import ServiceManager

manager = ServiceManager()
mcp = FastMCP("dsh", instructions=(
    "访问本机 DSH、读取其任务或向其派发任务之前，先调用 ensure_running。"
    "它检测已认证的 DSH API，服务未运行时自动使用现有脚本在后台启动。"
    "仅在 ready=true 后继续 DSH 操作。status 只读检查，不会启动。"
    "这两个工具不派发任务，也不停止或重启现有服务。"
    "Qwen 由学校部署，使用免费。考虑到数据安全，一般只用于普通查询和非敏感数据分析，避免发送敏感信息。"
    "DeepSeek 官方模型通过付费 API 调用，使用会产生费用。"))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                     idempotentHint=True, openWorldHint=False))
def status() -> dict:
    """只读检测本机 DSH 服务及 API 是否可用；不启动服务，不返回认证令牌或会话内容。"""
    return manager.status()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                     idempotentHint=True, openWorldHint=False))
def ensure_running() -> dict:
    """操作DSH前调用：检测服务，未运行则自动后台启动，等待API可用。已有服务不重复启动。"""
    return manager.ensure_running()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--ensure-running", action="store_true")
    args = parser.parse_args()
    if args.status or args.ensure_running:
        print(json.dumps(status() if args.status else ensure_running(), ensure_ascii=False))
    else:
        mcp.run(transport="stdio")
