from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from .config import AgentError


class Redactor:
    def __init__(self, secrets=()):
        values = list(secrets) + [v for k, v in os.environ.items()
                                 if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I)]
        self.secrets = sorted({v for v in values if v and len(v) >= 4}, key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                       lambda m: "[REDACTED PRIVATE KEY]" + "\n" * m[0].count("\n"), value, flags=re.S)
        value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16})\b",
                       "[REDACTED]", value)
        value = re.sub(r"(?im)(\b(?:api[_-]?key|access[_-]?token|secret|password|authorization)\b[\"']?\s*[:=]\s*)([^\r\n]+)",
                       r"\1[REDACTED]", value)
        return value

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, dict):
            return {self.text(str(k)): self.clean(v) for k, v in value.items()}
        return value


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def reject_link_ancestors(path: Path):
    for p in (path, *path.parents):
        if p.exists() and is_link(p):
            raise AgentError("路径包含符号链接或 Windows junction，请使用真实目录。")


def safe_output(source: Path, output: Path) -> tuple[Path, Path]:
    reject_link_ancestors(source.absolute())
    reject_link_ancestors(output.absolute())
    source, output = source.resolve(strict=True), output.resolve()
    if not source.is_dir():
        raise AgentError("被分析路径必须是目录。")
    if output == source or output.is_relative_to(source):
        raise AgentError("报告目录不能位于被分析项目内；请用 --output 指定外部目录。")
    return source, output
