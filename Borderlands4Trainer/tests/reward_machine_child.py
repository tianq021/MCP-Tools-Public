"""Execute reward wrappers in this disposable process, never inside the game."""
import ctypes as C
from pathlib import Path
import struct
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from reward_hooks import wrapper, DATA, CASH_ID, ERIDIUM_ID
k = C.WinDLL('kernel32', use_last_error=True)
k.VirtualAlloc.restype = C.c_void_p
k.VirtualAlloc.argtypes = [C.c_void_p, C.c_size_t, C.c_ulong, C.c_ulong]
k.VirtualFree.argtypes = [C.c_void_p, C.c_size_t, C.c_ulong]
p = k.VirtualAlloc(None, 0x2000, 0x3000, 0x40)
if not p:
    raise C.WinError(C.get_last_error())
try:
    C.memmove(p + 0x800, bytes.fromhex('4c89c0c3'), 4)  # Return received R8.
    prototype = C.CFUNCTYPE(C.c_uint64, C.c_uint64, C.c_void_p, C.c_uint64, C.c_uint64)
    fn = prototype(p)
    def code(kind):
        data = wrapper(kind, p, p + 0x800)
        C.memmove(p, data, len(data))
    code('xp')
    C.c_uint64.from_address(p + DATA + 0x30).value = 0
    cases = 0
    for multiplier in (1, 2, 3, 4, 5, 7, 10, 100):
        C.c_uint64.from_address(p + DATA).value = multiplier
        for value in (0, 1, 313, 224301, 2**32, 2**63 - 1, 2**63, 2**64 - 1):
            assert fn(0, None, value, 0) == min(value * multiplier, 2**64 - 1)
            cases += 1
    assert fn(1, None, 123, 0) == 123  # Different player is not modified.
    # The original callback must still receive the original RDX.
    C.memmove(p + 0x800, bytes.fromhex('4889d0c3'), 4)
    assert fn(0, 12345, 999999, 0) == 12345
    C.memmove(p + 0x800, bytes.fromhex('4c89c0c3'), 4)
    code('currency')
    C.memset(p + DATA, 0, 32)
    def invoke(identifier, amount, high=0):
        currency = C.create_string_buffer(struct.pack('<QQ', identifier, high))
        return fn(0, C.addressof(currency), amount, 0)
    C.c_uint32.from_address(p + DATA).value = 100
    C.c_uint32.from_address(p + DATA + 0x10).value = 200
    assert invoke(999, 5) == 5
    assert invoke(CASH_ID, 5, 1) == 5
    assert invoke(CASH_ID, 0) == 0
    assert invoke(CASH_ID, 0xffffffff) == 0xffffffff
    assert C.c_uint32.from_address(p + DATA).value == 100
    assert invoke(CASH_ID, 102) == 202
    assert invoke(CASH_ID, 102) == 102
    assert invoke(ERIDIUM_ID, 7) == 207
    assert invoke(ERIDIUM_ID, 7) == 7
    assert tuple((C.c_uint32 * 3).from_address(p + DATA)) == (0, 1, 100)
    assert tuple((C.c_uint32 * 3).from_address(p + DATA + 0x10)) == (0, 1, 200)
    C.c_uint32.from_address(p + DATA).value = 100
    assert invoke(CASH_ID, 0x7ffffffe) == 0x7fffffff
    # Execute a relocated real-style prologue and its balanced epilogue.
    original = p + 0x500
    prefix = bytes.fromhex('41565657534881ec88000000488b05')
    prologue = prefix + struct.pack('<i', (p + 0x800) - (original + len(prefix) + 4))
    epilogue = bytes.fromhex('4c89c04881c4880000005b5f5e415ec3')
    C.memmove(original, prologue + epilogue, len(prologue + epilogue))
    actual = wrapper('xp', p, original, prologue)
    C.memmove(p, actual, len(actual))
    C.c_uint64.from_address(p + DATA).value = 5
    assert fn(0, None, 42, 0) == 210
    print(f'{cases} XP boundary cases; currency identity, one-shot, nonpositive and saturation cases passed')
finally:
    k.VirtualFree(p, 0, 0x8000)
