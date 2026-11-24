"""扫描参考资料目录，生成 data/code_library.json（本地黑枪/特殊装备代码库）。

用法：python scripts\build_code_library.py [参考资料目录]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit.library import REFERENCE_DIR, build_library, save_library  # noqa: E402


def main() -> int:
    ref = Path(sys.argv[1]) if len(sys.argv) > 1 else REFERENCE_DIR
    print(f"扫描目录: {ref}")
    entries, stats = build_library(ref)
    out = save_library(entries)
    print(f"扫描文件 {stats['files']} 个，发现代码 {stats['found']} 条，"
          f"成功解码 {stats['decoded']} 条，失败 {stats['failed']} 条，去重 {stats['duplicate']} 条")
    print(f"命名条目 {stats['named']} 条，共 {len(entries)} 条写入 {out}")
    weapons = sum(1 for e in entries if e.get("is_weapon"))
    print(f"武器 {weapons} 条 / 其它装备 {len(entries) - weapons} 条")
    print("\n示例：")
    for e in entries[:8]:
        kind = "武器" if e.get("is_weapon") else "物品"
        print(f"  [{kind}] {e['name']}  Lv{e.get('level', 0)}  {e.get('note', '')[:24]}  ({e['source']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
