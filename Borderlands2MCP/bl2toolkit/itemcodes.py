"""BL2 物品/武器代码（BL2(...)）的编解码。

代码格式：`BL2(` + base64(Gibbed 物品序列) + `)`。
物品序列内部按位打包了：类型/平衡/厂商 头部 + 等级 + 若干配件(part)。
本模块只负责"代码 <-> 结构化字段"的转换；配件的人类可读名称需要 data/ 里的配件表。
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Iterable, Optional

from borderlands.bl2 import AppBL2

PREFIX = "BL2"
CODE_RE = re.compile(r"BL2\(([A-Za-z0-9+/=]+)\)")

# 全局共享一个 AppBL2 实例（构造时会跑一遍 argparse，略慢，缓存起来）
_APP: Optional[AppBL2] = None


def get_app() -> AppBL2:
    global _APP
    if _APP is None:
        _APP = AppBL2(["-"])  # 只解析默认参数，不做任何修改
    return _APP


def is_code(text: str) -> bool:
    return bool(CODE_RE.fullmatch((text or "").strip()))


def extract_codes(text: str) -> list[str]:
    """从任意文本里抽出所有 BL2(...) 代码。"""
    return ["BL2(" + m + ")" for m in CODE_RE.findall(text or "")]


def code_to_bytes(code: str) -> bytes:
    m = CODE_RE.fullmatch((code or "").strip())
    if not m:
        raise ValueError(f"不是合法的 BL2 代码: {code[:40]!r}")
    return base64.b64decode(m.group(1))


def bytes_to_code(raw: bytes) -> str:
    return f"{PREFIX}({base64.b64encode(raw).decode('ascii')})"


def decode(code: str, *, with_raw: bool = False) -> dict[str, Any]:
    """把 BL2 代码解码成结构化字典。

    返回字段：
      is_weapon: 1=武器, 0=物品(护盾/手雷/模组/圣物等)
      key: 该物品的随机序列号
      set: 等级档位（通常 0）
      level: [grade_index, game_stage]
      type / balance / manufacturer: {lib, asset}
      parts: 配件列表 [{lib, asset} | None]
      code: 规范化的代码字符串
    """
    app = get_app()
    raw = code_to_bytes(code)
    info = app.unwrap_item_info(raw)
    out = dict(info)
    out.pop("_base64", None)
    out["code"] = bytes_to_code(raw)
    if with_raw:
        out["raw_hex"] = raw.hex()
    return out


def encode(info: dict[str, Any]) -> str:
    """把结构化字典重新编码成 BL2 代码（key 保持原值）。"""
    app = get_app()
    payload = {
        "is_weapon": int(info["is_weapon"]),
        "key": int(info["key"]),
        "set": int(info.get("set", 0)),
        "level": list(info["level"]),
        "type": dict(info["type"]),
        "balance": dict(info["balance"]),
        "manufacturer": dict(info["manufacturer"]),
        "parts": [None if p is None else dict(p) for p in info["parts"]],
    }
    return bytes_to_code(app.wrap_item_info(payload))


def with_key_zero(code: str) -> str:
    """把代码里的 key 归零 —— 用于去重/比较（导出时的规范形式）。"""
    from borderlands.datautil.common import replace_raw_item_key

    raw = code_to_bytes(code)
    return bytes_to_code(replace_raw_item_key(raw, 0))


def make_code(
    *,
    template: str,
    parts: Optional[Iterable[Optional[dict]]] = None,
    level: Optional[Iterable[int]] = None,
    type_: Optional[dict] = None,
    balance: Optional[dict] = None,
    manufacturer: Optional[dict] = None,
    key: int = 0,
) -> str:
    """以一条已有代码为模板，替换配件/等级/头部字段，生成新代码。

    这是"黑枪"最基础的玩法：拿一把合法枪的代码当骨架，把配件换成别的，
    例如给霰弹枪换上高射速枪管、给护盾叠加高容量材质。
    """
    info = decode(template)
    if parts is not None:
        parts = list(parts)
        if len(parts) > len(info["parts"]):
            raise ValueError(f"配件太多：模板只有 {len(info['parts'])} 个槽位，给了 {len(parts)} 个")
        info["parts"] = parts + [None] * (len(info["parts"]) - len(parts))
    if level is not None:
        info["level"] = list(level)
    if type_ is not None:
        info["type"] = dict(type_)
    if balance is not None:
        info["balance"] = dict(balance)
    if manufacturer is not None:
        info["manufacturer"] = dict(manufacturer)
    info["key"] = key
    return encode(info)


def describe(code: str) -> str:
    """给一条代码生成简短的人类可读描述（配件用 lib:asset 表示）。"""

    def fmt(part: Optional[dict]) -> str:
        if part is None:
            return "-"
        return f"{part['lib']}:{part['asset']}"

    info = decode(code)
    kind = "武器" if info["is_weapon"] else "物品"
    parts = ", ".join(fmt(p) for p in info["parts"])
    return (
        f"{kind} level={info['level']} type={fmt(info['type'])} "
        f"balance={fmt(info['balance'])} manufacturer={fmt(info['manufacturer'])} "
        f"parts=[{parts}]"
    )


#: BL2 物品序列的数据版本（本机存档/工具都是 7）
EXPECTED_ITEM_VERSION = 7


def assess_risk(code: str, *, op_level: Optional[int] = None, char_level: Optional[int] = None) -> dict:
    """对一条物品代码做"可能被游戏判为非法/被删除"的结构性检查。

    只是静态检查（不启动游戏），不代表游戏一定接受或一定删除。

    注意：BL2 原版人物等级上限是 80，但 UVHM 有 Overpower（OP）1-10 级，
    在已解锁 OP 时物品等级 81-90（=80+OP1~OP10）是**合法**的，不能仅因 >80 就判非法。
    提供 op_level 时按 80+OP 判断；不提供时把 81-90 标为"需要 OP 等级"而不是非法。

    - 数据版本不是 7
    - 等级 0 / 超过 80+OP 可解释范围
    - 关键部件（type/balance/manufacturer）缺失
    - 武器配件数量异常少
    """
    raw = code_to_bytes(code)
    info = decode(code)
    warnings: list[str] = []
    notes: list[str] = []
    version = raw[0] & 0x7F
    if version != EXPECTED_ITEM_VERSION:
        warnings.append(f"物品数据版本 {version}（BL2 期望 {EXPECTED_ITEM_VERSION}），可能被判定非法")
    level = int(info["level"][0]) if info.get("level") else 0
    op_legal: Optional[bool] = None
    if level == 0:
        warnings.append("等级为 0：游戏里名字/红字可能缺失")
    elif level <= 80:
        op_legal = True
        if char_level is not None and level > char_level + 10:
            notes.append(f"等级 {level} 明显高于角色等级 {char_level}（合法但可能是改出来的）")
    elif level <= 90:
        need_op = level - 80
        if op_level is None:
            op_legal = None
            notes.append(
                f"等级 {level} = 80+OP{need_op}：如果存档已解锁 OP{need_op} 则属于**合法 OP 物品**，"
                f"不是非法；无法确认 OP 等级时不要当非法处理"
            )
        elif op_level >= need_op:
            op_legal = True
            notes.append(f"等级 {level} = 80+OP{need_op}，当前 OP{op_level} 已覆盖，合法范围")
        else:
            op_legal = False
            warnings.append(
                f"等级 {level} 需要 OP{need_op}，当前存档 OP{op_level} 不足：可能被修正/删除"
            )
    else:
        op_legal = False
        warnings.append(f"等级 {level} 超过原版 80+OP10（90）上限：风险高")
    for field in ("type", "balance", "manufacturer"):
        part = info.get(field) or {"lib": 0, "asset": 0}
        if not part.get("lib") and not part.get("asset"):
            warnings.append(f"{field} 缺失：物品可能直接消失")
    parts = [p for p in info.get("parts") or [] if p]
    if info["is_weapon"] and len(parts) < 3:
        warnings.append(f"武器配件只有 {len(parts)} 个，可能不完整")
    return {
        "code": code,
        "is_weapon": bool(info["is_weapon"]),
        "level": level,
        "parts": len(parts),
        "version": version,
        "op_level": op_level,
        "char_level": char_level,
        "op_legal": op_legal,
        "risky": bool(warnings),
        "warnings": warnings,
        "notes": notes,
    }
