"""Read complete inbox records. Cursor is the byte offset across retained archives."""
import json
import re
from pathlib import Path

from bridge_client import load_config


def chat_dir():
    return Path(load_config()["log_path"]).parent.parent / "chat"


def files(directory):
    dated = []
    for path in directory.glob("inbox.*.jsonl"):
        match = re.fullmatch(r"inbox\.(\d{8}_\d{6})(?:[._-](\d+))?\.jsonl", path.name)
        if match:
            dated.append((match[1], int(match[2] or 0), path))
    return [entry[-1] for entry in sorted(dated)] + [directory / "inbox.jsonl"]


def read_history(cursor=0, limit=50, directory=None):
    directory = directory or chat_dir()
    for attempt in range(3):
        ordered = files(directory)
        offset = 0
        messages = []
        next_cursor = cursor
        try:
            for path in ordered:
                if not path.exists() and path == ordered[-1]:
                    break
                size = path.stat().st_size
                if cursor < offset + size:
                    with path.open("rb") as stream:
                        start = max(0, cursor - offset)
                        if start:
                            stream.seek(start - 1)
                            if stream.read(1) != b"\n":
                                raise ValueError("cursor must be a returned record boundary")
                        stream.seek(start)
                        while stream.tell() < size and len(messages) < limit:
                            line = stream.readline(size - stream.tell())
                            if not line.endswith(b"\n"):
                                break
                            messages.append(json.loads(line))
                            next_cursor = offset + stream.tell()
                offset += size
                if len(messages) >= limit:
                    break
            if ordered == files(directory):
                if cursor > offset:
                    raise ValueError("cursor exceeds retained history")
                return {"count": len(messages), "messages": messages, "next_cursor": next_cursor}
        except (FileNotFoundError, ValueError):
            if ordered == files(directory):
                raise
    raise OSError("inbox rotating; retry with the same cursor")

