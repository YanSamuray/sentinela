"""Persist a local pseudonymization key with Windows user-scoped DPAPI.

Only ciphertext is written. The key cannot be recovered by copying this file
alone to a different account or machine. Losing the Windows DPAPI credentials
may prevent recovery; never silently replace an existing key.
"""
from __future__ import annotations

import ctypes
import os
import secrets
from pathlib import Path


class SecretError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise SecretError("windows_dpapi_required")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), source_buffer)
    target = Blob()
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; LOCAL_MACHINE deliberately not enabled.
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise SecretError("dpapi_operation_failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.memset(target.pbData, 0, target.cbData)
        kernel.LocalFree(target.pbData)
        ctypes.memset(source_buffer, 0, len(data))


def load_local_key(path: Path, *, create: bool = False) -> bytes:
    """Read a sealed 32-byte key, or exclusively create one when authorized."""
    path = Path(path)
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise SecretError("secret_path_link_forbidden")
    if not path.exists():
        if not create:
            raise SecretError("secret_missing")
        sealed = _dpapi(secrets.token_bytes(32), decrypt=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(b"TOTENS-DPAPI-V1\n" + sealed)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass  # Another creator won; always use the persisted key.
    raw = path.read_bytes()
    prefix = b"TOTENS-DPAPI-V1\n"
    if not raw.startswith(prefix):
        raise SecretError("invalid_secret_envelope")
    result = _dpapi(raw[len(prefix):], decrypt=True)
    if len(result) != 32:
        raise SecretError("invalid_secret_length")
    return result
