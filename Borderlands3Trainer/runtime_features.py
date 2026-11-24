#!/usr/bin/env python3
"""Version-checked BL3 runtime features built from unique AOB signatures.

Numeric features install a short-lived capture hook, wait for the game to use
the relevant object, restore the original instruction immediately, and then
perform one checked data write. Persistent toggles are reversible byte patches.
"""

from __future__ import annotations

import struct
import time
from typing import Callable

import memory_scan
import save_values
from movement_speed import MovementSpeed
from loot_weights import LootWeights


PROCESS_NAME = "Borderlands3.exe"
MODULE_NAME = "Borderlands3.exe"


CAPTURES = {
    "currency": {
        "pattern": (
            "48 8B 89 28 02 00 00 49 C1 E0 06 4C 03 C1 49 3B C8 "
            "74 ?? 48 39 51 10 75 03 03 41 18 48 83 C1 40"
        ),
        "hook_offset": 23,
        "original": bytes.fromhex("75 03 03 41 18"),
        "kind": "currency",
        "hint": "正在自动定位现金和镒矿……",
    },
    "xp": {
        "pattern": (
            "48 8D 8E 28 08 00 00 C7 86 40 08 00 00 00 00 00 00 "
            "E8 ?? ?? ?? ?? 8B 80 D8 01 00 00 89 86 48 08 00 00"
        ),
        "hook_offset": 22,
        "original": bytes.fromhex("8B 80 D8 01 00 00"),
        "kind": "rax",
        "hint": "正在自动定位经验值……",
    },
    "skill": {
        "pattern": (
            "F7 40 08 00 00 00 30 75 ?? 48 8B 0B 48 8B 81 20 08 00 00 "
            "48 85 C0 74 ?? 44 8B 81 5C 08 00 00 8B 90 60 01 00 00"
        ),
        "hook_offset": 31,
        "original": bytes.fromhex("8B 90 60 01 00 00"),
        "kind": "rax",
        "hint": "正在自动定位技能点……",
    },
    "golden_keys": {
        "pattern": "48 85 C0 74 F8 8B 40 08 C3",
        "hook_offset": 3,
        "original": bytes.fromhex("74 F8 8B 40 08"),
        "kind": "golden_keys",
        "hint": "正在自动定位金钥匙……",
    },
}


VALUES = {
    "money": {"capture": "currency", "offset": 0x18, "minimum": 0, "maximum": 2_000_000_000},
    "eridium": {"capture": "currency", "offset": 0x58, "minimum": 0, "maximum": 2_000_000_000},
    "xp": {"capture": "xp", "offset": 0x1D8, "minimum": 0, "maximum": 2_000_000_000},
    "skill_points": {"capture": "skill", "offset": 0x160, "minimum": 0, "maximum": 9999},
    "golden_keys": {"capture": "golden_keys", "offset": 0x08, "minimum": 0, "maximum": 2_000_000_000},
}


PATCHES = {
    "infinite_ammo": {
        "pattern": "48 89 7C 24 40 FF 90 48 04 00 00",
        "patch_offset": -0x1E,
        "original": bytes.fromhex("40 53"),
        "patched": bytes.fromhex("C3 90"),
    },
    "keep_skill_points": {
        "pattern": "41 3B D1 74 24 89 96 60 01 00 00 48 8D 4E 48",
        "patch_offset": 5,
        "original": bytes.fromhex("89 96 60 01 00 00"),
        "patched": b"\x90" * 6,
    },
}


StatusCallback = Callable[[str], None]


def _rip_store(register: str, instruction_address: int, pointer_address: int) -> bytes:
    opcodes = {
        "rax": bytes.fromhex("48 89 05"),
        "rcx": bytes.fromhex("48 89 0D"),
    }
    displacement = pointer_address - (instruction_address + 7)
    if not -(2**31) <= displacement < 2**31:
        raise memory_scan.TrainerError("Capture pointer storage is outside rel32 range")
    return opcodes[register] + struct.pack("<i", displacement)


def build_capture_stub(
    kind: str,
    remote_address: int,
    hook_address: int,
    original: bytes,
    pointer_offset: int = 0x40,
) -> tuple[bytes, int]:
    """Build a tiny x64 capture stub and return (blob, pointer address)."""
    pointer_address = remote_address + pointer_offset

    if kind == "currency":
        code = bytearray()
        code += b"\x9C"                         # pushfq
        code += bytes.fromhex("49 83 F9 0A")      # cmp r9, 0x0A
        code += bytes.fromhex("74 07")            # je popfq
        code += _rip_store("rcx", remote_address + len(code), pointer_address)
        code += b"\x9D"                          # popfq
        code += bytes.fromhex("75 03 03 41 18")   # original branch/add
    elif kind == "golden_keys":
        code = bytearray(
            _rip_store("rax", remote_address, pointer_address)
        )
        branch_address = remote_address + len(code)
        original_target = hook_address - 6
        displacement = original_target - (branch_address + 6)
        code += bytes.fromhex("0F 84") + struct.pack("<i", displacement)
        code += bytes.fromhex("8B 40 08")
    elif kind in {"rax", "rcx"}:
        code = bytearray(_rip_store(kind, remote_address, pointer_address))
        code += original
    else:
        raise ValueError(f"Unknown capture stub kind: {kind}")

    code += memory_scan.relative_jump(
        remote_address + len(code), hook_address + len(original)
    )
    if len(code) > pointer_offset:
        raise memory_scan.TrainerError("Capture stub overlaps pointer storage")
    code += b"\x90" * (pointer_offset - len(code))
    code += b"\x00" * 8
    return bytes(code), pointer_address


class FeatureManager:
    def __init__(self, expected_executable: str, status: StatusCallback | None = None) -> None:
        self.expected_executable = expected_executable
        self.status = status or (lambda text: None)
        self.pid: int | None = None
        self.module_base = 0
        self.module_size = 0
        self.process_path = ""
        self.process_sha256 = ""
        self.aob_cache: dict[str, int] = {}
        self.pointer_cache: dict[str, int] = {}
        self.enabled_patches: set[str] = set()
        self._character_saves: list[dict] | None = None
        self._profile_saves: list[dict] | None = None
        self.movement: MovementSpeed | None = None
        self.loot: LootWeights | None = None

    def reset_for_process(self, pid: int) -> None:
        if self.pid == pid:
            return
        if self.movement is not None:
            self.movement.close()
            self.movement = None
        if self.loot is not None:
            self.loot.close()
            self.loot = None
        self.pid = pid
        self.module_base = 0
        self.module_size = 0
        self.process_path = ""
        self.process_sha256 = ""
        self.aob_cache.clear()
        self.pointer_cache.clear()
        self.enabled_patches.clear()
        self._character_saves = None
        self._profile_saves = None

    def open(self, writable: bool) -> memory_scan.ProcessMemory:
        pid = memory_scan.find_process(PROCESS_NAME)
        self.reset_for_process(pid)
        process = memory_scan.ProcessMemory(pid, writable=writable)
        try:
            path = process.image_path()
            if self.expected_executable and path.casefold() != self.expected_executable.casefold():
                raise memory_scan.TrainerError(f"游戏路径不匹配：{path}")
            self.process_path = path
            if not self.module_base:
                self.module_base, self.module_size, _ = memory_scan.find_module(
                    pid, MODULE_NAME, process.handle
                )
            if not self.process_sha256:
                self.process_sha256 = memory_scan.sha256_file(path)
            return process
        except Exception:
            process.close()
            raise

    def _resolve_aob(self, process: memory_scan.ProcessMemory, cache_key: str, pattern: str) -> int:
        if cache_key in self.aob_cache:
            return self.aob_cache[cache_key]
        matches = memory_scan.scan_module_aob(
            process, self.module_base, self.module_size, pattern
        )
        if len(matches) != 1:
            raise memory_scan.TrainerError(
                f"功能特征 {cache_key} 应唯一匹配，实际找到 {len(matches)} 个。"
                "当前游戏版本暂不安全支持此功能。"
            )
        self.aob_cache[cache_key] = matches[0]
        return matches[0]

    @staticmethod
    def _pointer_plausible(
        process: memory_scan.ProcessMemory, capture_name: str, pointer: int
    ) -> bool:
        if pointer <= 0x10000 or pointer & 7:
            return False
        try:
            if capture_name == "currency":
                money = process.read_i32(pointer + 0x18)
                eridium = process.read_i32(pointer + 0x58)
                return 0 <= money <= 2_000_000_000 and 0 <= eridium <= 2_000_000_000
            if capture_name == "xp":
                return 0 <= process.read_i32(pointer + 0x1D8) <= 2_000_000_000
            if capture_name == "skill":
                return 0 <= process.read_i32(pointer + 0x160) <= 9999
            if capture_name == "golden_keys":
                return 0 <= process.read_i32(pointer + 0x08) <= 2_000_000_000
        except Exception:
            return False
        return False

    def _uobject_like(self, process: memory_scan.ProcessMemory, pointer: int) -> bool:
        """Conservative check for an object whose vtable belongs to the game."""
        if pointer <= 0x10000 or pointer & 7:
            return False
        try:
            vtable = process.read_u64(pointer)
        except Exception:
            return False
        return self.module_base <= vtable < self.module_base + self.module_size

    def _latest_character_saves(self) -> list[dict]:
        if self._character_saves is None:
            self._character_saves = save_values.character_snapshots()
        return self._character_saves[:3]

    def _latest_profile_saves(self) -> list[dict]:
        if self._profile_saves is None:
            self._profile_saves = save_values.profile_snapshots()
        return self._profile_saves[:2]

    def _save_candidates(self, capture_name: str) -> list[tuple[int, dict]]:
        if capture_name == "golden_keys":
            return [
                (int(item["golden_keys"]), item)
                for item in self._latest_profile_saves()
            ]
        field = {
            "currency": "money",
            "xp": "xp",
            "skill": "skill_points",
        }.get(capture_name)
        if not field:
            return []
        return [(int(item[field]), item) for item in self._latest_character_saves()]

    def _validate_saved_candidate(
        self,
        process: memory_scan.ProcessMemory,
        capture_name: str,
        value_address: int,
        snapshot: dict,
    ) -> int:
        offset = {
            "currency": 0x18,
            "xp": 0x1D8,
            "skill": 0x160,
            "golden_keys": 0x08,
        }[capture_name]
        pointer = value_address - offset
        if not self._pointer_plausible(process, capture_name, pointer):
            return 0

        if capture_name == "currency":
            if process.read_i32(pointer + 0x58) != int(snapshot["eridium"]):
                return 0
            # Currency entries are 0x40 bytes apart.  Their key fields should
            # be distinct and non-null; when they are object pointers, require
            # at least one game-module vtable as additional confirmation.
            key_a = process.read_u64(pointer + 0x10)
            key_b = process.read_u64(pointer + 0x50)
            if not key_a or not key_b or key_a == key_b:
                return 0
            key_object_score = int(self._uobject_like(process, key_a)) + int(
                self._uobject_like(process, key_b)
            )
            if key_object_score == 0 and key_a > 0xFFFFFFFF and key_b > 0xFFFFFFFF:
                return 0
            return pointer

        if not self._uobject_like(process, pointer):
            return 0
        return pointer

    def _locate_pointer_from_saves(
        self, process: memory_scan.ProcessMemory, capture_name: str
    ) -> int:
        """Try to locate a live object from read-only local-save values."""
        for saved_value, snapshot in self._save_candidates(capture_name):
            # Common values such as 0 and 1 produce too many false positives
            # for standalone fields.  Currency remains usable because its
            # companion value and 0x40 entry stride provide extra structure.
            if capture_name != "currency" and saved_value in {0, 1, -1}:
                continue
            try:
                addresses = memory_scan.scan_i32(
                    process,
                    saved_value,
                    max_candidates=250_000,
                )
            except memory_scan.TooManyCandidates:
                continue
            matches: list[int] = []
            for address in addresses:
                try:
                    pointer = self._validate_saved_candidate(
                        process, capture_name, address, snapshot
                    )
                except Exception:
                    continue
                if pointer:
                    matches.append(pointer)
            unique = sorted(set(matches))
            if len(unique) == 1:
                self.pointer_cache[capture_name] = unique[0]
                self.status("已通过本地存档值完成自动定位")
                return unique[0]
        return 0

    def _capture_pointer(
        self,
        process: memory_scan.ProcessMemory,
        capture_name: str,
        timeout: float = 12.0,
    ) -> int:
        definition = CAPTURES[capture_name]
        match = self._resolve_aob(
            process, f"capture:{capture_name}", str(definition["pattern"])
        )
        hook = match + int(definition["hook_offset"])
        original = bytes(definition["original"])
        if process.read(hook, len(original)) != original:
            raise memory_scan.TrainerError(f"{capture_name} 原始指令校验失败")

        remote = process.allocate_near(hook)
        stub, pointer_storage = build_capture_stub(
            str(definition["kind"]), remote, hook, original
        )
        patch = memory_scan.relative_jump(hook, remote)
        patch += b"\x90" * (len(original) - len(patch))
        installed = False
        captured = 0
        try:
            process.write(remote, stub, b"\x00" * len(stub))
            with memory_scan.suspended_process_threads(int(self.pid or 0)):
                process.patch_code(hook, patch, original)
            installed = True
            self.status(str(definition["hint"]))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                pointer = process.read_u64(pointer_storage)
                if pointer and self._pointer_plausible(process, capture_name, pointer):
                    captured = pointer
                    break
                time.sleep(0.05)
            if not captured:
                raise memory_scan.TrainerError(
                    f"未能自动定位 {capture_name}。请进入游戏场景后重试，并按提示打开对应菜单。"
                )
        finally:
            try:
                if installed:
                    with memory_scan.suspended_process_threads(int(self.pid or 0)):
                        process.patch_code(hook, original, patch)
                    time.sleep(0.20)
            finally:
                process.free(remote)

        self.pointer_cache[capture_name] = captured
        return captured

    def _get_pointer(
        self, process: memory_scan.ProcessMemory, capture_name: str
    ) -> int:
        cached = self.pointer_cache.get(capture_name, 0)
        if cached and self._pointer_plausible(process, capture_name, cached):
            return cached
        self.pointer_cache.pop(capture_name, None)
        saved_pointer = self._locate_pointer_from_saves(process, capture_name)
        if saved_pointer:
            return saved_pointer
        return self._capture_pointer(process, capture_name)

    def set_value(self, feature_name: str, value: int) -> dict:
        definition = VALUES[feature_name]
        minimum = int(definition["minimum"])
        maximum = int(definition["maximum"])
        if not minimum <= value <= maximum:
            raise memory_scan.TrainerError(
                f"目标值必须在 {minimum} 到 {maximum} 之间"
            )
        with self.open(writable=True) as process:
            pointer = self._get_pointer(process, str(definition["capture"]))
            address = pointer + int(definition["offset"])
            old_value = process.read_i32(address)
            readback = process.write_i32(address, value, old_value)
        return {
            "feature": feature_name,
            "pid": self.pid,
            "process_path": self.process_path,
            "process_sha256": self.process_sha256,
            "address": f"0x{address:016X}",
            "value_type": "int32",
            "old_value": old_value,
            "new_value": value,
            "readback": readback,
        }

    def set_patch(self, patch_name: str, enabled: bool) -> dict:
        definition = PATCHES[patch_name]
        with self.open(writable=True) as process:
            match = self._resolve_aob(
                process, f"patch:{patch_name}", str(definition["pattern"])
            )
            address = match + int(definition["patch_offset"])
            original = bytes(definition["original"])
            patched = bytes(definition["patched"])
            current = process.read(address, len(original))

            if enabled:
                if current == patched:
                    self.enabled_patches.add(patch_name)
                elif current == original:
                    with memory_scan.suspended_process_threads(int(self.pid or 0)):
                        process.patch_code(address, patched, original)
                    self.enabled_patches.add(patch_name)
                else:
                    raise memory_scan.TrainerError(
                        f"{patch_name} 原始字节不匹配，拒绝启用"
                    )
            else:
                if current == original:
                    self.enabled_patches.discard(patch_name)
                elif current == patched:
                    with memory_scan.suspended_process_threads(int(self.pid or 0)):
                        process.patch_code(address, original, patched)
                    self.enabled_patches.discard(patch_name)
                else:
                    raise memory_scan.TrainerError(
                        f"{patch_name} 当前字节不匹配，拒绝恢复"
                    )
        return {
            "feature": patch_name,
            "enabled": enabled,
            "pid": self.pid,
            "process_path": self.process_path,
            "process_sha256": self.process_sha256,
        }

    def set_movement_multiplier(self, multiplier: int) -> dict:
        if multiplier == 1:
            return self.movement.restore() if self.movement else {
                'feature': 'movement_speed', 'multiplier': 1, 'status': 'restored'}
        pid = memory_scan.find_process(PROCESS_NAME)
        self.reset_for_process(pid)
        if self.movement is None:
            process = self.open(writable=True)
            try:
                self.movement = MovementSpeed(process, self.status)
            except Exception:
                process.close()
                raise
        return self.movement.set_multiplier(multiplier)

    def check_movement(self) -> str | None:
        return self.movement.check() if self.movement else None

    def _loot_session(self) -> LootWeights:
        self.reset_for_process(memory_scan.find_process(PROCESS_NAME))
        if self.loot is None:
            process = self.open(writable=True)
            try:
                self.loot = LootWeights(process, self.status)
            except Exception:
                process.close()
                raise
        return self.loot

    def read_loot(self) -> dict:
        return self._loot_session().read_current()

    def set_loot(self, purple, legendary) -> dict:
        return self._loot_session().set_scales(purple, legendary)

    def restore_loot(self) -> dict:
        return self.loot.restore() if self.loot else {'feature':'weapon_loot_weights','status':'unchanged'}

    def check_loot(self) -> tuple:
        if self.loot is None:
            return None, None
        message = self.loot.check()
        scales = self.loot._values(self.loot._entries(discover=False)) if self.loot.pool else None
        return message, scales

    def restore_all(self) -> list[str]:
        restored: list[str] = []
        errors: list[str] = []
        if self.loot:
            try:
                self.loot.restore()
                restored.append('weapon_loot_weights')
            except Exception as exc:
                errors.append(str(exc))
        if self.movement:
            try:
                self.movement.restore()
                restored.append('movement_speed')
            except Exception as exc:
                errors.append(str(exc))
        for patch_name in list(self.enabled_patches):
            try:
                self.set_patch(patch_name, False)
                restored.append(patch_name)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise memory_scan.TrainerError('部分功能恢复失败，请重试：' + '；'.join(errors))
        return restored
