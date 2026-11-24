"""Read the technology catalog and active research on the verified Steam build.

In this build the technology panel calls RVA 0x11C3E60 with the technology
system, the selected definition, and a null error collector. That routine
calls the eligibility check at 0x11C12B0 before writing the active technology
and submitting its cost to the resource system.
"""

import ctypes
from ctypes import wintypes
import hashlib
import struct


BUILD_SHA256 = "031dbfe529256b7bf532aa11bfaf8ef75ef0618aa0492a970e532566342ba19d"
TECHNOLOGY_SYSTEM_RVA = 0x3FBFA58
TECHNOLOGY_CATALOG_RVA = 0x3FD13B0
WORKSHOP_COUNT_OFFSET = 0x2068
ACTIVE_TECHNOLOGY_OFFSET = 0x2260
TECHNOLOGY_NAME_OFFSET = 0x18


class ResearchReadError(Exception):
    pass


class ResearchReader:
    def __init__(self, game):
        executable = game.game / "Frostpunk.exe"
        if hashlib.sha256(executable.read_bytes()).hexdigest() != BUILD_SHA256:
            raise ResearchReadError("technology offsets are verified only for Steam build 20303936")
        if not game.image_base:
            raise ResearchReadError("the game executable base has not been observed")
        self.base = game.image_base
        self.handle = int(game.process._handle)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.read_memory = kernel.ReadProcessMemory
        self.read_memory.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        )

    def read(self, address, size):
        if not address or size <= 0:
            raise ResearchReadError("invalid research memory range")
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        if not self.read_memory(self.handle, address, buffer, size, ctypes.byref(count)) or count.value != size:
            raise ResearchReadError("game memory changed or could not be read")
        return buffer.raw

    def pointer(self, address):
        return struct.unpack("<Q", self.read(address, 8))[0]

    def catalog(self):
        table, count = struct.unpack("<QI", self.read(self.base + TECHNOLOGY_CATALOG_RVA, 12))
        if not table or not 0 < count <= 2048:
            raise ResearchReadError("technology catalog has not been initialized")
        return table, count

    def name(self, technology):
        name = self.pointer(technology + TECHNOLOGY_NAME_OFFSET)
        if not name:
            raise ResearchReadError("technology has no catalog name")
        size = min(192, 4096 - (name % 4096))
        data = self.read(name, size)
        if b"\0" not in data and size < 192:
            data += self.read(name + size, 192 - size)
        raw, separator, _ = data.partition(b"\0")
        if not raw or not separator:
            raise ResearchReadError("invalid technology name in catalog")
        return raw.decode("utf-8", errors="replace")

    def state(self):
        system = self.pointer(self.base + TECHNOLOGY_SYSTEM_RVA)
        table, count = self.catalog()
        pointers = struct.unpack(f"<{count}Q", self.read(table, count * 8))
        definitions = [self.name(technology) for technology in pointers]
        workshops = None
        active = None
        if system:
            workshops, = struct.unpack("<I", self.read(system + WORKSHOP_COUNT_OFFSET, 4))
            current = self.pointer(system + ACTIVE_TECHNOLOGY_OFFSET)
            if current:
                if current not in pointers:
                    raise ResearchReadError("current research is outside the technology catalog")
                active = self.name(current)
        if system != self.pointer(self.base + TECHNOLOGY_SYSTEM_RVA) or (table, count) != self.catalog():
            raise ResearchReadError("research state changed during the read; retry")
        return {
            "city_research_initialized": bool(system),
            "registered_research_buildings": workshops,
            "active_technology": active,
            "catalog_count": count,
            "catalog": definitions,
        }
