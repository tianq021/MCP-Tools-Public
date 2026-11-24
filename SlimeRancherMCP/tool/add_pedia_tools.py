"""把 sr_pedia_unlock / sr_give_item 两个工具定义加进 SR1 的 mcp/tools.json（幂等）。"""
import json
import os

PATH = r"E:\SlimeRancherMCP\mcp\tools.json"

NEW = [
    {
        "name": "sr_pedia_unlock",
        "title": "补全史莱姆百科",
        "description": "解锁史莱姆百科条目（成就「专业范儿」用）。pedia_id 留空 = 解锁所有未解锁的非隐藏条目；也可传逗号分隔的条目 ID。走游戏自己的 PediaDirector.Unlock()，会正常触发 COMPLETED_SLIMEPEDIA 统计。默认预演，执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {
                "name": "pedia_id",
                "type": "str",
                "description": "留空=全部未解锁的非隐藏条目；或逗号分隔，如 BRIAR_CHICKADOO,GINGER。",
                "default": "",
                "max_length": 500,
            }
        ],
    },
    {
        "name": "sr_give_item",
        "title": "发放物品到背包",
        "description": "把指定物品直接加进真空背包（走原生 Ammo.MaybeAddToSpecificSlot）。用于测试与 Rush 模式批量产结晶。默认预演，执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {
                "name": "item_id",
                "type": "str",
                "description": "精确物品 ID，如 GOLD_PLORT、PINK_PLORT。",
                "required": True,
                "min_length": 1,
                "max_length": 64,
            },
            {
                "name": "count",
                "type": "int",
                "description": "数量，1-999（受背包格子容量限制，返回值里有 added/remaining）。",
                "default": 1,
                "ge": 1,
                "le": 999,
            },
        ],
    },
]


def main() -> int:
    spec = json.load(open(PATH, "r", encoding="utf-8"))
    names = {t["name"] for t in spec["tools"]}
    added = []
    for tool in NEW:
        if tool["name"] in names:
            continue
        spec["tools"].append(tool)
        added.append(tool["name"])
    if added:
        with open(PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(spec, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print("added:", added or "none")
    print("total tools:", len(spec["tools"]))
    writes = sum(1 for t in spec["tools"] if t.get("kind") == "write")
    print(f"read={len(spec['tools']) - writes} write={writes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
