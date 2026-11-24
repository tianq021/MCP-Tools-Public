"""Production render-off mode: validated, reversible skip of the game draw dispatch.

This module is the safe wrapper used by the real sampling flow (collector /
training environment) and by the dual-instance stability harness.  It is built
on top of the experimentally verified patch in ``scripts/headless/render_patch.py``
(``GScreenClass::Render`` at VA 0x4F4480, original prologue ``83 EC 08``).

Safety rules implemented here (see reports/headless-feasibility.md):
  * only processes whose image is ``<E:\\RA2MCP>\\test-game*\\gamemd-spawn-ra2yrcpp.exe``
    may be patched - the original installation E:\\hj can never match this rule;
  * the executable SHA256 is checked against the copy's ``ra2mcp-instance.json``
    marker when present (mutex-patched copies differ), so a swapped/updated exe
    is refused instead of patched blindly;
  * the target function prologue must still equal the verified original bytes;
  * every write is read back and verified; the original bytes are remembered;
  * restore writes the original bytes back and verifies them; a failed restore
    raises :class:`RenderOffRestoreError` so the caller must terminate the
    process instead of handing it back to a human;
  * the window is hidden with ShowWindow(SW_HIDE) and kept hidden; restore shows
    it again;
  * a lightweight watchdog detects frame stalls, consecutive state-read failures
    and consecutive action failures and reports them to the caller.

Nothing in this module writes to the game directory or to disk executables.
"""
import ctypes
import ctypes.wintypes as w
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEADLESS = ROOT / "scripts" / "headless"
if str(HEADLESS) not in sys.path:
    sys.path.insert(0, str(HEADLESS))
if str(ROOT / "vendor" / "pyra2yr") not in sys.path:
    sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))

import render_patch  # noqa: E402
import winapi  # noqa: E402

EXE_NAME = "gamemd-spawn-ra2yrcpp.exe"
PATCH_MODE = "render"
EXPECTED_EXE_SUFFIX = "\\test-game"  # test-game, test-game-1, test-game-headless*


class RenderOffError(RuntimeError):
    """Render-off mode cannot be (safely) enabled; caller must not start it."""


class RenderOffRestoreError(RuntimeError):
    """Patch restore failed; the game process must be terminated, not reused."""


def _process_image_path(pid):
    import ctypes.wintypes as wt
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        size = wt.DWORD(2048)
        buf = ctypes.create_unicode_buffer(2048)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        kernel32.CloseHandle(handle)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_target_process(pid):
    """Return an evidence dict; raise RenderOffError when the PID is not a copy.

    A render-off patch may only ever be applied to a game process launched from
    an isolated test copy inside this workspace.  The original installation
    ``E:\\hj`` is outside ``E:\\RA2MCP`` and is therefore always rejected.
    """
    pid = int(pid)
    evidence = {"pid": pid, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not winapi.is_process_alive(pid):
        raise RenderOffError(f"process {pid} is not alive")
    path = _process_image_path(pid)
    if not path:
        raise RenderOffError(f"cannot query image path of pid {pid}")
    p = pathlib.Path(path)
    evidence["exe_path"] = str(p)
    if p.name.lower() != EXE_NAME:
        raise RenderOffError(f"pid {pid} image is {p.name}, not {EXE_NAME}")
    game_dir = p.parent.resolve()
    evidence["game_dir"] = str(game_dir)
    try:
        game_dir.relative_to(ROOT.resolve())
    except ValueError:
        raise RenderOffError(
            f"pid {pid} runs from {game_dir}, outside the workspace; refusing") from None
    if not game_dir.name.lower().startswith("test-game"):
        raise RenderOffError(
            f"pid {pid} game dir '{game_dir.name}' is not a test-game copy")
    # the copy's own patch marker pins the expected exe hash (mutex rename differs
    # per copy); an executable swap/update is refused instead of patched blindly
    marker = game_dir / "ra2mcp-instance.json"
    actual = sha256_file(p)
    evidence["exe_sha256"] = actual
    if marker.exists():
        try:
            known = json.loads(marker.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            raise RenderOffError(f"bad instance marker {marker}: {exc}") from None
        expected = known.get("patched_sha256")
        evidence["expected_exe_sha256"] = expected
        if expected and actual.lower() != str(expected).lower():
            raise RenderOffError(
                f"exe hash mismatch for copy {game_dir.name}: {actual[:16]}... != "
                f"{str(expected)[:16]}...; refusing render-off")
    evidence["validated"] = True
    return evidence


def pe_image_info(path):
    """Parse DOS/PE headers and return (data, image_base, sections) without deps."""
    import struct
    path = pathlib.Path(path)
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise RenderOffError(f"{path} is not a PE image")
    if len(data) < 0x40:
        raise RenderOffError(f"{path} is too short to be a PE image")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise RenderOffError(f"{path} has no PE signature")
    coff = e_lfanew + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt_off = coff + 20
    magic = struct.unpack_from("<H", data, opt_off)[0]
    if magic == 0x10B:
        image_base = struct.unpack_from("<I", data, opt_off + 28)[0]
    elif magic == 0x20B:
        image_base = struct.unpack_from("<Q", data, opt_off + 24)[0]
    else:
        raise RenderOffError(f"{path} has unknown optional header magic {magic:#x}")
    sections = []
    sec_off = opt_off + opt_size
    for i in range(num_sections):
        base = sec_off + i * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((vaddr, max(vsize, rawsize), rawptr))
    return data, image_base, sections


def pe_file_offset(path, va):
    """Map an absolute VA of the PE image to its file offset."""
    data, image_base, sections = pe_image_info(path)
    rva = int(va) - int(image_base)
    for vaddr, span, rawptr in sections:
        if vaddr <= rva < vaddr + span:
            offset = rawptr + (rva - vaddr)
            if offset >= len(data):
                raise RenderOffError(f"VA 0x{va:x} file offset outside {path}")
            return offset
    raise RenderOffError(f"VA 0x{va:x} not mapped in any section of {path}")


def _read_pe_bytes(path, va, size):
    """Read `size` bytes at absolute VA from an on-disk PE image (no deps)."""
    data, _image_base, _sections = pe_image_info(path)
    offset = pe_file_offset(path, va)
    chunk = data[offset:offset + size]
    if len(chunk) != size:
        raise RenderOffError(f"short read at VA 0x{va:x} in {path}")
    return chunk


def verify_disk_prologue(game_dir):
    """Check the patch target bytes of the copy's on-disk executable."""
    path = pathlib.Path(game_dir) / EXE_NAME
    if not path.exists():
        raise RenderOffError(f"game executable missing: {path}")
    spec = render_patch.TARGETS[PATCH_MODE]
    current = _read_pe_bytes(path, spec["va"], len(spec["expect"]))
    if current != spec["expect"]:
        raise RenderOffError(
            f"unexpected on-disk prologue at 0x{spec['va']:x}: {current.hex()} "
            f"(expected {spec['expect'].hex()})")
    return {"exe": str(path), "va": spec["va"],
            "prologue_hex": current.hex(), "patch_hex": spec["patch"].hex()}


class RenderOffController:
    """One game process / one match: hide window, patch, watch, restore."""

    def __init__(self, pid, evidence_dir=None, stall_seconds=8.0,
                 read_fail_limit=5, action_fail_limit=10):
        self.pid = int(pid)
        self.evidence_dir = pathlib.Path(evidence_dir) if evidence_dir else None
        self.stall_seconds = float(stall_seconds)
        self.read_fail_limit = int(read_fail_limit)
        self.action_fail_limit = int(action_fail_limit)
        self.patch = None
        self.window = None
        self.evidence = {"pid": self.pid,
                         "events": [],
                         "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._frame = None
        self._frame_at = None
        self._read_fail_streak = 0
        self._action_fail_streak = 0
        self._last_integrity_check = 0.0
        self._patch_checks = 0

    # ---- evidence helpers ----
    def _event(self, kind, **data):
        entry = {"kind": kind, "t": round(time.time(), 3), **data}
        self.evidence["events"].append(entry)
        if self.evidence_dir:
            try:
                self.evidence_dir.mkdir(parents=True, exist_ok=True)
                with open(self.evidence_dir / "render-off-events.jsonl", "a",
                          encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return entry

    def _write_evidence(self):
        if not self.evidence_dir:
            return
        try:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            (self.evidence_dir / "render-off-state.json").write_text(
                json.dumps(self.evidence, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8")
        except OSError:
            pass

    # ---- window ----
    def find_window(self, timeout_s=60.0):
        win = winapi.pick_game_window(self.pid)
        if win is None:
            # 多实例同时重新开局时，窗口创建会滞后；等待而不是立即失败
            # （2026-10-30：14 实例批量重开曾因单点判定直接触发 RenderOffError，
            #  进而导致 vector env 全量重建）。
            win = winapi.wait_for_window(self.pid, timeout_s=timeout_s)
        if win is None:
            raise RenderOffError(f"no game window found for pid {self.pid}")
        self.window = win
        return win

    def hide_window(self):
        win = self.window or self.find_window()
        winapi.hide_window(win["hwnd"])
        visible = winapi.is_window_visible(win["hwnd"])
        self.window = win
        self._event("window_hide", hwnd=win["hwnd"], visible_after=visible)
        if visible:
            raise RenderOffError(f"ShowWindow(SW_HIDE) failed for hwnd {win['hwnd']}")
        return win

    def show_window(self):
        if self.window is None:
            try:
                self.find_window()
            except RenderOffError:
                return None
        winapi.show_window(self.window["hwnd"])
        visible = winapi.is_window_visible(self.window["hwnd"])
        self._event("window_show", hwnd=self.window["hwnd"], visible_after=visible)
        return visible

    # ---- patch ----
    def enable(self):
        if self.patch is not None:
            raise RenderOffError("render-off already enabled for this process")
        target = validate_target_process(self.pid)
        self.evidence["target"] = target
        prologue = verify_disk_prologue(target["game_dir"])
        self.evidence["disk_prologue"] = prologue
        state_path = (self.evidence_dir / "render-off-patch.json"
                      if self.evidence_dir else None)
        try:
            info = render_patch.apply_patch(self.pid, PATCH_MODE,
                                            state_path=state_path)
        except Exception as exc:  # noqa: BLE001
            self._event("patch_failed", error=f"{type(exc).__name__}: {exc}")
            self._write_evidence()
            raise RenderOffError(f"patch apply failed: {exc}") from None
        if not info.get("verified"):
            self._event("patch_unverified", info=info)
            self._write_evidence()
            raise RenderOffError("patch write was not verified")
        self.patch = info
        self.evidence["patch"] = info
        self._event("patch_applied", va=info["va"], mode=PATCH_MODE,
                    bytes_after=info["bytes_after"])
        self._write_evidence()
        return info

    def disable(self):
        if self.patch is None:
            return {"restored": True, "note": "no patch to restore"}
        state_path = (self.evidence_dir / "render-off-restore.json"
                      if self.evidence_dir else None)
        try:
            info = render_patch.restore_patch(
                self.pid, PATCH_MODE, self.patch["bytes_before"],
                state_path=state_path)
        except Exception as exc:  # noqa: BLE001
            self._event("restore_failed", error=f"{type(exc).__name__}: {exc}")
            self._write_evidence()
            self.evidence["restore"] = {"restored": False,
                                        "error": f"{type(exc).__name__}: {exc}"}
            raise RenderOffRestoreError(f"patch restore failed: {exc}") from None
        self.evidence["restore"] = info
        self.patch = None
        if not info.get("restored"):
            self._event("restore_mismatch", info=info)
            self._write_evidence()
            raise RenderOffRestoreError(
                f"restore readback mismatch: {info.get('bytes_after')}")
        self._event("patch_restored", bytes_after=info["bytes_after"])
        self._write_evidence()
        return info

    def process_exited(self, exit_code):
        """There is no address space left to restore; retain that evidence."""
        self._event('process_exited', exit_code=exit_code,
                    note='runtime patch disappeared with the exited process')
        self.evidence['restore'] = {'restored': False, 'not_required': True,
                                    'process_exited': True, 'exit_code': exit_code}
        self.patch = None
        self._write_evidence()

    def assert_patch_intact(self):
        """Periodic integrity check while the match runs."""
        if self.patch is None:
            raise RenderOffError("render-off patch is not enabled")
        target = render_patch.read_target(self.pid, PATCH_MODE)
        self._patch_checks += 1
        self._last_integrity_check = time.time()
        expected = render_patch.TARGETS[PATCH_MODE]["patch"].hex()
        if not target["bytes"].startswith(expected):
            self._event("patch_lost", bytes=target["bytes"], expected=expected)
            self._write_evidence()
            raise RenderOffError(
                f"render-off patch lost at 0x{target['va']:x}: {target['bytes']}")
        return target

    # ---- health watchdog ----
    def note_state(self, frame):
        now = time.time()
        if self._frame is not None and int(frame) == int(self._frame):
            stalled = now - self._frame_at
        else:
            stalled = 0.0
            self._frame_at = now
        self._frame = int(frame)
        if stalled > self.stall_seconds:
            return ("frame_stall", round(stalled, 2))
        return None

    def note_read(self, ok):
        if ok:
            self._read_fail_streak = 0
            return None
        self._read_fail_streak += 1
        if self._read_fail_streak >= self.read_fail_limit:
            return ("read_fail_streak", self._read_fail_streak)
        return None

    def note_actions(self, ok_count, invalid_count):
        if invalid_count <= 0:
            self._action_fail_streak = 0
            return None
        self._action_fail_streak += int(invalid_count)
        if self._action_fail_streak >= self.action_fail_limit:
            return ("action_fail_streak", self._action_fail_streak)
        return None

    def check_health(self, frame, read_ok, ok_count=0, invalid_count=0):
        violations = []
        for result in (self.note_state(frame), self.note_read(read_ok),
                       self.note_actions(ok_count, invalid_count)):
            if result:
                violations.append(result)
        return violations

    # ---- lifecycle ----
    def summary(self):
        return {"pid": self.pid, "window": self.window,
                "patch": self.patch, "restore": self.evidence.get("restore"),
                "target": self.evidence.get("target"),
                "disk_prologue": self.evidence.get("disk_prologue"),
                "events": self.evidence["events"],
                "patch_check_count": self._patch_checks}
