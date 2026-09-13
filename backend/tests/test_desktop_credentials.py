from uuid import uuid4
import ctypes
import pytest
from backend.app import desktop_credentials as credentials


def test_native_synthetic_roundtrip_clear_and_repeat():
    target = 'PaperLens.Test.' + uuid4().hex + '/Hy3'
    store = credentials.CredentialStore(target=target)
    try:
        assert store.read() is None
        assert store.configured() is False
        store.write('synthetic-only-' + target)
        assert store.read() == 'synthetic-only-' + target
        assert store.configured() is True
        store.write('synthetic-replacement')
        assert store.read() == 'synthetic-replacement'
        store.clear()
        assert store.configured() is False
        store.clear()
        assert credentials.CredentialStore(target=target).read() is None
    finally:
        store.clear()


@pytest.mark.parametrize('operation', ['read', 'write', 'clear'])
def test_native_failure_is_safe_and_preserves_synthetic_entry(monkeypatch, operation):
    target = 'PaperLens.Test.' + uuid4().hex + '/Hy3'
    store = credentials.CredentialStore(target=target)
    store.write('synthetic-old')
    try:
        with monkeypatch.context() as patch:
            def fail(*args):
                ctypes.set_last_error(5)
                return 0
            patch.setattr(store._api, {'read':'CredReadW', 'write':'CredWriteW', 'clear':'CredDeleteW'}[operation], fail)
            with pytest.raises(credentials.CredentialError) as caught:
                getattr(store, operation)(*(['synthetic-new'] if operation == 'write' else []))
            assert str(caught.value) == 'DESKTOP_CREDENTIAL_UNAVAILABLE'
            assert caught.value.envelope() == {'ok': False, 'error': {
                'error_code': 'DESKTOP_CREDENTIAL_UNAVAILABLE',
                'message': ('清除 Key 失败，请重试。' if operation == 'clear' else '凭据存储不可用，请重试。'), 'retryable': True}}
            if operation == 'read':
                with pytest.raises(credentials.CredentialError):
                    store.configured()
        assert store.read() == 'synthetic-old'
    finally:
        store.clear()


@pytest.mark.parametrize('key', ['', '   ', 'x'*2561, '\x00', 123])
def test_invalid_key_never_calls_native_write(monkeypatch, key):
    store = credentials.CredentialStore(target='PaperLens.Test.' + uuid4().hex + '/Hy3')
    def forbidden(*args):
        pytest.fail('invalid key reached native write')
    monkeypatch.setattr(store._api, 'CredWriteW', forbidden)
    with pytest.raises(credentials.CredentialError) as caught:
        store.write(key)
    assert str(caught.value) == 'DESKTOP_SETTINGS_INVALID'


def test_read_frees_native_allocation(monkeypatch):
    target = 'PaperLens.Test.' + uuid4().hex + '/Hy3'
    store = credentials.CredentialStore(target=target)
    store.write('synthetic-only')
    free = store._api.CredFree
    calls = []
    def observed(pointer):
        calls.append(True)
        free(pointer)
    try:
        monkeypatch.setattr(store._api, 'CredFree', observed)
        assert store.read() == 'synthetic-only'
        assert calls == [True]
    finally:
        store.clear()


def test_corrupt_blob_fails_without_false_status_and_is_freed(monkeypatch):
    store = credentials.CredentialStore(target='PaperLens.Test.' + uuid4().hex + '/Hy3')
    blob = (ctypes.c_ubyte * 1)(255)
    record = credentials._Credential(CredentialBlobSize=1, CredentialBlob=blob)
    freed = []
    def read(target, kind, flags, output):
        ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(credentials._Credential)))[0] = ctypes.pointer(record)
        return 1
    monkeypatch.setattr(store._api, 'CredReadW', read)
    monkeypatch.setattr(store._api, 'CredFree', lambda pointer: freed.append(True))
    with pytest.raises(credentials.CredentialError):
        store.configured()
    assert freed == [True]


def test_utf8_roundtrip_and_byte_limit():
    target = 'PaperLens.Test.' + uuid4().hex + '/Hy3'
    store = credentials.CredentialStore(target=target)
    try:
        store.write('合成-only')
        assert store.read() == '合成-only'
        with pytest.raises(credentials.CredentialError):
            store.write('合'*854)
        assert store.read() == '合成-only'
    finally:
        store.clear()
