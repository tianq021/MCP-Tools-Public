"""Release only this session's journalled inputs if its worker process dies."""
import ctypes as C
import json
from pathlib import Path
import sys
import time

from native import Windows, Input, Payload, Mouse, Keyboard, MUTEX, DATA

JOURNAL = DATA / "active-input.json"


def recover(win, session=None):
    if not JOURNAL.exists():
        return
    record = json.loads(JOURNAL.read_text(encoding="utf-8"))
    if session is not None and record["session"] != session:
        return
    handle = win.k.OpenProcess(0x100000, False, record["pid"])
    if handle:
        try:
            if win.k.WaitForSingleObject(handle, 0) == 0x102:
                return
        finally:
            win.k.CloseHandle(handle)
    # Caller owns the same desktop mutex as the input controller.
    for row in record["keys"]:
        event = Input(type=1, data=Payload(key=Keyboard(scan=row["scan"], flags=row["flags"] | 2)))
        if win.u.SendInput(1, C.byref(event), C.sizeof(Input)) != 1:
            raise RuntimeError("Watchdog could not release a key.")
    for up in record["buttons"]:
        event = Input(type=0, data=Payload(mouse=Mouse(flags=up)))
        if win.u.SendInput(1, C.byref(event), C.sizeof(Input)) != 1:
            raise RuntimeError("Watchdog could not release a mouse button.")
    JOURNAL.unlink(missing_ok=True)
    (DATA / "last-recovery.json").write_text(json.dumps({
        "session": record["session"], "keys": len(record["keys"]),
        "buttons": len(record["buttons"]), "recovered": True,
    }), encoding="utf-8")


def main():
    session = sys.argv[1]
    win = Windows()
    mutex = win.k.CreateMutexW(None, False, MUTEX)
    if not mutex:
        return 1
    print("ready", flush=True)
    try:
        while JOURNAL.exists():
            try:
                record = json.loads(JOURNAL.read_text(encoding="utf-8"))
                if record["session"] != session:
                    return 0
                result = win.k.WaitForSingleObject(mutex, 0)
                if result in (0, 0x80):
                    try:
                        recover(win, session)
                    finally:
                        win.k.ReleaseMutex(mutex)
            except (OSError, ValueError):
                pass
            time.sleep(0.025)
    finally:
        win.k.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
