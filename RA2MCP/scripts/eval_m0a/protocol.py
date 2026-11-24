"""M0-A 固定评测协议（2026-11-01 新建）。

目标：为历史模型提供**可重复、同条件、无脚本代做战略**的完整对局评测。
本模块只包含常量/配置解析/校验函数，不启动游戏、不修改训练或奖励。

协议要点（全部写入 evaluation.json 以便审计）：
- 地图：2peaks（spawnmap.ini，SHA256 DD64CB8B...）
- 对手：内置 AI，1v1（AIPlayers=1），AIDifficulty=2（Easy 语义见 spawn.ini 注释）
- 阵营：人类 Americans / AI Russians（spawn.ini 固定）
- 种子：spawn.ini `Seed=20261028`（固定；游戏 RNG 未单独播种的局限见报告）
- 资金：人类 20000 / AI 100000（legacy spawner 固定 AI=5x，已知条件，报告标注）
- 规则：ShortGame=Yes、Superweapons=No、FogOfWar=No、GameSpeed=1、TechLevel=10
- 决策：0.5s/步，确定性策略（argmax），冻结模型（不更新）
- 时长：300s 上限（timeout 计为有效评测结局，单独统计）
- 环境：RA2FullEnv（模型控制经济/建造/生产/单位；`_economy()` 被覆写为 no-op，
  无 RulePolicy 代做战略）
"""
import hashlib
import json
import pathlib

PROTOCOL_VERSION = "m0a-freeze-1"
EXPERIMENT_TAG = "m0a-20261101"

ROOT = pathlib.Path(__file__).resolve().parents[2]

# 固定对局配置与地图（评测前校验哈希；不一致则拒绝启动）
EXPECTED_SPAWN_INI = ROOT / "config" / "spawn.ini"
EXPECTED_MAP = ROOT / "experiments" / "build_training_v2" / "map" / "base_2peaks.map"
EXPECTED_SPAWN_SHA256 = "2f15801c51c0952c2c0d1da09810589d619708db55724e882d24975ec2172a39"
EXPECTED_MAP_SHA256 = "dd64cb8b10d44e489bd21cf62914af4706386f9ee161bf53875fef186c571e6d"

DECISION_SECONDS = 0.5
EPISODE_SECONDS = 300.0

MODEL_DECISION_SOURCES = ("ppo", "model", "rl", "policy")

# 评测工作机使用现有 test-game* 副本；每份副本的桥接端口来自其 ra2yrcpp.json。
# 禁止修改副本配置；只读取并校验 spawn.ini / spawnmap.ini 哈希。
_CANDIDATE_DIRS = ["test-game"] + [f"test-game-{i}" for i in range(1, 16)] + ["test-game-build"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_port(game_dir):
    cfg = pathlib.Path(game_dir) / "ra2yrcpp.json"
    if not cfg.exists():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))
        port = int(data.get("port"))
        if 1 <= port <= 65535:
            return port
    except Exception:  # noqa: BLE001
        return None
    return None


def resolve_game_slots(workers):
    """返回最多 workers 个可用评测槽位：dict(name, game_dir, port, spawn_sha, map_sha)。

    只接受 spawn.ini/spawnmap.ini 与协议常量一致的副本（拒绝配置漂移）。
    端口去重，按候选顺序稳定排列。
    """
    slots, used_ports = [], set()
    for name in _CANDIDATE_DIRS:
        game_dir = ROOT / name
        spawn = game_dir / "spawn.ini"
        spawnmap = game_dir / "spawnmap.ini"
        port = _read_port(game_dir)
        if port is None or port in used_ports:
            continue
        if not spawn.exists() or not spawnmap.exists():
            continue
        spawn_sha = sha256_file(spawn)
        map_sha = sha256_file(spawnmap)
        if spawn_sha != EXPECTED_SPAWN_SHA256 or map_sha != EXPECTED_MAP_SHA256:
            continue
        if not (game_dir / "gamemd-spawn-ra2yrcpp.exe").exists():
            continue
        slots.append({"name": name, "game_dir": str(game_dir), "port": port,
                      "spawn_ini_sha256": spawn_sha, "spawnmap_sha256": map_sha})
        used_ports.add(port)
        if len(slots) >= workers:
            break
    return slots


def protocol_manifest():
    spawn_sha = sha256_file(EXPECTED_SPAWN_INI)
    map_sha = sha256_file(EXPECTED_MAP)
    compliant = spawn_sha == EXPECTED_SPAWN_SHA256 and map_sha == EXPECTED_MAP_SHA256
    return {
        "protocol_version": PROTOCOL_VERSION,
        "experiment_tag": EXPERIMENT_TAG,
        "decision_seconds": DECISION_SECONDS,
        "episode_seconds": EPISODE_SECONDS,
        "deterministic_policy": True,
        "env": "RA2FullEnv (model-controlled economy, _economy() no-op, no RulePolicy)",
        "map": "2peaks (spawnmap.ini)",
        "opponent": "builtin AI 1v1 (AIDifficulty=2; legacy spawner AI=5x credits)",
        "factions": "human Americans / AI Russians (spawn.ini)",
        "seed": "spawn.ini Seed=20261028 (fixed)",
        "spawn_ini": {"path": str(EXPECTED_SPAWN_INI.relative_to(ROOT)),
                      "sha256": spawn_sha},
        "spawnmap": {"path": str(EXPECTED_MAP.relative_to(ROOT)), "sha256": map_sha},
        "config_compliant": compliant,
        "known_conditions": [
            "AI starts with 5x human credits (legacy CnCNet5 spawner ignores [CreditsFactor])",
            "FogOfWar=No (only shroud), Superweapons=No, ShortGame=Yes",
            "300s episode cap; timeout is a valid evaluation outcome and reported separately",
            "game RNG is not separately seeded; same spawn seed still allows AI/timing variation",
        ],
        "model_decision_sources": list(MODEL_DECISION_SOURCES),
        "script_policy_forbidden": True,
    }
