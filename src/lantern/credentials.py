"""Optional cloud credentials encrypted for the current Windows user with DPAPI."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise ValueError("Remembering cloud keys is supported only with Windows user encryption.")
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("The saved cloud key could not be protected or read for this Windows user.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class CredentialStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> str:
        if not self.path.is_file():
            return ""
        if self.path.stat().st_size > 65536:
            raise ValueError("The saved cloud key is invalid. Enter the key again.")
        return crypt(self.path.read_bytes(), decrypt=True).decode("utf-8")

    def save(self, value: str):
        encrypted = crypt(value.encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(".tmp")
        pending.write_bytes(encrypted)
        pending.replace(self.path)

    def forget(self):
        self.path.unlink(missing_ok=True)
