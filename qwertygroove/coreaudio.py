"""Talks to macOS's audio system (CoreAudio) directly: lists the sound outputs,
tells which one is in use, and switches between them. No extra tools needed."""
import ctypes
import ctypes.util
import struct

_SYSTEM = 1                    # kAudioObjectSystemObject
_UTF8 = 0x08000100             # kCFStringEncodingUTF8


def _code(s):
    return struct.unpack(">I", s.encode())[0]


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


_ca = _cf = None


def _libs():
    """Load CoreAudio + CoreFoundation once. Returns False when not on a Mac."""
    global _ca, _cf
    if _ca is not None:
        return True
    ca_path, cf_path = ctypes.util.find_library("CoreAudio"), ctypes.util.find_library("CoreFoundation")
    if not ca_path or not cf_path:
        return False
    try:
        ca, cf = ctypes.cdll.LoadLibrary(ca_path), ctypes.cdll.LoadLibrary(cf_path)
        args = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p]
        ca.AudioObjectGetPropertyDataSize.argtypes = args + [ctypes.POINTER(ctypes.c_uint32)]
        ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        ca.AudioObjectGetPropertyData.argtypes = args + [ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        ca.AudioObjectSetPropertyData.argtypes = args + [ctypes.c_uint32, ctypes.c_void_p]
        ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
    except (OSError, AttributeError):
        return False
    _ca, _cf = ca, cf
    return True


def available():
    return _libs()


def _size(obj, selector, scope="glob"):
    addr, size = _Address(_code(selector), _code(scope), 0), ctypes.c_uint32(0)
    if _ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(addr), 0, None, ctypes.byref(size)) != 0:
        return 0
    return size.value


def _get_u32(obj, selector, scope="glob"):
    addr, value, size = _Address(_code(selector), _code(scope), 0), ctypes.c_uint32(0), ctypes.c_uint32(4)
    if _ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(value)) != 0:
        return None
    return value.value


def _name(dev):
    addr = _Address(_code("lnam"), _code("glob"), 0)
    ref, size = ctypes.c_void_p(), ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    if _ca.AudioObjectGetPropertyData(dev, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(ref)) != 0 \
            or not ref.value:
        return None
    buf = ctypes.create_string_buffer(512)
    ok = _cf.CFStringGetCString(ref, buf, 512, _UTF8)
    _cf.CFRelease(ref)
    return buf.value.decode("utf-8", "replace") if ok else None


def outputs():
    """[(device id, name, transport)] for every device that can play sound."""
    if not _libs():
        return []
    n = _size(_SYSTEM, "dev#") // 4
    if not n:
        return []
    ids = (ctypes.c_uint32 * n)()
    addr, size = _Address(_code("dev#"), _code("glob"), 0), ctypes.c_uint32(n * 4)
    if _ca.AudioObjectGetPropertyData(_SYSTEM, ctypes.byref(addr), 0, None, ctypes.byref(size), ids) != 0:
        return []
    found = []
    for dev in ids[: size.value // 4]:
        if _size(dev, "stm#", "outp") == 0:            # no output streams: a microphone etc.
            continue
        name = _name(dev)
        if name:
            found.append((dev, name, _get_u32(dev, "tran")))
    return found


def current_output():
    """Name of the output macOS is playing through right now, or None."""
    if not _libs():
        return None
    dev = _get_u32(_SYSTEM, "dOut")
    for d, name, _ in outputs():
        if d == dev:
            return name
    return None


def switch_output(name):
    """Make `name` the Mac's sound output. Returns True if it worked."""
    if not _libs():
        return False
    for dev, n, _ in outputs():
        if n == name:
            addr, value = _Address(_code("dOut"), _code("glob"), 0), ctypes.c_uint32(dev)
            return _ca.AudioObjectSetPropertyData(_SYSTEM, ctypes.byref(addr), 0, None, 4, ctypes.byref(value)) == 0
    return False


def is_bluetooth(name):
    """True/False for a Bluetooth output, None if it can't be told."""
    if not _libs():
        return None
    for _, n, transport in outputs():
        if n == name:
            return transport in (_code("blue"), _code("blea"))
    return None


def is_built_in(name):
    for _, n, transport in outputs():
        if n == name:
            return transport == _code("bltn")
    return False
