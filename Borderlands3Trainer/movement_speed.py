"""Player-only movement multiplier using live UE reflection, without code hooks."""
from __future__ import annotations

import math
import struct
import memory_scan as m
from bl3_reflection import Reflection


def scaled_value(baseline: bytes, multiplier: int) -> bytes:
    if type(multiplier) is not int or multiplier not in (1, 2, 3):
        raise m.TrainerError('移动倍率仅支持 1、2、3 倍')
    value = struct.unpack('<f', baseline)[0]
    if not math.isfinite(value) or not 0.01 <= value <= 20:
        raise m.TrainerError('原始移动倍率异常，拒绝修改')
    return struct.pack('<f', value * multiplier)


class MovementSpeed:
    def __init__(self, process, status=lambda text: None):
        self.process = process
        self.status = status
        self.status('正在识别移动属性，首次定位约需 30～90 秒……')
        self.ref = Reflection(process)
        self.controller = 0
        self.controller_id = None
        self.active = None

    def _controller_valid(self, controller):
        r = self.ref
        r.identity(controller)
        cls = self.process.read_u64(controller+16)
        if not r.inherits(cls, 'OakPlayerController'):
            return False
        player = r.object_pointer(controller, 'Player')
        if not player:
            return False
        r.identity(player)
        if not r.inherits(self.process.read_u64(player+16), 'LocalPlayer'):
            return False
        return r.object_pointer(player, 'PlayerController') == controller

    def _find_controller(self, discover=True):
        r = self.ref
        if self.controller:
            try:
                if r.identity(self.controller) == self.controller_id and self._controller_valid(self.controller):
                    return self.controller
            except m.TrainerError:
                pass
        self.controller = 0
        if not discover:
            raise m.TrainerError('当前玩家已离开场景')
        matches = []
        for obj in r.objects_iter():
            try:
                cls = self.process.read_u64(obj+16)
                if r.inherits(cls, 'OakPlayerController') and self._controller_valid(obj):
                    matches.append(obj)
            except (m.TrainerError, OSError):
                continue
        if len(matches) != 1:
            raise m.TrainerError(f'需要唯一的本地玩家，当前找到 {len(matches)} 个；请进入单人角色场景')
        self.controller = matches[0]
        self.controller_id = r.identity(self.controller)
        return self.controller

    def _target(self, discover=True):
        r = self.ref
        controller = self._find_controller(discover)
        pawn = r.object_pointer(controller, 'Pawn')
        if not pawn or r.object_pointer(controller, 'AcknowledgedPawn') != pawn:
            raise m.TrainerError('角色正在切换，请进入场景后重试')
        r.identity(pawn)
        if not r.inherits(self.process.read_u64(pawn+16), 'OakCharacter'):
            raise m.TrainerError('请退出载具，使用步行角色后再设置速度')
        if r.object_pointer(pawn, 'Controller') != controller:
            raise m.TrainerError('当前角色归属校验失败')
        movement = r.object_pointer(pawn, 'OakCharacterMovement')
        identity = r.identity(movement)
        if not r.inherits(self.process.read_u64(movement+16), 'OakCharacterMovementComponent'):
            raise m.TrainerError('移动组件类型不匹配')
        if r.object_pointer(movement, 'CharacterOwner') != pawn:
            raise m.TrainerError('移动组件不属于当前玩家')
        off, _, prop = r.property(self.process.read_u64(movement+16), 'MaxGroundSpeedScale', 'StructProperty')
        attr = self.process.read_u64(prop+0x70)
        if r.objname(attr) != 'GbxAttributeFloat':
            raise m.TrainerError('移动倍率属性格式不匹配')
        value_off = r.property(attr, 'Value', 'FloatProperty')[0]
        address = movement+off+value_off
        return {'identity': identity, 'pawn': pawn, 'controller': controller, 'address': address}

    def _same_object(self, state):
        try:
            return self.ref.identity(state['identity'][0]) == state['identity']
        except m.TrainerError:
            return False

    def set_multiplier(self, multiplier):
        # Validate even when returning to normal speed.
        scaled_value(struct.pack('<f', 1.0), multiplier)
        if multiplier == 1:
            return self.restore()
        target = self._target()
        if self.active and target['identity'] != self.active['identity']:
            self.restore()
        if self.active:
            original = self.active['original']
            expected = self.active['written']
        else:
            original = self.process.read(target['address'], 4)
            expected = original
        data = scaled_value(original, multiplier)
        # Resolve again before writing, protecting against travel during initial discovery.
        if self._target() != target:
            raise m.TrainerError('角色已切换，请重试')
        # Keep recovery data before a write that might fail at the readback stage.
        state = dict(target, original=original, written=data, multiplier=multiplier)
        previous = self.active
        self.active = state
        try:
            self.process.write(target['address'], data, expected)
        except Exception:
            try:
                current = self.process.read(target['address'], 4)
                if current == expected:
                    self.active = previous
            except m.TrainerError:
                pass  # Keep the original bytes for a later recovery attempt.
            raise
        self.active = state
        return {'feature': 'movement_speed', 'pid': self.process.pid,
                'multiplier': multiplier, 'original_scale': struct.unpack('<f',original)[0],
                'new_scale': struct.unpack('<f',data)[0]}

    def restore(self):
        state = self.active
        if state:
            if self._same_object(state):
                current = self.process.read(state['address'], 4)
                if current == state['written']:
                    self.process.write(state['address'], state['original'], current)
                elif current != state['original']:
                    # A skill/game update owns this value now; don't overwrite it.
                    self.active = None
                    return {'feature': 'movement_speed', 'multiplier': 1,
                            'status': 'game_changed', 'message': '游戏已更新速度属性，已停止加速并保留游戏当前值'}
            self.active = None
        return {'feature': 'movement_speed', 'multiplier': 1, 'status': 'restored'}

    def check(self):
        """Cheap state check only; never continuously scan or freeze values."""
        if not self.active:
            return None
        state = self.active
        if not self._same_object(state):
            self.active = None
            return '角色已卸载，加速已停止；进入场景后重新选择倍率'
        try:
            current = self._target(discover=False)
        except m.TrainerError:
            self.restore()
            return '角色或载具状态改变，加速已停止；步行时可重新启用'
        if current['identity'] != state['identity']:
            self.restore()
            return '角色已切换，原角色速度已恢复；请重新选择倍率'
        if self.process.read(state['address'],4) != state['written']:
            self.active = None
            return '游戏已重新计算移动属性，加速已停止；可重新选择倍率'
        return None

    def close(self):
        self.restore()
        self.process.close()
