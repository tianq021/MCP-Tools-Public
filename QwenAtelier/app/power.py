"""Windows power sampling; no power plan or system setting is changed."""
import ctypes
from ctypes import wintypes
import os


class SystemPowerStatus(ctypes.Structure):
    _fields_ = [('ac', ctypes.c_ubyte), ('flags', ctypes.c_ubyte),
                ('percent', ctypes.c_ubyte), ('saver', ctypes.c_ubyte),
                ('remaining', wintypes.DWORD), ('full_lifetime', wintypes.DWORD)]


def interpret_power(ac, flags, percent):
    connected = {0: False, 1: True}.get(ac)
    present = None if flags == 255 else not bool(flags & 128)
    return {'supported': True, 'ac_connected': connected, 'battery_present': present,
            'battery_percent': percent if 0 <= percent <= 100 else None,
            'on_battery': connected is False and present is not False}


def power_snapshot():
    unknown = {'supported': False, 'ac_connected': None, 'battery_present': None,
               'battery_percent': None, 'on_battery': False}
    if os.name != 'nt':
        return unknown
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetSystemPowerStatus.argtypes = [ctypes.POINTER(SystemPowerStatus)]
        kernel.GetSystemPowerStatus.restype = wintypes.BOOL
        status = SystemPowerStatus()
        if kernel.GetSystemPowerStatus(ctypes.byref(status)):
            return interpret_power(status.ac, status.flags, status.percent)
    except (OSError, AttributeError):
        pass
    return unknown
