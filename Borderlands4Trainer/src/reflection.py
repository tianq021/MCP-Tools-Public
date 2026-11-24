"""Experimental read-only BL4 reflection, derived from locally validated layouts.
References: https://github.com/bl-sdk/unrealsdk/tree/master/src/unrealsdk/game/bl4
"""
import struct
from collections import OrderedDict

class Reflection:
    def __init__(self, reader, base, signatures):
        self.p, self.cache = reader, {}
        self.objects = base + signatures['objects'][0]['target_rva']
        self.names = base + signatures['names_nonpgo'][0]['target_rva']
        # Runtime instructions must agree with statically resolved RIP operands.
        for key, prefix, displacement in [('objects', b'\x44\x8b\x05', 3),
                                          ('names_nonpgo', b'\x48\x89\xce', 15)]:
            row = signatures[key][0]
            address = base + row['rva']
            raw = reader.read(address, displacement + 4)
            if not raw.startswith(prefix) or address + displacement + 4 + struct.unpack_from('<i', raw, displacement)[0] != base + row['target_rva']:
                raise ValueError('Runtime signature disagrees with file')
        if self.name(0) != 'None':
            raise ValueError('Unrecognized FName pool layout')

    def u64(self, address):
        return struct.unpack('<Q', self.p.read(address, 8))[0]

    def i32(self, address):
        return struct.unpack('<i', self.p.read(address, 4))[0]

    def name(self, index):
        if index not in self.cache:
            chunk, offset = index >> 16, index & 65535
            if chunk > self.i32(self.names + 8):
                raise ValueError('Name index outside pool')
            address = self.u64(self.names + 16 + chunk * 8) + offset * 2
            meta = struct.unpack('<H', self.p.read(address, 2))[0]
            length = meta >> 6
            if not 0 < length <= 1024:
                raise ValueError('Invalid name length')
            self.cache[index] = self.p.read(address + 2, length * (2 if meta & 1 else 1)).decode('utf-16-le' if meta & 1 else 'utf-8')
        return self.cache[index]

    def objname(self, address):
        return self.name(struct.unpack('<I', self.p.read(address + 24, 4))[0]) if address else 'None'

    def objects_iter(self):
        chunks, count = self.u64(self.objects + 16), self.i32(self.objects + 36)
        if not 0 < count < 2000000:
            raise ValueError('Unexpected object count')
        for start in range(0, count, 65536):
            self.p.require_alive()
            chunk = self.u64(chunks + start // 65536 * 8)
            data = self.p.read(chunk, min(65536, count - start) * 24)
            for offset in range(0, len(data), 24):
                address = struct.unpack_from('<Q', data, offset)[0]
                if address:
                    yield start + offset // 24, address

    def object_headers(self):
        """Scan-local page snapshots; callers must revalidate selected objects live."""
        pages = OrderedDict()
        for index, obj in self.objects_iter():
            address = obj + 8
            page, offset = address & ~4095, address & 4095
            try:
                if offset + 16 <= 4096:
                    if page not in pages:
                        try:
                            pages[page] = self.p.read(page, 4096)
                        except OSError:
                            pages[page] = None
                        if len(pages) > 1024:
                            pages.popitem(last=False)
                    pages.move_to_end(page)
                    data = pages[page]
                    raw = data[offset:offset + 16] if data is not None else self.p.read(address, 16)
                else:
                    raw = self.p.read(address, 16)
                flags, _, cls = struct.unpack('<iiQ', raw)
                yield index, obj, flags, cls
            except OSError:
                continue

    def props(self, cls):
        classes = set()
        while cls and cls not in classes and len(classes) < 64:
            classes.add(cls)
            field, seen = self.u64(cls + 0x50), set()
            while field and field not in seen and len(seen) < 4096:
                seen.add(field)
                raw = self.p.read(field, 0x50)
                name = self.name(struct.unpack_from('<I', raw, 0x20)[0])
                type_ptr = struct.unpack_from('<Q', raw, 8)[0]
                kind = self.name(struct.unpack('<I', self.p.read(type_ptr, 4))[0])
                offset = struct.unpack_from('<i', raw, 0x44)[0]
                yield dict(name=name, kind=kind, offset=offset, field=field)
                field = struct.unpack_from('<Q', raw, 0x18)[0]
            cls = self.u64(cls + 0x40)

