from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .config import AgentError
from .security import Redactor, is_link, reject_link_ancestors

EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".json", ".toml",
              ".yaml", ".yml", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".cs",
              ".html", ".css", ".sql", ".ps1", ".sh", ".ini", ".cfg"}
SKIP_DIRS = {"node_modules", "venv", "__pycache__", "dist", "build", "runs", "outputs",
             "vendor", "coverage", "target"}
SECRET_NAMES = ("*secret*", "*credential*", "*password*", "*token*", "*.pem", "*.key",
                "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "auth.json", "*.env*", "*lock*")


@dataclass
class Snapshot:
    root: Path
    files: dict[str, str]
    hashes: dict[str, str]
    skipped: list[dict]

    def manifest(self):
        return {"files": [{"path": p, "sha256": self.hashes[p],
                           "sent_sha256": hashlib.sha256(t.encode()).hexdigest(),
                           "lines": len(t.splitlines()), "bytes": len(t.encode())}
                          for p, t in self.files.items()], "skipped": self.skipped}

    def context(self):
        return [{"path": p, "numbered_lines": [f"{i}: {s}" for i, s in enumerate(t.splitlines(), 1)]}
                for p, t in self.files.items()]

    def changed_files(self):
        changed = []
        for name, digest in self.hashes.items():
            path = self.root / name
            try:
                reject_link_ancestors(path)
                with path.open("rb") as handle:
                    current = handle.read(300001)
                if len(current) > 300000 or hashlib.sha256(current).hexdigest() != digest:
                    changed.append(name)
            except (OSError, AgentError):
                changed.append(name)
        return changed


def collect(root: Path, config: dict, redactor: Redactor) -> Snapshot:
    reject_link_ancestors(root.absolute())
    root = root.resolve(strict=True)
    files, hashes, skipped = {}, {}, []
    total, visited = 0, 0
    def skip(path, reason):
        if len(skipped) < 500:
            skipped.append({"path": path, "reason": reason})
    for current, dirs, names in os.walk(root, followlinks=False):
        kept = []
        for name in sorted(dirs):
            p = Path(current) / name
            rel = p.relative_to(root).as_posix()
            if name.startswith(".") or name.lower() in SKIP_DIRS or is_link(p):
                skip(rel, "excluded-directory-or-link")
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            visited += 1
            if visited > 10000:
                raise AgentError("文件数量超过扫描上限；请缩小被分析目录。")
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            if name.startswith(".") or any(fnmatch.fnmatch(name.lower(), p) for p in SECRET_NAMES):
                skip(rel, "sensitive-or-hidden-name")
                continue
            if path.suffix.lower() not in EXTENSIONS:
                skip(rel, "unsupported-extension")
                continue
            if config["include"] and not any(fnmatch.fnmatchcase(rel, p) for p in config["include"]):
                skip(rel, "not-in-includes")
                continue
            if any(fnmatch.fnmatchcase(rel, p) for p in config["exclude"]):
                skip(rel, "user-excluded")
                continue
            try:
                reject_link_ancestors(path)
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    skip(rel, "not-regular-or-hardlink")
                    continue
                if info.st_size > config["max_file_bytes"]:
                    skip(rel, "file-too-large")
                    continue
                with path.open("rb") as handle:
                    data = handle.read(config["max_file_bytes"] + 1)
                if len(data) > config["max_file_bytes"] or b"\0" in data:
                    skip(rel, "too-large-or-binary")
                    continue
                text = redactor.text(data.decode("utf-8-sig"))
                if len(files) >= config["max_files"] or total + len(text.encode()) > config["max_total_bytes"]:
                    skip(rel, "snapshot-budget")
                    continue
                files[rel], hashes[rel] = text, hashlib.sha256(data).hexdigest()
                total += len(text.encode())
            except (OSError, UnicodeError, AgentError):
                skip(rel, "unreadable-or-link")
    if not files:
        raise AgentError("没有可分析的 UTF-8 文本文件。请检查目录和筛选配置。")
    return Snapshot(root, files, hashes, skipped)
