"""Reversible purple/orange weapon weight scales in the loaded general gun pool.

These are selection weights, NOT per-kill probabilities. No SDK injection or
game asset files are needed: the pool and fields are resolved through reflection.
"""
from __future__ import annotations

import math
import struct
import memory_scan as m
from bl3_reflection import Reflection

POOL_NAME = 'ItemPool_Guns_All'
POOL_PACKAGE = '/Game/GameData/Loot/ItemPools/Guns/ItemPool_Guns_All'
RARITIES = ('Common', 'Uncommon', 'Rare', 'VeryRare', 'Legendary')
EDITABLE = ('VeryRare', 'Legendary')


def parse_scale(value) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise m.TrainerError('掉落权重系数必须是 0～1000 的数字') from exc
    if not math.isfinite(result) or not 0 <= result <= 1000:
        raise m.TrainerError('掉落权重系数必须在 0～1000 之间，且不能是 NaN 或无穷大')
    return result


class LootWeights:
    def __init__(self, process, status=lambda text: None):
        self.process = process
        self.status = status
        self.status('正在定位武器稀有度池，首次读取可能需要约一分钟……')
        self.ref = Reflection(process)
        self.pool = 0
        self.pool_id = None
        self.active = {}

    def _find_pool(self):
        r, p = self.ref, self.process
        if self.pool:
            try:
                if r.identity(self.pool) == self.pool_id:
                    return self.pool
            except m.TrainerError:
                pass
        matches = []
        for obj in r.objects_iter():
            try:
                if r.objname(obj) != POOL_NAME:
                    continue
                if r.objname(p.read_u64(obj+32)) != POOL_PACKAGE:
                    continue
                if not r.inherits(p.read_u64(obj+16), 'ItemPoolData'):
                    continue
                r.identity(obj)
                matches.append(obj)
            except m.TrainerError:
                continue
        if len(matches) != 1:
            raise m.TrainerError(f'普通武器掉落池应唯一，当前找到 {len(matches)} 个；请进入角色场景后读取')
        self.pool = matches[0]
        self.pool_id = r.identity(self.pool)
        return self.pool

    def _entries(self, discover=True):
        r, p = self.ref, self.process
        pool = self._find_pool() if discover else self.pool
        if not pool or r.identity(pool) != self.pool_id:
            raise m.TrainerError('武器掉落池已卸载，请重新读取')
        cls = p.read_u64(pool+16)
        offset, _, prop = r.property(cls, 'BalancedItems', 'ArrayProperty')
        array = p.read(pool+offset,16)
        data, count, capacity = struct.unpack('<Qii', array)
        if not data or count != 5 or not count <= capacity <= 1024:
            raise m.TrainerError('武器稀有度池结构不同，拒绝猜测写入')
        inner = p.read_u64(prop+0x70)
        if r.objname(p.read_u64(inner+16)) != 'StructProperty':
            raise m.TrainerError('掉落条目不是结构属性')
        stride = p.read_i32(inner+0x34)
        row_struct = p.read_u64(inner+0x70)
        if r.objname(row_struct) != 'BalancedInventoryInfo' or not 64 <= stride <= 512:
            raise m.TrainerError('掉落条目布局不匹配')
        item_offset = r.property(row_struct, 'ItemPoolData', 'ObjectProperty')[0]
        weight_offset, _, weight_prop = r.property(row_struct, 'Weight', 'StructProperty')
        weight_struct = p.read_u64(weight_prop+0x70)
        if r.objname(weight_struct) != 'AttributeInitializationData':
            raise m.TrainerError('掉落权重布局不匹配')
        scale_offset = r.property(weight_struct, 'BaseValueScale', 'FloatProperty')[0]
        attribute_offset = r.property(weight_struct, 'BaseValueAttribute', 'ObjectProperty')[0]
        field_offset = weight_offset+scale_offset
        if field_offset+4 > stride or item_offset+8 > stride or weight_offset+attribute_offset+8 > stride:
            raise m.TrainerError('掉落属性超出条目边界')
        entries = {}
        for index in range(count):
            entry = data+index*stride
            raw = p.read(entry,stride)
            child = struct.unpack_from('<Q',raw,item_offset)[0]
            name = r.objname(child)
            rarity = name.removeprefix('ItemPool_Guns_')
            if rarity not in RARITIES or rarity in entries:
                raise m.TrainerError('武器稀有度条目缺失或重复')
            attribute = struct.unpack_from('<Q',raw,weight_offset+attribute_offset)[0]
            expected = f'Att_RarityWeight_{RARITIES.index(rarity)+1:02d}_{rarity}'
            if r.objname(attribute) != expected:
                raise m.TrainerError('稀有度权重来源已改变，暂不支持该掉落模组')
            value = raw[field_offset:field_offset+4]
            parse_scale(struct.unpack('<f',value)[0])
            entries[rarity] = {
                'address': entry+field_offset, 'value': value,
                'guard': (self.pool_id, array, child, attribute, raw[:field_offset], raw[field_offset+4:]),
            }
        if p.read(pool+offset,16) != array:
            raise m.TrainerError('武器池正在更新，请重试')
        return entries

    @staticmethod
    def _values(entries):
        return {name: struct.unpack('<f',entry['value'])[0] for name,entry in entries.items()}

    def read_current(self):
        self.check()
        return {'feature':'weapon_loot_weights', 'pid':self.process.pid,
                'scales':self._values(self._entries()), 'probability_percent':None,
                'scope':'普通武器池的稀有度权重系数，不是每次击杀的爆率百分比'}

    def set_scales(self, purple, legendary):
        desired = dict(zip(EDITABLE,(parse_scale(purple),parse_scale(legendary))))
        if self.check():
            raise m.TrainerError('掉落池或权重已改变，请先重新读取再应用')
        entries = self._entries()
        # Recheck the live container and all identifying metadata immediately before writes.
        if self._entries(discover=False) != entries:
            raise m.TrainerError('掉落数据正在变化，请重试')
        previous = {k:dict(v) for k,v in self.active.items()}
        attempted = []
        try:
            for rarity in EDITABLE:
                entry = entries[rarity]
                data = struct.pack('<f',desired[rarity])
                original = self.active.get(rarity,{}).get('original',entry['value'])
                self.active[rarity] = dict(entry, original=original, written=data)
                attempted.append(rarity)
                self.process.write(entry['address'],data,entry['value'])
        except Exception as exc:
            failures = []
            for rarity in reversed(attempted):
                entry, state = entries[rarity], self.active[rarity]
                try:
                    fresh = self._entries(discover=False)[rarity]
                    if fresh['guard'] != entry['guard']:
                        raise m.TrainerError('掉落池已变更，无法回滚')
                    if fresh['value'] == state['written']:
                        self.process.write(entry['address'],entry['value'],state['written'])
                    elif fresh['value'] != entry['value']:
                        raise m.TrainerError('权重被外部更新，未覆盖外部值')
                    if rarity in previous:
                        self.active[rarity] = previous[rarity]
                    else:
                        self.active.pop(rarity,None)
                except Exception as rollback_error:
                    failures.append(str(rollback_error))
            if failures:
                raise m.TrainerError(f'应用失败且部分回滚失败，请点击恢复原值重试：{failures}') from exc
            raise
        return self.read_current()

    def restore(self):
        message = self.check()
        if not self.active:
            return {'feature':'weapon_loot_weights','status':'unchanged','message':message}
        entries = self._entries(discover=False)
        skipped = []
        errors = []
        for rarity,state in list(self.active.items()):
            entry = entries[rarity]
            try:
                if entry['guard'] != state['guard']:
                    skipped.append(rarity)
                elif entry['value'] == state['written']:
                    self.process.write(state['address'],state['original'],state['written'])
                elif entry['value'] != state['original']:
                    skipped.append(rarity)
                del self.active[rarity]
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise m.TrainerError('掉落权重恢复失败，请重试：'+'；'.join(errors))
        return {'feature':'weapon_loot_weights','status':'restored' if not skipped and not message else 'external_change',
                'skipped':skipped,'message':message}

    def check(self):
        if not self.pool:
            return None
        # Reading failures are reported, rather than silently dropping recovery records.
        try:
            identity = self.ref.identity(self.pool)
        except m.TrainerError:
            # No writes through an unloaded asset. The OS/game owns its lifetime.
            self.active.clear()
            self.pool = 0
            self.pool_id = None
            return '掉落池已卸载；进入场景后重新读取并应用'
        if identity != self.pool_id:
            self.active.clear()
            self.pool = 0
            self.pool_id = None
            return '掉落池已替换；请重新读取并应用'
        if not self.active:
            return None
        entries = self._entries(discover=False)
        changed = []
        for rarity,state in list(self.active.items()):
            entry = entries[rarity]
            if entry['guard'] != state['guard'] or entry['value'] != state['written']:
                changed.append(rarity)
                del self.active[rarity]
        return '游戏已更新部分掉落权重，请重新读取确认' if changed else None

    def close(self):
        self.check()
        self.restore()
        self.process.close()
