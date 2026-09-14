"""Windows Generic Credential storage. No enumeration, files or network fallback.

Only application startup may consume read(); never expose it through the native bridge.
Tests must supply a fresh PaperLens.Test.<random>/Hy3 target, never the default.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from pydantic import ValidationError
from backend.app.models import DesktopSettingsRequest


class CredentialError(RuntimeError):
    def __init__(self, code='DESKTOP_CREDENTIAL_UNAVAILABLE', *, clearing=False):
        super().__init__(code)
        self.code = code
        self.clearing = clearing

    def envelope(self):
        return {'ok': False, 'error': {
            'error_code': self.code,
            'message': ('设置输入无效。' if self.code == 'DESKTOP_SETTINGS_INVALID'
                        else '清除 Key 失败，请重试。' if self.clearing
                        else '凭据存储不可用，请重试。'),
            'retryable': self.code == 'DESKTOP_CREDENTIAL_UNAVAILABLE',
        }}


class _Credential(ctypes.Structure):
    _fields_ = [
        ('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
        ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
        ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
        ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD),
        ('Attributes', ctypes.c_void_p), ('TargetAlias', wintypes.LPWSTR),
        ('UserName', wintypes.LPWSTR),
    ]


class CredentialStore:
    def __init__(self, *, target='PaperLens/Hy3'):
        # target is internal dependency injection, never a bridge argument.
        if target != 'PaperLens/Hy3' and not (
            target.startswith('PaperLens.Test.') and target.endswith('/Hy3')
            and len(target) > len('PaperLens.Test./Hy3') and '\x00' not in target
        ):
            raise CredentialError('DESKTOP_SETTINGS_INVALID')
        self._target = target
        try:
            self._api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
            pointer = ctypes.POINTER(_Credential)
            self._api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer)]
            self._api.CredReadW.restype = wintypes.BOOL
            self._api.CredWriteW.argtypes = [pointer, wintypes.DWORD]
            self._api.CredWriteW.restype = wintypes.BOOL
            self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            self._api.CredDeleteW.restype = wintypes.BOOL
            self._api.CredFree.argtypes = [ctypes.c_void_p]
            self._api.CredFree.restype = None
        except (AttributeError, OSError):
            raise CredentialError() from None

    def read(self) -> str | None:
        pointer = ctypes.POINTER(_Credential)()
        if not self._api.CredReadW(self._target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return None
            raise CredentialError()
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or not 0 < credential.CredentialBlobSize <= 2560:
                raise CredentialError()
            value = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode('utf-8')
            # Reject malformed stored blobs rather than treating them as absent.
            DesktopSettingsRequest(mode='live', api_key=value)
            return value
        except (UnicodeError, ValidationError):
            raise CredentialError() from None
        finally:
            self._api.CredFree(pointer)

    def configured(self) -> bool:
        return self.read() is not None

    def write(self, key: str) -> None:
        try:
            validated = DesktopSettingsRequest(mode='live', api_key=key)
        except ValidationError:
            raise CredentialError('DESKTOP_SETTINGS_INVALID') from None
        payload = validated.api_key.get_secret_value().encode('utf-8')
        blob = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        credential = _Credential(Type=1, TargetName=self._target,
                                 CredentialBlobSize=len(payload), CredentialBlob=blob,
                                 Persist=2, UserName='PaperLens')
        try:
            if not self._api.CredWriteW(ctypes.byref(credential), 0):
                raise CredentialError()
        finally:
            ctypes.memset(blob, 0, len(payload))

    def clear(self) -> None:
        if not self._api.CredDeleteW(self._target, 1, 0) and ctypes.get_last_error() != 1168:
            raise CredentialError(clearing=True)
