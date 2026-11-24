"""Reversible runtime code patches for the no-render (DirectDraw) experiment.

Writes a tiny patch into the *test copy's already running game process* using
WriteProcessMemory and restores the original bytes on demand.  Only the two
analysis targets from disasm_analysis.py are supported:

  blit   GScreenClass::DoBlit   (0x4F4780) - final presentation/blit path
  render GScreenClass::Render   (0x4F4480) - whole per-frame draw dispatch

The patch replaces the first three bytes (the function prologue `83 EC xx`,
`sub esp, imm8`) with `C3 90 90` (`ret; nop; nop`).  The original bytes are
returned and can be written back; nothing is persisted to disk.

The target process is the game we launched for the experiment; this is not a
tool for the original game installation (E:\\hj is never touched).
"""
import argparse
import ctypes
import ctypes.wintypes as w
import hashlib
import json
import pathlib
import sys

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
PAGE_EXECUTE_READWRITE = 0x40

TARGETS = {
    # DoBlit/GScreenClass::Render are __fastcall/thiscall with stack args, so the
    # skip stub must clean the same argument bytes as the original epilogue
    # (DoBlit ends with `ret 4`, TacticalClass::Render with `ret 0xC`).
    "blit": {"va": 0x4F4780, "name": "GScreenClass::DoBlit",
             "expect": b"\x83\xec\x5c", "patch": b"\xc2\x04\x00"},
    "render": {"va": 0x4F4480, "name": "GScreenClass::Render",
               "expect": b"\x83\xec\x08", "patch": b"\xc3\x90\x90"},
    # battlefield/tactical drawing only; keeps UI draws, blit/present and any
    # frame pacing in the main loop intact (prologue: sub esp, 0xF4)
    "tactical": {"va": 0x6D3D10, "name": "TacticalClass::Render",
                 "expect": b"\x81\xec\xf4\x00\x00\x00",
                 "patch": b"\xc2\x0c\x00"},
}

kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.OpenProcess.restype = w.HANDLE
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.CloseHandle.restype = w.BOOL
kernel32.ReadProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = w.BOOL
kernel32.WriteProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.restype = w.BOOL
kernel32.VirtualProtectEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                      w.DWORD, ctypes.POINTER(w.DWORD)]
kernel32.VirtualProtectEx.restype = w.BOOL
kernel32.FlushInstructionCache.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
kernel32.FlushInstructionCache.restype = w.BOOL


class GameProcess:
    def __init__(self, pid):
        self.pid = int(pid)
        self.handle = kernel32.OpenProcess(
            PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE |
            PROCESS_QUERY_INFORMATION, False, self.pid)
        if not self.handle:
            raise OSError(f"OpenProcess({self.pid}) failed: {ctypes.get_last_error()}")

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def read(self, va, size):
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        if not kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(va), buf,
                                          size, ctypes.byref(read)):
            raise OSError(f"ReadProcessMemory(0x{va:x}) failed: "
                          f"{ctypes.get_last_error()}")
        return bytes(buf.raw[:read.value])

    def write(self, va, data):
        old = w.DWORD(0)
        if not kernel32.VirtualProtectEx(self.handle, ctypes.c_void_p(va), len(data),
                                         PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
            raise OSError(f"VirtualProtectEx failed: {ctypes.get_last_error()}")
        try:
            buf = ctypes.create_string_buffer(data, len(data))
            written = ctypes.c_size_t(0)
            if not kernel32.WriteProcessMemory(self.handle, ctypes.c_void_p(va), buf,
                                               len(data), ctypes.byref(written)):
                raise OSError(f"WriteProcessMemory(0x{va:x}) failed: "
                              f"{ctypes.get_last_error()}")
        finally:
            kernel32.VirtualProtectEx(self.handle, ctypes.c_void_p(va), len(data),
                                      old.value, ctypes.byref(w.DWORD(0)))
        kernel32.FlushInstructionCache(self.handle, ctypes.c_void_p(va), len(data))
        return written.value

    def verify(self, va, expect):
        return self.read(va, len(expect)) == expect


def apply_patch(pid, mode, state_path=None):
    """Patch one target; returns dict with original bytes (hex) for restore."""
    if mode not in TARGETS:
        raise ValueError(f"unknown patch mode {mode!r}")
    spec = TARGETS[mode]
    va = spec["va"]
    gp = GameProcess(pid)
    try:
        current = gp.read(va, 8)
        size = len(spec["patch"])
        info = {"mode": mode, "va": va, "name": spec["name"],
                "pid": int(pid), "bytes_before": current[:size].hex()}
        if current[:size] == spec["patch"]:
            info["already_patched"] = True
            return info
        if current[:len(spec["expect"])] != spec["expect"]:
            raise RuntimeError(
                f"unexpected prologue at 0x{va:x}: {current[:len(spec['expect'])].hex()} "
                f"(expected {spec['expect'].hex()}); refusing to patch")
        info["original_sha256"] = hashlib.sha256(current[:size]).hexdigest()
        written = gp.write(va, spec["patch"])
        info["bytes_written"] = written
        info["bytes_after"] = gp.read(va, 8).hex()
        info["verified"] = gp.read(va, size) == spec["patch"]
        info["state_path"] = str(state_path) if state_path else None
        if state_path:
            pathlib.Path(state_path).write_text(json.dumps(info, indent=2),
                                                encoding="utf-8")
        return info
    finally:
        gp.close()


def restore_patch(pid, mode, original_hex, state_path=None):
    spec = TARGETS[mode]
    original = bytes.fromhex(original_hex)
    size = len(spec["patch"])
    if len(original) < size:
        raise ValueError("original bytes too short")
    gp = GameProcess(pid)
    try:
        before = gp.read(spec["va"], 8)
        gp.write(spec["va"], original[:size])
        after = gp.read(spec["va"], 8)
        info = {"mode": mode, "va": spec["va"], "pid": int(pid),
                "bytes_before": before.hex(), "bytes_after": after.hex(),
                "restored": after[:size] == original[:size]}
        if state_path:
            pathlib.Path(state_path).write_text(json.dumps(info, indent=2),
                                                encoding="utf-8")
        return info
    finally:
        gp.close()


def read_target(pid, mode):
    spec = TARGETS[mode]
    gp = GameProcess(pid)
    try:
        return {"mode": mode, "va": spec["va"], "bytes": gp.read(spec["va"], 8).hex()}
    finally:
        gp.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--mode", choices=sorted(TARGETS) + ["all"], default=None)
    ap.add_argument("--restore", default=None,
                    help="JSON file written by a previous --mode run")
    ap.add_argument("--out-dir", default=None,
                    help="directory to save patch/restore evidence JSON")
    args = ap.parse_args()
    result = {}
    if args.restore:
        data = json.loads(pathlib.Path(args.restore).read_text(encoding="utf-8"))
        result["restore"] = restore_patch(args.pid, data["mode"], data["bytes_before"],
                                          state_path=None)
    elif args.mode:
        modes = sorted(TARGETS) if args.mode == "all" else [args.mode]
        out_dir = pathlib.Path(args.out_dir) if args.out_dir else None
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        patches = []
        for mode in modes:
            state_path = out_dir / f"patch-{mode}.json" if out_dir else None
            patches.append(apply_patch(args.pid, mode, state_path=state_path))
        result["patches"] = patches
    else:
        result["targets"] = {mode: read_target(args.pid, mode)
                             for mode in sorted(TARGETS)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
