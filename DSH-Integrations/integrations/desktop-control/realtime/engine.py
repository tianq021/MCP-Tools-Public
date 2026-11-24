"""Small, explicit visual rules; no game memory access or model/network per frame."""
from dataclasses import dataclass
from pathlib import Path
import re

import cv2
import numpy as np
from actions import KEYS, validate_steps

ROOT = Path(__file__).resolve().parent
PROFILES = ROOT / "profiles"
CAPTURES = ROOT / "captures"
LOGS = ROOT / "logs"


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,59}", value):
        raise ValueError("名称必须是小写字母开头的英文、数字、下划线或短横线。")
    return value


def image_read(path):
    frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("无法读取模板或截图。")
    return frame


def image_write(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("截图编码失败。")
    encoded.tofile(path)


def integer(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError("整数参数超出范围。")
    return value


def box(value, width, height):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("区域须为 [left,top,right,bottom] 像素坐标。")
    l, t, r, b = value
    for number, limit in zip(value, (width, height, width, height)):
        integer(number, 0, limit)
    if r <= l or b <= t:
        raise ValueError("区域尺寸无效。")
    return l, t, r, b


@dataclass
class Rule:
    name: str
    template: np.ndarray
    region: tuple
    threshold: float
    stable_frames: int
    action: dict
    cooldown_s: float
    streak: int = 0
    absent: int = 0
    armed: bool = True
    last_action: float = -1e10
    first_seen: float | None = None

    def match(self, frame):
        l, t, r, b = self.region
        area = cv2.cvtColor(frame[t:b, l:r], cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(area, self.template, cv2.TM_CCOEFF_NORMED)
        _, score, _, pos = cv2.minMaxLoc(result)
        return float(score), (l + pos[0] + self.template.shape[1] // 2,
                              t + pos[1] + self.template.shape[0] // 2)

    def update(self, score, now):
        if score >= self.threshold:
            if self.first_seen is None:
                self.first_seen = now
            self.streak += 1
            self.absent = 0
        else:
            self.streak = 0
            self.first_seen = None
            self.absent += 1
            if self.absent >= 3:
                self.armed = True
        return self.armed and self.streak >= self.stable_frames and now - self.last_action >= self.cooldown_s

    def fired(self, now):
        self.last_action = now
        self.armed = False


def load_rules(profile, root=None):
    import json
    folder = (root or PROFILES) / identifier(profile)
    config = json.loads((folder / "profile.json").read_text(encoding="utf-8-sig"))
    width, height = config["client_size"]
    integer(width, 100, 10000)
    integer(height, 100, 10000)
    rules = []
    if not 1 <= len(config["rules"]) <= 8:
        raise ValueError("每个配置须有 1 到 8 条规则。")
    for row in config["rules"]:
        name = identifier(row["name"])
        template = image_read(folder / (name + ".png"))
        gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        region = box(row["region"], width, height)
        if gray.std() < 8 or gray.shape[0] < 8 or gray.shape[1] < 8:
            raise ValueError("模板需要足够的文字或纹理。")
        if gray.shape[1] > region[2] - region[0] or gray.shape[0] > region[3] - region[1]:
            raise ValueError("模板尺寸超过搜索区域。")
        threshold = float(row.get("threshold", 0.93))
        if not 0.85 <= threshold <= 1:
            raise ValueError("匹配阈值须在 0.85 到 1 之间。")
        action = row["action"]
        if action.get("type") not in ("click_match", "click", "key", "notify", "sequence"):
            raise ValueError("不支持的规则动作。")
        if action["type"] == "click":
            integer(action["x"], 0, width - 1)
            integer(action["y"], 0, height - 1)
        if action["type"] == "key" and action.get("key") not in KEYS:
            raise ValueError("不支持的规则按键。")
        if action["type"] == "sequence":
            validate_steps(action.get("steps"), width, height)
        cooldown = float(row.get("cooldown_s", 1))
        if not 0.25 <= cooldown <= 60:
            raise ValueError("冷却时间须在 0.25 到 60 秒之间。")
        rules.append(Rule(name, gray, region, threshold,
                          integer(row.get("stable_frames", 2), 2, 10), action, cooldown))
    return config, rules


def summary(samples):
    if not samples:
        return None
    return {"p50_ms": round(float(np.percentile(samples, 50)), 3),
            "p95_ms": round(float(np.percentile(samples, 95)), 3), "samples": len(samples)}
