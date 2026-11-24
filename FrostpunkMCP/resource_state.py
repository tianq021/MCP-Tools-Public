"""Inventory layout of the hash-verified Steam build 20303936.

Evidence: research eligibility RVA 0x11C12B0 calls resource eligibility
0x15DD300 using the singleton at 0x3FEE5B8. Its 0x15DD160 helper looks up
the definition in system+0x2240 and compares int32(entry+8) with the cost.
Initialization at 0x15DD560 confirms count at +0x2248 and stride 0x70.
Definition+0 holds the resource's internal name pointer.
"""
import struct
from research_state import ResearchReadError

RESOURCE_SYSTEM_RVA = 0x3FEE5B8


def resource_state(reader):
    system = reader.pointer(reader.base + RESOURCE_SYSTEM_RVA)
    if not system:
        return None
    table, count = struct.unpack('<QI', reader.read(system + 0x2240, 12))
    if not table or not 0 < count <= 128:
        raise ResearchReadError('resource table is unavailable or its layout changed')
    raw = reader.read(table, count * 0x70)
    result = {}
    for n in range(count):
        definition, amount = struct.unpack_from('<Qi', raw, n * 0x70)
        name_address = reader.pointer(definition)
        name_raw = bytearray()
        while len(name_raw) < 192:
            address = name_address + len(name_raw)
            data = reader.read(address, min(192-len(name_raw), 4096-address % 4096))
            value, end, _ = data.partition(b'\0')
            name_raw.extend(value)
            if end:
                break
        else:
            raise ResearchReadError('resource name exceeds the verified layout')
        name = name_raw.decode('utf-8', errors='strict')
        if not name or name in result:
            raise ResearchReadError('invalid or duplicate resource definition')
        result[name] = amount
    if (system != reader.pointer(reader.base + RESOURCE_SYSTEM_RVA)
            or (table, count) != struct.unpack('<QI', reader.read(system + 0x2240, 12))
            or raw != reader.read(table, len(raw))):
        raise ResearchReadError('resource state changed during read; retry')
    return result
