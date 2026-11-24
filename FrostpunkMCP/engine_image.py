"""Offline image reader for analysis of the hash-verified Frostpunk executable."""
import hashlib
import struct
from fp1 import DEFAULT_GAME
from research_state import BUILD_SHA256


class EngineImage:
    def __init__(self):
        self.data = (DEFAULT_GAME / 'Frostpunk.exe').read_bytes()
        if hashlib.sha256(self.data).hexdigest() != BUILD_SHA256:
            raise RuntimeError('unsupported executable build')
        self.base = 0
        pe = struct.unpack_from('<I', self.data, 0x3c)[0]
        count = struct.unpack_from('<H', self.data, pe+6)[0]
        optional = struct.unpack_from('<H', self.data, pe+20)[0]
        self.sections = []
        for n in range(count):
            p = pe+24+optional+40*n
            _, rva, size, raw = struct.unpack_from('<IIII', self.data, p+8)
            self.sections.append((rva, size, raw))

    def read(self, rva, size):
        for start, length, raw in self.sections:
            if start <= rva and rva+size <= start+length:
                return self.data[raw+rva-start:raw+rva-start+size]
        raise ValueError('range is not stored in the image')
