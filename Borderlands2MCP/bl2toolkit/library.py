"""本地黑枪 / 特殊装备代码库。

把参考资料目录里的文本文件（黑枪.txt、装备代码.txt 等）扫描成结构化 JSON，
这样就能按中文名检索、一键导入到角色存档。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from . import DATA_DIR, itemcodes

LIBRARY_FILE = DATA_DIR / "code_library.json"

#: 默认参考资料目录（用户收藏的无主之地2改枪资料）
REFERENCE_DIR = DATA_DIR.parent / "无主之地2改黑枪" / "Bi" / "SE改武相关教学以及黑武代码"


def load_library() -> list[dict[str, Any]]:
    if not LIBRARY_FILE.is_file():
        return []
    try:
        return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_library(entries: list[dict[str, Any]]) -> Path:
    LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return LIBRARY_FILE


def search(query: str = "", *, kind: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    """按名称/备注/来源模糊搜索代码库。kind: weapon / item / None。"""
    entries = load_library()
    if kind == "weapon":
        entries = [e for e in entries if e.get("is_weapon")]
    elif kind == "item":
        entries = [e for e in entries if not e.get("is_weapon")]
    if query:
        q = query.lower()
        entries = [
            e
            for e in entries
            if q in e.get("name", "").lower()
            or q in e.get("note", "").lower()
            or q in e.get("category", "").lower()
            or q in e.get("source", "").lower()
        ]
    return entries[:limit]


def find_by_name(name: str) -> Optional[dict[str, Any]]:
    entries = load_library()
    for e in entries:
        if e.get("name") == name:
            return e
    q = name.lower()
    for e in entries:
        if q in e.get("name", "").lower():
            return e
    return None


# --------------------------------------------------------------------------
# 构建代码库
# --------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^[\s\-=*#]{3,}.*$")
_NOTE_RE = re.compile(r"[（(]([^（）()]*)[）)]\s*$")
#: 排版提示行（例如 "枪管GD_Anemone_Weapons..."），归到上一条代码的提示里
_HINT_PREFIXES = ("枪管", "枪身", "握把", "瞄具", "枪托", "弹匣", "元素", "材质", "配件", "枪机", "GD_")
#: 这些行是分区标题，不是下一条代码的名字
_CATEGORY_HINTS = ("代码", "武器代码", "分享", "目录", "说明")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    return text


def _looks_like_name(line: str) -> bool:
    """判断"上一行"是否像物品名字（用于"名字独占一行、代码下一行"的排版）。"""
    line = line.strip()
    if not line or "BL2(" in line:
        return False
    if len(line) > 40:
        return False
    if line.startswith(("说明", "注", "作者", "本文")):
        return False
    if line.endswith(_CATEGORY_HINTS):
        return False
    return True


def _next_nonempty(lines: list[str], start: int) -> int:
    for i in range(start, len(lines)):
        if lines[i].strip():
            return i
    return -1


def parse_text_file(path: Path) -> list[dict[str, Any]]:
    """从一份资料文本里抽出所有 BL2 代码条目。

    兼容几种常见排版：
    - 名字和代码同一行：``虫牙 BL2(...)`` / ``虫牙：BL2(...)`` / ``虫牙BL2(...)``
    - 名字独占一行、代码在下一行
    - 分区标题（``护盾－Shield====`` / ``个人物品`` / ``无雨分享``）
    - 代码后跟一行 ``枪管GD_...`` 说明
    """
    text = _read_text(path)
    lines = [ln.rstrip() for ln in text.splitlines()]
    entries: list[dict[str, Any]] = []
    category = ""
    code_line_nos = {i for i, ln in enumerate(lines) if "BL2(" in ln}
    category_lines: set[int] = set()

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if idx not in code_line_nos:
            if "====" in stripped or _HEADER_RE.match(stripped):
                header = stripped.split("====")[0].strip("=-*# 　")
                if header:
                    category = header[:40]
                category_lines.add(idx)
                continue
            # 分区标题判定：短行 + 后面连续至少两条"纯代码行"
            nxt = _next_nonempty(lines, idx + 1)
            nxt2 = _next_nonempty(lines, nxt + 1) if nxt >= 0 else -1
            bare = lambda i: i >= 0 and lines[i].strip().startswith("BL2(") and lines[i].strip().endswith(")")
            if len(stripped) <= 16 and bare(nxt) and bare(nxt2):
                category = stripped[:40]
                category_lines.add(idx)
                continue
            # 枪管/配件路径之类的提示，附到上一条代码上
            if entries and stripped.startswith(_HINT_PREFIXES):
                key = "hints" if stripped.startswith("GD_") else "note"
                old = entries[-1].get(key) or ""
                entries[-1][key] = (old + " " + stripped).strip() if old else stripped
            continue

        for code in itemcodes.extract_codes(stripped):
            pos = stripped.find(code)
            name = stripped[:pos].strip(" \t：:;；-—")
            tail = stripped[pos + len(code):].strip()
            note = ""
            m = _NOTE_RE.search(tail)
            if m:
                note = m.group(1)
            elif tail:
                note = tail

            if not name:
                # 名字在上一行的情况（黑武代码.txt 的排版）
                for k in range(idx - 1, max(-1, idx - 4), -1):
                    if k in category_lines or "BL2(" in lines[k]:
                        continue
                    if _looks_like_name(lines[k]):
                        name = lines[k].strip()
                        break
            if not name and category:
                count = sum(1 for e in entries if e["category"] == category) + 1
                name = f"{category} #{count}"

            entries.append(
                {
                    "name": name or "(未命名)",
                    "code": itemcodes.with_key_zero(code),
                    "original_code": code,
                    "note": note,
                    "category": category,
                    "source": path.name,
                }
            )
    return entries


def build_library(reference_dir: Optional[Path] = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """扫描参考资料目录，生成去重后的代码库。返回 (条目, 统计)。"""
    ref = Path(reference_dir) if reference_dir else REFERENCE_DIR
    stats = {"files": 0, "found": 0, "decoded": 0, "failed": 0, "duplicate": 0, "named": 0}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    if not ref.is_dir():
        return merged, stats

    for path in sorted(ref.glob("*.txt")):
        stats["files"] += 1
        for entry in parse_text_file(path):
            stats["found"] += 1
            code = entry["code"]
            if code in seen:
                stats["duplicate"] += 1
                continue
            seen.add(code)
            try:
                info = itemcodes.decode(code)
                entry["is_weapon"] = bool(info["is_weapon"])
                entry["level"] = int(info["level"][0])
                entry["parts"] = len([p for p in info["parts"] if p])
                stats["decoded"] += 1
            except Exception:
                entry["is_weapon"] = bool(itemcodes.code_to_bytes(code)[0] & 0x80)
                entry["level"] = 0
                entry["parts"] = 0
                stats["failed"] += 1
            if entry["name"] and entry["name"] != "(未命名)":
                stats["named"] += 1
            merged.append(entry)

    # 名字相同的条目加序号，方便按名字精确导入
    name_count: dict[str, int] = {}
    for entry in merged:
        base = entry["name"]
        name_count[base] = name_count.get(base, 0) + 1
        if name_count[base] > 1:
            entry["name"] = f"{base} #{name_count[base]}"
    return merged, stats
