from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


CREDENTIAL_TARGET = "PNPSCADA Electricity Reading Tool"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CredentialError(RuntimeError):
    pass


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    def __init__(self, target: str = CREDENTIAL_TARGET) -> None:
        if sys.platform != "win32":
            raise CredentialError("Windows Credential Manager is only available on Windows")
        self.target = target
        self._api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._api.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        self._api.CredReadW.restype = wintypes.BOOL
        self._api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._api.CredWriteW.restype = wintypes.BOOL
        self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._api.CredDeleteW.restype = wintypes.BOOL
        self._api.CredFree.argtypes = [ctypes.c_void_p]

    def read(self) -> tuple[str, str] | None:
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self._api.CredReadW(self.target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == 1168:  # ERROR_NOT_FOUND
                return None
            raise CredentialError(f"Windows could not read the saved login (error {error})")
        try:
            credential = pointer.contents
            blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            password = blob.decode("utf-16-le")
            return credential.UserName or "", password
        finally:
            self._api.CredFree(pointer)

    def write(self, username: str, password: str) -> None:
        encoded = password.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(encoded)
        credential = _CREDENTIALW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = self.target
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.c_void_p)
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = username
        if not self._api.CredWriteW(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise CredentialError(f"Windows could not save the login (error {error})")

    def delete(self) -> None:
        if not self._api.CredDeleteW(self.target, CRED_TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error != 1168:
                raise CredentialError(f"Windows could not delete the saved login (error {error})")
