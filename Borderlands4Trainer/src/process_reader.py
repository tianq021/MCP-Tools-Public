"""Windows x64 read-only process inspection; no write or injection APIs."""
import ctypes as C
from ctypes import wintypes as W
import os

if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
    raise RuntimeError('Requires 64-bit Windows Python')


class ProcessEntry(C.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                ('name', W.WCHAR * 260)]


class ModuleEntry(C.Structure):
    _fields_ = [('size', W.DWORD), ('id', W.DWORD), ('pid', W.DWORD),
                ('global_usage', W.DWORD), ('usage', W.DWORD),
                ('base', C.c_void_p), ('image_size', W.DWORD), ('module', W.HMODULE),
                ('name', W.WCHAR * 256), ('path', W.WCHAR * 260)]


class MemoryInfo(C.Structure):
    _fields_ = [('base', C.c_void_p), ('allocation', C.c_void_p),
                ('allocation_protect', W.DWORD), ('partition', W.WORD),
                ('region_size', C.c_size_t), ('state', W.DWORD),
                ('protect', W.DWORD), ('kind', W.DWORD)]


k = C.WinDLL('kernel32', use_last_error=True)


def bind(name, result, args):
    fn = getattr(k, name)
    fn.restype, fn.argtypes = result, args
    return fn


snapshot = bind('CreateToolhelp32Snapshot', W.HANDLE, [W.DWORD, W.DWORD])
close = bind('CloseHandle', W.BOOL, [W.HANDLE])
process_first = bind('Process32FirstW', W.BOOL, [W.HANDLE, C.POINTER(ProcessEntry)])
process_next = bind('Process32NextW', W.BOOL, [W.HANDLE, C.POINTER(ProcessEntry)])
module_first = bind('Module32FirstW', W.BOOL, [W.HANDLE, C.POINTER(ModuleEntry)])
module_next = bind('Module32NextW', W.BOOL, [W.HANDLE, C.POINTER(ModuleEntry)])
open_process = bind('OpenProcess', W.HANDLE, [W.DWORD, W.BOOL, W.DWORD])
image_name = bind('QueryFullProcessImageNameW', W.BOOL,
                  [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)])
times = bind('GetProcessTimes', W.BOOL, [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4)
query = bind('VirtualQueryEx', C.c_size_t,
             [W.HANDLE, C.c_void_p, C.POINTER(MemoryInfo), C.c_size_t])
read_memory = bind('ReadProcessMemory', W.BOOL,
                   [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)])
wait = bind('WaitForSingleObject', W.DWORD, [W.HANDLE, W.DWORD])


def entries(flags, pid, structure, first, next_entry):
    handle = snapshot(flags, pid)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        entry = structure()
        entry.size = C.sizeof(entry)
        ok = first(handle, C.byref(entry))
        while ok:
            yield entry
            ok = next_entry(handle, C.byref(entry))
        error = C.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES
            raise C.WinError(error)
    finally:
        close(handle)


def game_pids():
    return [e.pid for e in entries(2, 0, ProcessEntry, process_first, process_next)
            if e.name.casefold() == 'borderlands4.exe']


def modules(pid):
    try:
        return [dict(name=e.name, path=e.path, base=e.base, size=e.image_size, method='toolhelp')
                for e in entries(0x18, pid, ModuleEntry, module_first, module_next)]
    except OSError as original_error:
        # Alternative documented read-only API; no privilege changes or patching.
        enum = bind('K32EnumProcessModulesEx', W.BOOL,
                    [W.HANDLE, C.POINTER(W.HMODULE), W.DWORD, C.POINTER(W.DWORD), W.DWORD])
        filename = bind('K32GetModuleFileNameExW', W.DWORD,
                        [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD])
        class Info(C.Structure):
            _fields_ = [('base', C.c_void_p), ('size', W.DWORD), ('entry', C.c_void_p)]
        details = bind('K32GetModuleInformation', W.BOOL,
                       [W.HANDLE, W.HMODULE, C.POINTER(Info), W.DWORD])
        with Reader(pid) as reader:
            handles = (W.HMODULE * 4096)()
            needed = W.DWORD()
            if not enum(reader.handle, handles, C.sizeof(handles), C.byref(needed), 3):
                raise C.WinError(C.get_last_error())
            if needed.value > C.sizeof(handles):
                raise RuntimeError('Module list exceeded bounded buffer')
            result = []
            for handle in handles[:needed.value // C.sizeof(W.HMODULE)]:
                buf, info = C.create_unicode_buffer(32768), Info()
                if not filename(reader.handle, handle, buf, len(buf)):
                    raise C.WinError(C.get_last_error())
                if not details(reader.handle, handle, C.byref(info), C.sizeof(info)):
                    raise C.WinError(C.get_last_error())
                result.append(dict(name=os.path.basename(buf.value), path=buf.value,
                                   base=info.base, size=info.size, method='psapi',
                                   toolhelp_error=str(original_error)))
            return result


class Reader:
    def __init__(self, pid, expected_path=None):
        self.pid = pid
        # QUERY_INFORMATION | VM_READ | SYNCHRONIZE, never VM_WRITE.
        self.handle = open_process(0x100410, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        try:
            buf, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not image_name(self.handle, 0, buf, C.byref(size)):
                raise C.WinError(C.get_last_error())
            self.path = buf.value
            if expected_path and os.path.normcase(os.path.abspath(self.path)) != os.path.normcase(os.path.abspath(expected_path)):
                raise ValueError('Process executable path does not match configuration')
            created, exited, kernel, user = [W.FILETIME() for _ in range(4)]
            if not times(self.handle, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user)):
                raise C.WinError(C.get_last_error())
            self.created = (created.dwHighDateTime << 32) | created.dwLowDateTime
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            close(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def identity(self):
        return dict(pid=self.pid, created=self.created, path=self.path)

    def require_alive(self):
        if wait(self.handle, 0) != 258:  # WAIT_TIMEOUT
            raise RuntimeError('Target process exited or cannot be queried')

    def read(self, address, size):
        if not 0 < size <= 8 * 1024 * 1024:
            raise ValueError('Read size outside allowed range')
        buf, count = C.create_string_buffer(size), C.c_size_t()
        ok = read_memory(self.handle, address, buf, size, C.byref(count))
        if not ok or count.value != size:
            raise C.WinError(C.get_last_error())
        return buf.raw

    def regions(self):
        address = 0
        while address < 0x7FFFFFFFFFFF:
            self.require_alive()
            info = MemoryInfo()
            if not query(self.handle, address, C.byref(info), C.sizeof(info)):
                error = C.get_last_error()
                if error == 87:  # above target's maximum user address
                    break
                raise C.WinError(error)
            base = info.base or 0
            end = base + info.region_size
            if end <= address:
                raise RuntimeError('Invalid memory region size')
            # Deliberately restrict initial numeric research to committed writable data.
            if info.state == 0x1000 and not info.protect & 0x100 and info.protect & 0xFF in (4, 8, 0x40, 0x80):
                yield base, info.region_size
            address = end
