"""从 IL 反演方块定义：TerrainType → 是否前景 / GroundType / 是否实心。

输入 research/dumps/il_terrain_defs.txt（InspectAssembly 的 il 输出）。
输出 research/definitions.json 和 docs/08_定义_方块与敌人.md 的方块部分。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(r"E:\BroforceMapEditor")
IL = ROOT / "research" / "dumps" / "il_terrain_defs.txt"
CATALOG = json.loads((ROOT / "research" / "catalog.json").read_text(encoding="utf-8"))
OUT_JSON = ROOT / "research" / "definitions.json"

lines = [line.strip() for line in IL.read_text(encoding="utf-8").splitlines()]


def method_body(name_part: str) -> list[str]:
    start = None
    for i, line in enumerate(lines):
        if line.startswith("METHOD ") and name_part in line:
            start = i + 1
            break
    if start is None:
        raise SystemExit(f"method not found: {name_part}")
    body = []
    for line in lines[start:]:
        if not line.startswith("IL_") and not line:
            body.append(line)
            continue
        if line.startswith("METHOD ") or line.startswith("ASSEMBLY"):
            break
        body.append(line)
    return [line for line in body if line]


INT_RE = re.compile(r"^ldc\.i4(?:\.s|\.m1|\.([0-8]))?\s*(.*)$")


def parse_int(text: str, rest: str):
    if text == "ldc.i4.m1":
        return -1
    if text.startswith("ldc.i4."):
        return int(text.split(".")[-1])
    if rest:
        return int(rest)
    return None


def instructions(body: list[str]):
    """[(offset_int, opcode, operand_text)]，标签行单独返回。"""
    result = []
    for line in body:
        match = re.match(r"^IL_([0-9A-Fa-f]{4})\s+(.*)$", line)
        if match:
            offset = int(match.group(1), 16)
            rest = match.group(2).strip()
            result.append((offset, rest))
    return result


def parse_switch(operand: str) -> list[int]:
    return [int(x, 16) for x in re.findall(r"IL_([0-9A-Fa-f]{4})", operand)]


# ---------- 1. IsForegroundType：switch 表，返回 true/false ----------
body = method_body("MapData::IsForegroundType")
text = "\n".join(body)
switch_match = re.search(r"switch\s+(.+)", text)
targets = parse_switch(switch_match.group(1))
# 目标块：IL_0163 -> ldc.i4.0 ret ; IL_0165/IL_0167 -> ldc.i4.1 ret
target_value = {}
for i, line in enumerate(body):
    m = re.match(r"^IL_([0-9A-Fa-f]{4})\s+(ldc\.i4[^\s]*)\s*$", line)
    if m and i + 1 < len(body) and re.match(r"^IL_[0-9A-Fa-f]{4}\s+ret$", body[i + 1].strip()):
        key = int(m.group(1), 16)
        value = parse_int(m.group(2), "")
        target_value[key] = value

foreground = {}
for terrain_index, target in enumerate(targets):
    if target in target_value:
        foreground[terrain_index] = bool(target_value[target])

# ---------- 2. GetGroundType：switch 表 → 每个分支 ldc.i4 X; ret ----------
body = method_body("MapData::GetGroundType")
label_to_value = {}
for i, line in enumerate(body):
    m = re.match(r"^IL_([0-9A-Fa-f]{4})\s+(ldc\.i4(?:\.s)?)\s+(-?\d+)\s*$", line)
    if m and i + 1 < len(body) and re.match(r"^IL_[0-9A-Fa-f]{4}\s+ret$", body[i + 1].strip()):
        label_to_value[int(m.group(1), 16)] = int(m.group(3))
switch_match = re.search(r"switch\s+(.+)", "\n".join(body))
gt_targets = parse_switch(switch_match.group(1))
terrain_to_ground = {}
for terrain_index, target in enumerate(gt_targets):
    if target in label_to_value:
        terrain_to_ground[terrain_index] = label_to_value[target]

# ---------- 3. IsGroundTypeSolid：小型 IL 解释器 ----------
body = method_body("Map::IsGroundTypeSolid")
code = instructions(body)
by_offset = {offset: idx for idx, (offset, _) in enumerate(code)}
labels = {}
for i, (offset, text) in enumerate(code):
    for target in re.findall(r"IL_([0-9A-Fa-f]{4})", text):
        labels.setdefault(int(target, 16), None)


def run(arg: int) -> bool | None:
    stack: list[int] = []
    pc = 0
    steps = 0
    while pc < len(code) and steps < 2000:
        steps += 1
        offset, text = code[pc]
        op = text.split()[0]
        if text.startswith("ldarg.0"):
            stack.append(arg)
            pc += 1
        elif text.startswith("ldc.i4"):
            parts = text.split()
            if parts[0] == "ldc.i4.s":
                stack.append(int(parts[1]))
            elif parts[0] == "ldc.i4":
                stack.append(int(parts[1]))
            else:
                stack.append(int(parts[0].split(".")[-1]))
            pc += 1
        elif op == "sub":
            b = stack.pop(); a = stack.pop(); stack.append(a - b); pc += 1
        elif op == "add":
            b = stack.pop(); a = stack.pop(); stack.append(a + b); pc += 1
        elif op == "switch":
            value = stack.pop()
            targets = parse_switch(" ".join(text.split()[1:]))
            if 0 <= value < len(targets):
                pc = by_offset.get(targets[value])
                if pc is None:
                    return None
            else:
                pc += 1
        elif text.startswith("beq"):
            b = stack.pop(); a = stack.pop()
            target = int(re.search(r"IL_([0-9A-Fa-f]{4})", text).group(1), 16)
            pc = by_offset[target] if a == b else pc + 1
        elif text.startswith("bne.un"):
            b = stack.pop(); a = stack.pop()
            target = int(re.search(r"IL_([0-9A-Fa-f]{4})", text).group(1), 16)
            pc = by_offset[target] if a != b else pc + 1
        elif text.startswith("blt"):
            b = stack.pop(); a = stack.pop()
            target = int(re.search(r"IL_([0-9A-Fa-f]{4})", text).group(1), 16)
            pc = by_offset[target] if a < b else pc + 1
        elif text.startswith("bge"):
            b = stack.pop(); a = stack.pop()
            target = int(re.search(r"IL_([0-9A-Fa-f]{4})", text).group(1), 16)
            pc = by_offset[target] if a >= b else pc + 1
        elif text.startswith("bgt"):
            b = stack.pop(); a = stack.pop()
            target = int(re.search(r"IL_([0-9A-Fa-f]{4})", text).group(1), 16)
            pc = by_offset[target] if a > b else pc + 1
        elif text.startswith("brtrue"):
            value = stack.pop()
            target = int(re.search(r"IL_([0-9A-Fa-f]{4})", text).group(1), 16)
            pc = by_offset[target] if value else pc + 1
        elif text.startswith("brfalse"):
            value = stack.pop()
            target = int(re.search(r"IL_([0-9A-Fa-f]{4})", text).group(1), 16)
            pc = by_offset[target] if not value else pc + 1
        elif text.startswith("br"):
            target = int(re.search(r"IL_([0-9A-Fa-f]{4})", text).group(1), 16)
            pc = by_offset[target]
        elif op == "ret":
            return bool(stack.pop()) if stack else None
        else:
            return None
    return None


ground_solid = {}
for ground_value in range(0, 130):
    result = run(ground_value)
    if result is not None:
        ground_solid[ground_value] = result

# ---------- 汇总 ----------
terrain_names = {v: k for k, v in CATALOG["TerrainType"].items()}
ground_names = {v: k for k, v in CATALOG["GroundType"].items()}
table = []
for index in sorted(terrain_names):
    ground = terrain_to_ground.get(index)
    table.append({
        "value": index,
        "name": terrain_names[index],
        "foreground": foreground.get(index),
        "groundType": ground,
        "groundName": ground_names.get(ground) if ground is not None else None,
        "solid": ground_solid.get(ground) if ground is not None else None,
    })

OUT_JSON.write_text(json.dumps({
    "terrain": table,
    "groundSolid": {str(k): v for k, v in sorted(ground_solid.items())},
}, ensure_ascii=False, indent=1), encoding="utf-8")

print("terrain entries:", len(table))
print("foreground known:", sum(1 for r in table if r["foreground"] is not None))
print("ground mapped:", sum(1 for r in table if r["groundType"] is not None))
print("solid known:", sum(1 for r in table if r["solid"] is not None))
print("solid count:", sum(1 for r in table if r["solid"]))
for row in table[:12]:
    print(row)
