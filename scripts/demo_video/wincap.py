"""Capture a single window (even if covered) to PNG using PrintWindow. Stdlib only."""

import ctypes
import struct
import zlib
from ctypes import wintypes

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]

# DWM extended frame bounds (window rect without invisible resize borders)
dwmapi = ctypes.WinDLL("dwmapi")


def _frame_rect(hwnd):
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def _png(path, w, h, bgra):
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        rgb = bytearray(w * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        raw += b"\x00" + rgb

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(chunk(b"IEND", b""))


def capture(hwnd, path):
    full = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(full))
    fr = _frame_rect(hwnd)
    w, h = full.right - full.left, full.bottom - full.top
    wdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(wdc)
    bmp = gdi32.CreateCompatibleBitmap(wdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    user32.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT
    hdr = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bi = ctypes.create_string_buffer(hdr, 44)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, bi, 0)
    gdi32.SelectObject(mdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, wdc)
    data = buf.raw
    # crop invisible borders
    x0, y0 = fr.left - full.left, fr.top - full.top
    cw, ch = fr.right - fr.left, fr.bottom - fr.top
    if (x0, y0, cw, ch) != (0, 0, w, h):
        rows = [data[(y0 + y) * w * 4 + x0 * 4:(y0 + y) * w * 4 + (x0 + cw) * 4] for y in range(ch)]
        data, w, h = b"".join(rows), cw, ch
    _png(path, w, h, data)
    return w, h


def find(title):
    return user32.FindWindowW(None, title)


def _read_png_rgb(path):
    """이 모듈이 쓴 PNG(8비트 RGB, 필터 0)를 다시 읽어 (너비, 높이, 줄별 RGB)."""
    data = open(path, "rb").read()
    w, h = struct.unpack(">II", data[16:24])
    idat, i = b"", 8
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        if data[i + 4:i + 8] == b"IDAT":
            idat += data[i + 8:i + 8 + n]
        i += 12 + n
    raw = zlib.decompress(idat)
    return w, h, [bytearray(raw[r * (w * 3 + 1) + 1:(r + 1) * (w * 3 + 1)]) for r in range(h)]


def process_windows(class_name, pid=None):
    """이 프로세스(기본)의 보이는 창 중 class_name인 것. 우클릭 메뉴는 '#32768'."""
    import os

    pid = pid or os.getpid()
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _l):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        if owner.value == pid and user32.IsWindowVisible(hwnd) and buf.value == class_name:
            found.append(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    return found


def capture_with(hwnd, extra_hwnds, path):
    """창 하나를 찍고, 그 위에 떠 있는 다른 창(우클릭 메뉴, 끌기 표시 등)을 화면 위치 그대로 겹쳐 그림.
    바탕화면이나 다른 프로그램 창은 담기지 않음."""
    import os
    import tempfile

    base_path = path
    w, h = capture(hwnd, base_path)
    bw, bh, base = _read_png_rgb(base_path)
    fr = _frame_rect(hwnd)
    for k, extra in enumerate(extra_hwnds):
        tmp = os.path.join(tempfile.gettempdir(), f"_wincap_extra_{k}.png")
        capture(extra, tmp)
        ew, eh, rows = _read_png_rgb(tmp)
        er = _frame_rect(extra)
        ox, oy = er.left - fr.left, er.top - fr.top
        for r in range(eh):
            ty = oy + r
            if 0 <= ty < bh:
                x0, x1 = max(0, ox), min(bw, ox + ew)
                if x1 > x0:
                    base[ty][x0 * 3:x1 * 3] = rows[r][(x0 - ox) * 3:(x1 - ox) * 3]
        os.remove(tmp)
    raw = b"".join(b"\x00" + bytes(r) for r in base)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", bw, bh, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))
    return w, h
