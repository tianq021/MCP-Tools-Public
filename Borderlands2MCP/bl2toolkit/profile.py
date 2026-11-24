"""profile.bin 读写（Badass/金钥匙/服装/Claptrap 共享储藏箱）。

格式（已用本机 profile.bin 实测解析 + 与成熟工具 B2Profile 的格式定义交叉核对）：

```
SHA1(20 字节) | 未压缩长度(uint32 大端) | MiniLZO 压缩流
```

解压后：

```
uint32 大端 条目数
重复 条目数 次：
    StartByte(1) | ID(uint32 大端) | DataType(1) | 值 | EndByte(1)
```

DataType: 1=Int32(大端) / 4=String(uint32 大端长度 + ASCII) / 5=Single / 6=Binary(uint32 大端长度 + 原始字节) / 8=Int8。

和本任务直接相关的条目 ID（来自 B2Profile 的字段定义，实测本机 profile.bin 对得上）：

| ID | 含义 | 类型 |
| --- | --- | --- |
| 129 | FOV | Int32 |
| 130-133 | **Claptrap 共享储藏箱 4 个格子** | Binary（物品序列原始字节，等同 BL2(...) 里 base64 解码后的内容） |
| 136/137 | Badass Rank（两段相加后 /10） | Int32 |
| 138 | 可用 Badass Token | Int32 |
| 139 | 累计 Badass Token | Int32 |
| 162 | 金钥匙（每 3 字节：来源/总数/已用） | Binary |
| 300 | 服装/皮肤解锁位图 | Binary |

空储藏箱格子 = 40 个 0x00（与 B2Profile 的删除实现一致）。
"""

from __future__ import annotations

import base64
import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from borderlands.datautil.lzo1x import lzo1x_1_compress, lzo1x_decompress

from . import itemcodes
from .paths import backup_save, is_game_running, write_bytes_atomic

PROFILE_NAME = "profile.bin"

#: 条目 ID -> 名称
ENTRY_NAMES: dict[int, str] = {
    27: "NumProfileSaved",
    129: "FOV",
    130: "ClaptrapsStashSlot1",
    131: "ClaptrapsStashSlot2",
    132: "ClaptrapsStashSlot3",
    133: "ClaptrapsStashSlot4",
    136: "BadassRank1",
    137: "BadassRank2",
    138: "BadassTokensAvailable",
    139: "BadassTokensEarned",
    143: "BonusStats",
    162: "GoldenKeys",
    164: "NextBonusStats",
    300: "Customizations",
}
STASH_ENTRY_IDS = (130, 131, 132, 133)
EMPTY_STASH_BYTES = b"\x00" * 40

INT32, STRING, SINGLE, BINARY, INT8 = 1, 4, 5, 6, 8


@dataclass
class Entry:
    start_byte: int
    id: int
    dtype: int
    value: Any
    end_byte: int
    raw_value: bytes = b""

    @property
    def name(self) -> str:
        return ENTRY_NAMES.get(self.id, "")


class ProfileFile:
    """一份 profile.bin。

    典型用法::

        pf = ProfileFile("path/to/profile.bin")
        pf.stash_codes()          # -> 4 个 BL2(...) 代码（空格子为 None）
        pf.set_stash_slot(1, code)
        pf.save("path/to/copy.bin")
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"profile 不存在: {self.path}")
        self.raw = self.path.read_bytes()
        self.entries: list[Entry] = []
        self._load()

    # -- 解析 / 序列化 -------------------------------------------------

    def _load(self) -> None:
        if len(self.raw) < 24:
            raise ValueError("profile.bin 太短")
        stored_sha1 = self.raw[:20]
        if hashlib.sha1(self.raw[20:]).digest() != stored_sha1:
            raise ValueError("profile.bin 的 SHA1 校验失败（文件损坏或不是 BL2 profile）")
        uncompressed_size = struct.unpack(">I", self.raw[20:24])[0]
        payload = lzo1x_decompress(b"\xf0" + self.raw[20:])
        if len(payload) != uncompressed_size:
            raise ValueError(f"解压长度不符: {len(payload)} != {uncompressed_size}")
        self.payload = payload
        self._parse_entries(payload)

    def _parse_entries(self, payload: bytes) -> None:
        n = struct.unpack(">I", payload[:4])[0]
        off = 4
        entries: list[Entry] = []
        for i in range(n):
            if off + 6 > len(payload):
                raise ValueError(f"第 {i} 个条目越界")
            start_byte = payload[off]
            off += 1
            entry_id = struct.unpack(">I", payload[off:off + 4])[0]
            off += 4
            dtype = payload[off]
            off += 1
            raw_value = b""
            if dtype == INT32:
                value = struct.unpack(">i", payload[off:off + 4])[0]
                raw_value = payload[off:off + 4]
                off += 4
            elif dtype == STRING:
                length = struct.unpack(">I", payload[off:off + 4])[0]
                off += 4
                raw_value = payload[off:off + length]
                value = raw_value.decode("latin1")
                off += length
            elif dtype == SINGLE:
                value = struct.unpack(">f", payload[off:off + 4])[0]
                raw_value = payload[off:off + 4]
                off += 4
            elif dtype == BINARY:
                length = struct.unpack(">I", payload[off:off + 4])[0]
                off += 4
                raw_value = payload[off:off + length]
                value = raw_value
                off += length
            elif dtype == INT8:
                value = payload[off]
                raw_value = payload[off:off + 1]
                off += 1
            else:
                raise ValueError(f"未知 DataType {dtype}（第 {i} 个条目）")
            end_byte = payload[off]
            off += 1
            entries.append(
                Entry(
                    start_byte=start_byte,
                    id=entry_id,
                    dtype=dtype,
                    value=value,
                    end_byte=end_byte,
                    raw_value=raw_value,
                )
            )
        if off != len(payload):
            raise ValueError(f"解析后仍有 {len(payload) - off} 字节未消费，格式不符")
        self.entries = entries

    def _serialize_payload(self) -> bytes:
        out = bytearray(struct.pack(">I", len(self.entries)))
        for e in self.entries:
            out.append(e.start_byte & 0xFF)
            out += struct.pack(">I", e.id)
            out.append(e.dtype & 0xFF)
            if e.dtype == INT32:
                out += struct.pack(">i", int(e.value))
            elif e.dtype == STRING:
                data = e.value.encode("latin1") if isinstance(e.value, str) else bytes(e.value)
                out += struct.pack(">I", len(data)) + data
            elif e.dtype == SINGLE:
                out += struct.pack(">f", float(e.value))
            elif e.dtype == BINARY:
                data = bytes(e.value)
                out += struct.pack(">I", len(data)) + data
            elif e.dtype == INT8:
                out.append(int(e.value) & 0xFF)
            else:
                raise ValueError(f"未知 DataType {e.dtype}")
            out.append(e.end_byte & 0xFF)
        return bytes(out)

    def to_bytes(self) -> bytes:
        payload = self._serialize_payload()
        comp = lzo1x_1_compress(payload)
        tokens = comp[5:]  # 去掉 0xF0 标记 + 4 字节长度，剩下的是 LZO token 流
        stream = struct.pack(">I", len(payload)) + tokens
        return hashlib.sha1(stream).digest() + stream

    # -- 通用访问 -------------------------------------------------------

    def get_entry(self, entry_id: int) -> Optional[Entry]:
        for e in self.entries:
            if e.id == entry_id:
                return e
        return None

    def require_entry(self, entry_id: int) -> Entry:
        e = self.get_entry(entry_id)
        if e is None:
            raise ValueError(f"profile 里没有条目 ID={entry_id}（{ENTRY_NAMES.get(entry_id, '')}）")
        return e

    # -- 已解析的常用字段 ----------------------------------------------

    def badass(self) -> dict[str, int]:
        rank = 0
        try:
            rank = (int(self.require_entry(136).value) + int(self.require_entry(137).value)) // 10
        except ValueError:
            pass
        return {
            "rank": rank,
            "tokens_available": int(self.require_entry(138).value) if self.get_entry(138) else 0,
            "tokens_earned": int(self.require_entry(139).value) if self.get_entry(139) else 0,
            "fov": int(self.require_entry(129).value) if self.get_entry(129) else None,
        }

    def golden_keys(self) -> int:
        e = self.get_entry(162)
        if e is None or e.dtype != BINARY:
            return 0
        data = bytes(e.value)
        total = 0
        for i in range(0, len(data) - 2, 3):
            total += max(0, data[i + 1] - data[i + 2])
        return total

    def stash_slots(self) -> list[Optional[str]]:
        """返回 4 个格子的 BL2(...) 代码；空格子为 None。"""
        result: list[Optional[str]] = []
        for entry_id in STASH_ENTRY_IDS:
            e = self.get_entry(entry_id)
            if e is None or e.dtype != BINARY:
                result.append(None)
                continue
            data = bytes(e.value)
            if not data or all(b == 0 for b in data):
                result.append(None)
                continue
            result.append(itemcodes.bytes_to_code(data))
        return result

    def set_stash_slot(self, slot: int, code: Optional[str]) -> None:
        """设置 1-4 号储藏箱格子；code=None 表示清空。"""
        if slot not in (1, 2, 3, 4):
            raise ValueError("储藏箱格子只能是 1-4")
        entry_id = STASH_ENTRY_IDS[slot - 1]
        entry = self.require_entry(entry_id)
        if entry.dtype != BINARY:
            raise ValueError(f"条目 {entry_id} 不是 Binary 类型")
        if code is None or not code.strip():
            entry.value = EMPTY_STASH_BYTES
        else:
            raw = itemcodes.code_to_bytes(code)
            itemcodes.decode(code)  # 校验代码本身可解码
            if len(raw) > 512:
                raise ValueError("物品序列过长（>512 字节）")
            entry.value = raw

    def first_empty_stash_slot(self) -> Optional[int]:
        codes = self.stash_slots()
        for i, c in enumerate(codes, 1):
            if c is None:
                return i
        return None

    def stash_summary(self) -> dict[str, Any]:
        codes = self.stash_slots()
        items = []
        for i, code in enumerate(codes, 1):
            if code is None:
                items.append({"slot": i, "empty": True})
                continue
            try:
                info = itemcodes.decode(code)
                items.append(
                    {
                        "slot": i,
                        "empty": False,
                        "code": code,
                        "is_weapon": bool(info["is_weapon"]),
                        "level": list(info["level"]),
                        "parts": len([p for p in info["parts"] if p]),
                        "describe": itemcodes.describe(code),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                items.append({"slot": i, "empty": False, "code": code, "error": str(exc)})
        return {
            "profile": str(self.path),
            "sha256": hashlib.sha256(self.raw).hexdigest(),
            "entries": len(self.entries),
            "badass": self.badass(),
            "golden_keys": self.golden_keys(),
            "stash": items,
        }

    # -- 保存 -----------------------------------------------------------

    def save(self, target: Optional[str | Path] = None, *, backup: bool = True, force: bool = False) -> Path:
        dest = Path(target) if target else self.path
        if is_game_running() and not force:
            raise RuntimeError("检测到 Borderlands2.exe 正在运行：请先退出游戏再写 profile.bin")
        data = self.to_bytes()
        if backup and dest.exists():
            backup_save(dest, note="ProfileFile.save 自动备份")
        write_bytes_atomic(dest, data)
        if dest == self.path:
            self.raw = data
        return dest

    def save_as(self, target: str | Path) -> Path:
        return self.save(target, backup=False, force=True)


def default_profile_path() -> Optional[Path]:
    from .paths import find_steam_id_dir

    d = find_steam_id_dir()
    if d is None:
        return None
    p = d / PROFILE_NAME
    return p if p.is_file() else None
