"""Read-only BL3 reflection inspector; no game memory writes.

Layout reference: bl-sdk/unrealsdk game/bl3/offsets.h and globals.cpp.
"""
import struct
import memory_scan as m


class Reflection:
    def __init__(self, p):
        self.p = p
        self.base, self.size, _ = m.find_module(p.pid, 'Borderlands3.exe', p.handle)
        patterns = [
            '48 8D 0D ?? ?? ?? ?? C6 05 ?? ?? ?? ?? 01 E8 ?? ?? ?? ?? C6 05 ?? ?? ?? ?? 01',
            'E8 ?? ?? ?? ?? 48 8B C3 48 89 1D ?? ?? ?? ?? 48 8B 5C 24 ?? 48 83 C4 28 C3 33 DB',
        ]
        matches = [m.scan_module_aob(p, self.base, self.size, pat) for pat in patterns]
        if any(len(x) != 1 for x in matches):
            raise RuntimeError(f'Global signatures not unique: {matches}')
        a, b = matches[0][0], matches[1][0]
        self.objects = a + 7 + p.read_i32(a + 3)
        self.names = p.read_u64(b + 15 + p.read_i32(b + 11))
        self.cache = {}
        self.property_cache = {}
        self.inheritance_cache = {}
        if self.name(0) != 'None':
            raise m.TrainerError('游戏名称表格式不受支持')

    def name(self, idx):
        if idx not in self.cache:
            if not 0 <= idx < 4*1024*1024:
                return '?'
            chunk = self.p.read_u64(self.names + (idx // 16384)*8)
            entry = self.p.read_u64(chunk + (idx % 16384)*8)
            flags = self.p.read_i32(entry)
            raw = self.p.read_partial(entry + 16, 256)
            self.cache[idx] = (raw.decode('utf-16-le', errors='replace').split('\0')[0]
                               if flags & 1 else raw.split(b'\0')[0].decode(errors='replace'))
        return self.cache[idx]

    def objname(self, obj):
        return self.name(self.p.read_i32(obj + 24)) if obj else 'None'

    def objects_iter(self):
        chunks = self.p.read_u64(self.objects + 16)
        count = self.p.read_i32(self.objects + 36)
        if not 0 < count < 4_000_000:
            raise RuntimeError(f'Invalid object count {count}')
        for start in range(0, count, 65536):
            chunk = self.p.read_u64(chunks + start//65536*8)
            data = self.p.read(chunk, min(65536, count-start)*24)
            for off in range(0, len(data), 24):
                obj = struct.unpack_from('<Q', data, off)[0]
                if obj:
                    yield obj

    def props(self, cls):
        seen = set()
        while cls and cls not in seen and len(seen) < 64:
            seen.add(cls)
            child = self.p.read_u64(cls + 0x38)
            visited = set()
            while child and child not in visited and len(visited) < 10000:
                visited.add(child)
                kind = self.objname(self.p.read_u64(child+16))
                if 'Property' in kind:
                    yield self.objname(child), self.p.read_i32(child+0x44), kind, child
                child = self.p.read_u64(child+0x28)
            cls = self.p.read_u64(cls+0x30)

    def inherits(self, cls, wanted):
        key = (cls, wanted)
        if key in self.inheritance_cache:
            return self.inheritance_cache[key]
        for _ in range(64):
            if not cls:
                self.inheritance_cache[key] = False
                return False
            if self.objname(cls) == wanted:
                self.inheritance_cache[key] = True
                return True
            cls = self.p.read_u64(cls+0x30)
        return False

    def property(self, cls, name, kind=None):
        if cls not in self.property_cache:
            props = {}
            for n, offset, typ, address in self.props(cls):
                props.setdefault(n, (offset, typ, address))
            self.property_cache[cls] = props
        found = self.property_cache[cls].get(name)
        if not found or not 0 <= found[0] < 0x100000 or (kind and found[1] != kind):
            raise m.TrainerError(f'游戏属性不受支持：{name}')
        return found

    def offset(self, obj, name, kind=None):
        return self.property(self.p.read_u64(obj+16), name, kind)[0]

    def object_pointer(self, obj, name):
        return self.p.read_u64(obj + self.offset(obj, name, 'ObjectProperty'))

    def identity(self, obj):
        """Use the object table slot/serial as well as address to reject recycled objects."""
        index = self.p.read_i32(obj+12)
        count = self.p.read_i32(self.objects+36)
        if not 0 <= index < count:
            raise m.TrainerError('游戏对象已失效')
        chunks = self.p.read_u64(self.objects+16)
        chunk = self.p.read_u64(chunks + index//65536*8)
        entry = self.p.read(chunk + index%65536*24, 24)
        address, flags, cluster, serial = struct.unpack_from('<Qiii', entry)
        if address != obj or flags & (0x20000000 | 0x10000000) or self.p.read_i32(obj+8) & (0x10 | 0x8000 | 0x10000):
            raise m.TrainerError('游戏对象已卸载或不可修改')
        return obj, index, serial


if __name__ == '__main__':
    with m.ProcessMemory(m.find_process('Borderlands3.exe')) as p:
        r = Reflection(p)
        print('globals', hex(r.objects), hex(r.names), flush=True)
        classes = {}
        for obj in r.objects_iter():
            try:
                name = r.objname(obj)
                cls = p.read_u64(obj+16)
                if cls not in classes:
                    classes[cls] = r.inherits(cls, 'OakPlayerController')
                if classes[cls] and not p.read_i32(obj+8) & 0x10:
                    print('CONTROLLER', hex(obj), name, flush=True)
                    props = {n:o for n,o,k,a in r.props(cls)}
                    for key in ('Player','AcknowledgedPawn','Pawn'):
                        value = p.read_u64(obj+props[key])
                        print(key, hex(value), r.objname(value), flush=True)
                    pawn = p.read_u64(obj+props['Pawn'])
                    if pawn:
                        pp = {n:o for n,o,k,a in r.props(p.read_u64(pawn+16))}
                        if 'OakCharacterMovement' not in pp:
                            continue
                        movement = p.read_u64(pawn+pp['OakCharacterMovement'])
                        print('MOVEMENT', hex(movement), r.objname(movement), flush=True)
                        for n,o,k,a in r.props(p.read_u64(movement+16)):
                            if n in ('MaxWalkSpeed','MaxSprintSpeed','MaxGroundSpeedScale','Velocity'):
                                print(n, hex(o), k, p.read(movement+o,12).hex(), flush=True)
                                if k == 'StructProperty':
                                    st = p.read_u64(a+0x70)
                                    print('STRUCT',r.objname(st), list(r.props(st)),flush=True)
                if False:
                    print(hex(obj), name, 'class', r.objname(p.read_u64(obj+16)), flush=True)
                    cls = obj if r.objname(p.read_u64(obj+16)) == 'Class' else p.read_u64(obj+16)
                    for prop in r.props(cls):
                        if any(word in prop[0].lower() for word in ('speed','pawn','player','movement','owner','velocity','sprint')):
                            print('  ', prop, flush=True)
            except (OSError, RuntimeError):
                continue
