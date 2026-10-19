"""Publish images and file paths using the native Windows clipboard."""
import ctypes
from ctypes import wintypes
from io import BytesIO
import os
import struct
import threading
import time

from PIL import Image, UnidentifiedImageError

_lock = threading.Lock()


def clipboard_formats(paths):
    # DROPFILES followed by a double-null-terminated UTF-16 path list.
    formats = [(15, struct.pack('<IiiII', 20, 0, 0, 0, 1)
                + ('\0'.join(map(os.path.abspath, paths)) + '\0\0').encode('utf-16le'))]
    if len(paths) == 1:
        try:
            with Image.open(paths[0]) as source:
                output = BytesIO()
                source.convert('RGB').save(output, format='BMP')
                formats.append((8, output.getvalue()[14:]))  # CF_DIB excludes BMP header.
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            pass  # Other files can still be pasted into Explorer.
    return formats


def copy_files(paths):
    if os.name != 'nt':
        raise OSError('Image and file clipboard transfer requires Windows')
    formats = clipboard_formats(paths)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for library, name, args, result in [
        (kernel, 'GlobalAlloc', [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL),
        (kernel, 'GlobalLock', [wintypes.HGLOBAL], ctypes.c_void_p),
        (kernel, 'GlobalUnlock', [wintypes.HGLOBAL], wintypes.BOOL),
        (kernel, 'GlobalFree', [wintypes.HGLOBAL], wintypes.HGLOBAL),
        (user, 'CreateWindowExW', [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
         wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HWND),
        (user, 'DestroyWindow', [wintypes.HWND], wintypes.BOOL),
        (user, 'OpenClipboard', [wintypes.HWND], wintypes.BOOL),
        (user, 'EmptyClipboard', [], wintypes.BOOL),
        (user, 'CloseClipboard', [], wintypes.BOOL),
        (user, 'SetClipboardData', [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
    ]:
        function = getattr(library, name)
        function.argtypes, function.restype = args, result

    with _lock:
        window = user.CreateWindowExW(0, 'STATIC', 'QR AirDrop Clipboard', 0,
                                      0, 0, 0, 0, None, None, None, None)
        if not window:
            raise ctypes.WinError(ctypes.get_last_error())
        opened = False
        allocated = []
        try:
            # Allocate everything before replacing the existing clipboard.
            for kind, data in formats:
                handle = kernel.GlobalAlloc(2, len(data))
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                allocated.append([kind, handle])
                pointer = kernel.GlobalLock(handle)
                if not pointer:
                    raise ctypes.WinError(ctypes.get_last_error())
                ctypes.memmove(pointer, data, len(data))
                kernel.GlobalUnlock(handle)
            for attempt in range(10):
                opened = bool(user.OpenClipboard(window))
                if opened:
                    break
                time.sleep(0.05)
            if not opened:
                raise OSError('Windows clipboard is busy. Try again.')
            if not user.EmptyClipboard():
                raise ctypes.WinError(ctypes.get_last_error())
            for entry in allocated:
                if not user.SetClipboardData(*entry):
                    raise ctypes.WinError(ctypes.get_last_error())
                entry[1] = None  # Windows now owns this memory.
        finally:
            if opened:
                user.CloseClipboard()
            for _, handle in allocated:
                if handle:
                    kernel.GlobalFree(handle)
            user.DestroyWindow(window)
