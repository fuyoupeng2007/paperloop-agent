"""Protect an API key with the current Windows user's DPAPI identity."""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value: bytes, decrypt: bool) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('API 密钥保护目前仅支持 Windows。')
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.restype = wintypes.BOOL
    operation.argtypes = ([ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
                          if decrypt else [ctypes.POINTER(_Blob), wintypes.LPCWSTR,
                                           ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(_Blob)])
    source_buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source = _Blob(len(value), source_buffer)
    result = _Blob()
    if decrypt:
        ok = operation(ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(result))
    else:
        ok = operation(ctypes.byref(source), 'PaperLoop API key', None, None, None, 0x01, ctypes.byref(result))
    if not ok:
        raise OSError(ctypes.get_last_error(), '无法使用当前 Windows 用户保护或读取 API 密钥。')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def load(data: Path) -> str:
    path = data / 'api-key.dpapi'
    return _crypt(path.read_bytes(), True).decode('utf-8') if path.exists() else ''


def save(data: Path, key: str) -> None:
    data.mkdir(parents=True, exist_ok=True)
    path = data / 'api-key.dpapi'
    if not key:
        path.unlink(missing_ok=True)
        return
    temporary = data / 'api-key.dpapi.tmp'
    temporary.write_bytes(_crypt(key.encode('utf-8'), False))
    os.replace(temporary, path)
