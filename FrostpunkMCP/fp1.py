"""Direct Lua bridge for the Windows Steam release of Frostpunk 1."""

import argparse
import csv
import ctypes
from ctypes import wintypes
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib

from research_state import ResearchReadError, ResearchReader


GAME_MAIN = 0x962DD588
ENTRY = struct.Struct("<IQQQB")
STOCK_MAIN = b"-- SCRIPTS LOADED IN EDITOR AND STANDALONE"
MARKER = b"-- fp1bridge request\n"
DEFAULT_GAME = Path(r"F:\SteamLibrary\steamapps\common\Frostpunk")
DEFAULT_PROJECT = Path(os.environ["APPDATA"]) / "11bitstudios" / "Frostpunk"


class BridgeError(Exception):
    pass


def atomic_write(path, data, *, replace=True):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        for attempt in range(10):
            try:
                if replace:
                    os.replace(temporary, path)
                else:
                    os.link(temporary, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.01)
    finally:
        Path(temporary).unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def hash_prefix(file, size):
    checksum = hashlib.sha256()
    remaining = size
    file.seek(0)
    while remaining:
        block = file.read(min(remaining, 1024 * 1024))
        if not block:
            raise BridgeError("common.dat is shorter than the backup records")
        checksum.update(block)
        remaining -= len(block)
    return checksum.hexdigest()


class Archive:
    def __init__(self, game):
        if (game / "steam_appid.txt").read_text().strip() != "323190":
            raise BridgeError("expected Frostpunk 1 Steam App ID 323190")
        self.idx = game / "common.idx"
        self.dat = game / "common.dat"
        self.backup = game / "common.idx.fp1bridge.json"
        self.index = self.idx.read_bytes()
        if len(self.index) < 11:
            raise BridgeError("invalid common.idx header")
        count, = struct.unpack_from("<I", self.index, 3)
        if len(self.index) != 11 + count * ENTRY.size:
            raise BridgeError("unsupported common.idx layout")
        for position in range(11, len(self.index), ENTRY.size):
            record = ENTRY.unpack_from(self.index, position)
            if record[0] == GAME_MAIN:
                self.position = position
                self.record = record
                break
        else:
            raise BridgeError("GameMain.lua is missing from common.idx")

    def script(self):
        _, stored, original, offset, compressed = self.record
        with self.dat.open("rb") as file:
            file.seek(offset)
            data = file.read(stored)
        result = zlib.decompressobj(31).decompress(data) if compressed else data
        if len(result) != original:
            raise BridgeError("GameMain.lua size mismatch")
        return result

    def replace_entry(self, entry):
        return self.index[:self.position] + entry + self.index[self.position + ENTRY.size:]

    def install(self, request):
        if self.backup.exists():
            raise BridgeError("backup already exists; check status or uninstall first")
        original = self.script()
        if original.strip() != STOCK_MAIN:
            raise BridgeError("GameMain.lua differs from the stock script")
        seed_request(request)
        source = original + b"\n" + Path(__file__).with_name("bridge.lua").read_bytes()
        encoder = zlib.compressobj(wbits=31)
        blob = (encoder.compress(source) + encoder.flush())[:-8]
        dat_size = self.dat.stat().st_size
        installed = self.replace_entry(ENTRY.pack(GAME_MAIN, len(blob), len(source), dat_size, 1))
        with self.dat.open("r+b") as file:
            backup = {
                "original_entry": ENTRY.pack(*self.record).hex(),
                "original_index_hash": digest(self.index),
                "patched_index_hash": digest(installed),
                "original_dat_size": dat_size,
                "original_dat_hash": hash_prefix(file, dat_size),
                "patch": blob.hex(),
            }
            atomic_write(self.backup, json.dumps(backup).encode("ascii"), replace=False)
            file.write(blob)
            file.flush()
            os.fsync(file.fileno())
        atomic_write(self.idx, installed)
        if Archive(self.idx.parent).script() != source:
            raise BridgeError("installed script verification failed; use uninstall to recover")

    def uninstall(self):
        backup = json.loads(self.backup.read_text(encoding="ascii"))
        original_index = self.replace_entry(bytes.fromhex(backup["original_entry"]))
        if digest(original_index) != backup["original_index_hash"] or digest(self.index) not in (
            backup["original_index_hash"], backup["patched_index_hash"]
        ):
            raise BridgeError("common.idx changed since installation")
        size = backup["original_dat_size"]
        blob = bytes.fromhex(backup["patch"])
        with self.dat.open("r+b") as file:
            if hash_prefix(file, size) != backup["original_dat_hash"]:
                raise BridgeError("original common.dat content changed since installation")
            tail = file.read(len(blob) + 1)
            if not blob.startswith(tail):
                raise BridgeError("common.dat has unexpected data after the original archive")
            atomic_write(self.idx, original_index)
            file.truncate(size)
            file.flush()
            os.fsync(file.fileno())
        self.backup.unlink()


def require_game_closed():
    output = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq Frostpunk.exe", "/FO", "CSV", "/NH"],
        text=True, errors="replace", timeout=5,
    )
    if any(row and row[0].lower() == "frostpunk.exe" for row in csv.reader(output.splitlines())):
        raise BridgeError("close Frostpunk before changing the archives")


def seed_request(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as file:
            file.write(MARKER)
    except FileExistsError:
        if not path.read_bytes().startswith(MARKER):
            raise BridgeError("request filename is already used by a different file")


class DebugGame:
    """Collect the game's own OutputDebugString replies without reading its locked log."""

    def __init__(self, game, close_on_exit):
        self.game = game
        self.close_on_exit = close_on_exit
        self.replies = queue.Queue()
        self.stop = threading.Event()
        self.started = threading.Event()
        self.process = None
        self.image_base = None
        self.failure = None
        self.last_exception = None
        self.thread = threading.Thread(target=self.run)

    def __enter__(self):
        if struct.calcsize("P") != 8:
            raise BridgeError("the Frostpunk output receiver requires 64-bit Python")
        require_game_closed()
        if b"FP1BRIDGE/1:ready" not in Archive(self.game).script():
            raise BridgeError("install the Lua bridge before starting the game")
        self.thread.start()
        self.started.wait()
        if self.failure:
            self.thread.join()
            raise self.failure
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()

    def run(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WaitForDebugEventEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
        kernel.ContinueDebugEvent.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
        kernel.DebugActiveProcess.argtypes = (wintypes.DWORD,)
        kernel.DebugActiveProcessStop.argtypes = (wintypes.DWORD,)
        kernel.DebugSetProcessKillOnExit.argtypes = (wintypes.BOOL,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.ReadProcessMemory.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
        )
        event = ctypes.create_string_buffer(176)  # DEBUG_EVENT, Windows x64
        attached = False
        try:
            self.process = subprocess.Popen(
                [str(self.game / "Frostpunk.exe"), "-devconsole", "-novideo"],
                cwd=self.game,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                code = self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            else:
                raise BridgeError(f"Frostpunk exited at startup (0x{code & 0xffffffff:08x})")
            attached = bool(kernel.DebugActiveProcess(self.process.pid))
            if not attached:
                raise ctypes.WinError(ctypes.get_last_error())
            kernel.DebugSetProcessKillOnExit(False)
            self.started.set()
            while not self.stop.is_set():
                if not kernel.WaitForDebugEventEx(event, 100):
                    if self.process.poll() is not None:
                        break
                    continue
                code, pid, tid = struct.unpack_from("<III", event)
                disposition = 0x00010002  # DBG_CONTINUE
                try:
                    if code == 1:
                        exception, = struct.unpack_from("<I", event, 16)
                        if not struct.unpack_from("<I", event, 168)[0]:
                            address, = struct.unpack_from("<Q", event, 32)
                            self.last_exception = {"code": hex(exception), "address": hex(address),
                                "rva": hex(address - self.image_base) if self.image_base else None}
                        # The MSVC thread-name notification is raised for the debugger.
                        if exception not in (0x80000003, 0x406D1388):
                            disposition = 0x80010001  # DBG_EXCEPTION_NOT_HANDLED
                    elif code in (3, 6):  # CREATE_PROCESS_DEBUG_EVENT, LOAD_DLL_DEBUG_EVENT
                        handle, = struct.unpack_from("<Q", event, 16)
                        if handle:
                            kernel.CloseHandle(handle)
                        if code == 3:
                            self.image_base, = struct.unpack_from("<Q", event, 40)
                    elif code == 8:  # OUTPUT_DEBUG_STRING_EVENT
                        pointer, wide, size = struct.unpack_from("<QHH", event, 16)
                        if pointer and 0 < size <= 65535:
                            buffer = ctypes.create_string_buffer(size * (2 if wide else 1))
                            if kernel.ReadProcessMemory(int(self.process._handle), pointer, buffer, len(buffer), None):
                                message = buffer.raw.decode("utf-16-le" if wide else "utf-8", errors="replace")
                                if "FP1BRIDGE/1:" in message:
                                    self.replies.put(message.encode("utf-8"))
                finally:
                    kernel.ContinueDebugEvent(pid, tid, disposition)
                if code == 5:  # EXIT_PROCESS_DEBUG_EVENT
                    break
        except (OSError, ValueError, BridgeError) as exc:
            self.failure = exc
        finally:
            self.started.set()
            if self.process and self.process.poll() is None:
                if attached:
                    kernel.DebugActiveProcessStop(self.process.pid)
                if self.close_on_exit:
                    self.process.terminate()
                    self.process.wait(timeout=10)


def action_call(request, game, action, timeout, session=""):
    if action not in ("state", "probe") or not isinstance(session, str) or not re.fullmatch(r"[a-zA-Z0-9]*", session):
        raise BridgeError("expected state or probe, with a valid session for probe")
    if action == "probe" and not session:
        raise BridgeError("request state first to obtain the current session")
    seed_request(request)
    with request.with_suffix(".lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BridgeError("another bridge call is in progress") from exc
        try:
            return exchange(request, game, action, timeout, session)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def exchange(request, game, action, timeout, session):
    request_id = uuid.uuid4().hex
    line = f'FP1Bridge.receive("{request_id}", "{action}", "{session}")\n'
    pattern = re.compile(rb"FP1BRIDGE/1:" + request_id.encode() + rb":(ok|error):([0-9a-f]+):")
    start = time.perf_counter()
    atomic_write(request, MARKER + line.encode("ascii"))
    try:
        while time.perf_counter() - start < timeout:
            if game.failure:
                raise BridgeError(f"game output receiver failed: {game.failure}")
            if not game.thread.is_alive():
                code = game.process.poll()
                raise BridgeError(f"Frostpunk output receiver stopped (exit code {code}, exception {game.last_exception})")
            try:
                message = game.replies.get(timeout=min(0.1, max(0, timeout - (time.perf_counter() - start))))
            except queue.Empty:
                continue
            match = pattern.search(message)
            if match:
                result = bytes.fromhex(match[2].decode()).decode("utf-8", errors="replace")
                if match[1] == b"error":
                    raise BridgeError(result)
                fields = dict(row.split("=", 1) for row in result.splitlines())
                for name in ("frame", "input_mode", "previous", "applied", "restored"):
                    if name in fields:
                        fields[name] = int(fields[name])
                for name in ("active", "loading"):
                    fields[name] = {"true": True, "false": False}[fields[name]]
                return {"action": action, "elapsed_ms": round((time.perf_counter() - start) * 1000, 2), **fields}
        raise BridgeError(f"no {action} reply in {timeout:g}s; action outcome is unknown")
    finally:
        atomic_write(request, MARKER)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "With Frostpunk closed, run install, then demo, then uninstall. "
            "For a persistent session, serve launches Frostpunk and accepts one JSON object per line on stdin. "
            'Use {"action":"state"} first, then {"action":"probe","session":"<returned session>"}. '
            'Use {"action":"research_state"} to read the technology catalog and current research. '
            "The probe changes an engine setting, reads it back, and restores its original value."
        ),
    )
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("command", choices=("status", "install", "uninstall", "demo", "research-state", "serve"))
    args = parser.parse_args()
    if not 0 < args.timeout <= 120:
        parser.error("timeout must be greater than 0 and at most 120 seconds")
    request = DEFAULT_PROJECT / "fp1bridge_request.lua"

    if args.command in ("status", "install", "uninstall"):
        archive = Archive(args.game_dir)
        if args.command != "status":
            require_game_closed()
        if args.command == "install":
            archive.install(request)
        elif args.command == "uninstall":
            archive.uninstall()
        archive = Archive(args.game_dir)
        source = archive.script()
        result = {"game": str(args.game_dir), "stock": source.strip() == STOCK_MAIN,
                  "bridge": b"FP1BRIDGE/1:ready" in source, "backup": archive.backup.exists()}
    else:
        with DebugGame(args.game_dir, close_on_exit=args.command != "serve") as game:
            if args.command == "demo":
                startup = action_call(request, game, "state", args.timeout)
                start = time.perf_counter()
                snapshot = action_call(request, game, "state", 5)
                probe = action_call(request, game, "probe", 5, snapshot["session"])
                result = {"startup": startup, "state": snapshot, "probe": probe,
                          "cycle_ms": round((time.perf_counter() - start) * 1000, 2)}
            elif args.command == "research-state":
                action_call(request, game, "state", args.timeout)
                result = ResearchReader(game).state()
            else:
                reader = None
                for line in sys.stdin:
                    try:
                        message = json.loads(line)
                        if not isinstance(message, dict):
                            raise BridgeError("expected a JSON object")
                        if message.get("action") == "research_state":
                            action_call(request, game, "state", args.timeout)
                            if reader is None:
                                reader = ResearchReader(game)
                            response = reader.state()
                        else:
                            response = action_call(request, game, message.get("action"), args.timeout,
                                                   message.get("session", ""))
                    except (OSError, ValueError, BridgeError, ResearchReadError) as exc:
                        response = {"error": str(exc)}
                    print(json.dumps(response), flush=True)
                return
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, zlib.error, BridgeError, ResearchReadError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))
