"""Shared validation for model-planned input and local visual reactions."""
import math

KEYS = {"space": 0x20, "enter": 0x0D, "tab": 9, "backspace": 8,
        "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
        "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        "insert": 0x2D, "delete": 0x2E}
KEYS.update({chr(vk).lower(): vk for vk in range(0x41, 0x5B)})
KEYS.update({str(n): 0x30 + n for n in range(10)})
KEYS.update({"f" + str(n): 0x6F + n for n in range(1, 13)})
BUTTONS = {"left": (1, 2, 4), "right": (2, 8, 16), "middle": (4, 32, 64)}


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("数值参数超出范围。")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("整数参数超出范围。")
    return value


def key_names(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 6:
        raise ValueError("keys 必须包含 1 到 6 个键名。")
    if any(not isinstance(name, str) or name not in KEYS for name in value) or len(set(value)) != len(value):
        raise ValueError("未知或重复的键名；Esc 保留为用户急停。")
    # Avoid leaving a modifier in the other application after a focus switch.
    return value


def validate_steps(rows, width=None, height=None):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("steps 必须包含 1 到 100 个动作。")
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("每个动作必须是对象。")
        kind = row.get("type")
        if kind not in ("wait", "key", "key_down", "key_up", "move", "relative_move",
                        "click", "button_down", "button_up", "scroll", "drag"):
            raise ValueError("未知的连续动作类型。")
        if kind in ("key", "key_down", "key_up"):
            key_names(row.get("keys"))
        if kind in ("click", "button_down", "button_up", "drag"):
            if row.get("button", "left") not in BUTTONS:
                raise ValueError("未知的鼠标按钮。")
        if kind in ("move", "click", "drag"):
            for axis, limit in (("x", width), ("y", height)):
                integer(row.get(axis), 0, (limit - 1) if limit else 9999)
            if kind == "drag":
                for axis, limit in (("to_x", width), ("to_y", height)):
                    integer(row.get(axis), 0, (limit - 1) if limit else 9999)
        if kind == "relative_move":
            integer(row.get("dx"), -5000, 5000)
            integer(row.get("dy"), -5000, 5000)
        if kind == "scroll":
            integer(row.get("amount"), -10, 10)
            if row["amount"] == 0:
                raise ValueError("滚动量不能为零。")
        if kind in ("key", "click", "wait", "drag", "relative_move"):
            default = 0.035 if kind in ("key", "click") else 0.2 if kind == "drag" else 0
            duration = number(row.get("duration_s", default), 0, 20)
            if kind in ("key", "click") and duration < 0.016:
                raise ValueError("按键和点击至少保持 0.016 秒。")
            if kind == "drag" and duration < 0.05:
                raise ValueError("拖拽至少持续 0.05 秒。")
            total += duration
    if total > 20:
        raise ValueError("连续动作总时长不能超过 20 秒。")
    return total


def execute_steps(guard, rows):
    for row in rows:
        guard.check()
        kind = row["type"]
        if kind == "wait":
            guard.wait(row.get("duration_s", 0))
        elif kind == "key":
            guard.press_keys(row["keys"], row.get("duration_s", 0.035))
        elif kind in ("key_down", "key_up"):
            getattr(guard, kind)(row["keys"])
        elif kind == "move":
            guard.move(row["x"], row["y"])
        elif kind == "relative_move":
            guard.relative_move(row["dx"], row["dy"], row.get("duration_s", 0))
        elif kind == "click":
            guard.click(row["x"], row["y"], row.get("button", "left"), row.get("duration_s", 0.035))
        elif kind in ("button_down", "button_up"):
            getattr(guard, kind)(row.get("button", "left"))
        elif kind == "scroll":
            guard.scroll(row["amount"])
        elif kind == "drag":
            guard.drag(row["x"], row["y"], row["to_x"], row["to_y"],
                       row.get("duration_s", 0.2), row.get("button", "left"))
