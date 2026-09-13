"""Desktop entry with text-only parsing and explicit persisted settings.

Run with pythonw.exe backend/app/desktop.py (or double-click via a shortcut).
Product data and instance locking are local; API authentication follows in D2b.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import json


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class DesktopError(RuntimeError):
    pass



def local_app_data() -> Path:
    """Read the current user's Known Folder; never trust LOCALAPPDATA."""
    import ctypes
    import uuid
    from ctypes import wintypes
    folder = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID('f1b32785-6fba-4fcf-9d55-7b8e7f157091').bytes_le)
    pointer = ctypes.c_wchar_p()
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    free = ctypes.WinDLL('ole32').CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    try:
        if shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(pointer)) != 0 or not pointer.value:
            raise DesktopError('DESKTOP_DATA_UNAVAILABLE')
        return Path(pointer.value)
    finally:
        if pointer:
            free(pointer)


def user_data_root() -> Path:
    return local_app_data() / 'PaperLens'


class InstanceLock:
    """A user-only Global kernel object, destroyed when its last handle closes."""
    def __init__(self, application='PaperLens'):
        import ctypes
        from ctypes import wintypes
        os.environ['PYTHONNET_RUNTIME'] = 'netfx'
        import clr  # noqa: F401
        from System.Security.Principal import WindowsIdentity
        identity = WindowsIdentity.GetCurrent()
        try:
            sid = str(identity.User.Value)
        finally:
            identity.Dispose()
        self.name = 'Global\\' + application + '.' + sid
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        class Attributes(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wintypes.BOOL)]
        descriptor = ctypes.c_void_p()
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(f'D:P(A;;GA;;;{sid})', 1, ctypes.byref(descriptor), None):
            raise DesktopError('DESKTOP_ACCESS_DENIED')
        try:
            attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
            ctypes.set_last_error(0)
            self.handle = self.kernel.CreateMutexW(ctypes.byref(attributes), False, self.name)
            error = ctypes.get_last_error()
        finally:
            self.kernel.LocalFree(descriptor)
        if not self.handle:
            raise DesktopError('DESKTOP_ACCESS_DENIED')
        if error == 183:
            self.close()
            raise DesktopError('DESKTOP_ALREADY_RUNNING')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class RequestGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.closing = False

    def begin_close(self):
        with self.lock:
            self.closing = True
            return self.active == 0

    def __call__(self, app):
        async def guarded(scope, receive, send):
            if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
                return await app(scope, receive, send)
            with self.lock:
                rejected = self.closing
                if not rejected:
                    self.active += 1
            if rejected:
                from starlette.responses import JSONResponse
                response = JSONResponse({'error_code':'DESKTOP_ACCESS_DENIED', 'message':'应用正在退出，请等待或重启。', 'retryable':True}, status_code=403)
                return await response(scope, receive, send)
            try:
                return await app(scope, receive, send)
            finally:
                with self.lock:
                    self.active -= 1
        return guarded


def close_window(gate, server, notify):
    if not gate.begin_close():
        notify('操作正在进行，请等待完成后再次关闭窗口。')
        return False
    try:
        server.stop()
    except Exception:
        notify('退出未完成，请再次关闭窗口重试：DESKTOP_EXIT_TIMEOUT')
        return False
    return True


class DesktopAccess:
    def __init__(self, token):
        self.token = token
        self.origin = None

    def __call__(self, app):
        async def guarded(scope, receive, send):
            if scope['type'] != 'http':
                return await app(scope, receive, send)
            import hmac
            headers = {}
            duplicates = False
            for key, value in scope['headers']:
                if key in headers and key in (b'host', b'origin', b'x-paperlens-token'):
                    duplicates = True
                headers[key] = value
            origin = self.origin or ''
            valid = bool(origin) and not duplicates and headers.get(b'host') == origin.removeprefix('http://').encode()
            valid = valid and (b'origin' not in headers or headers[b'origin'] == origin.encode())
            path = scope['path']
            if path.startswith('/api/'):
                valid = valid and hmac.compare_digest(headers.get(b'x-paperlens-token', b''), self.token.encode())
            if scope['method'] == 'OPTIONS' or path in ('/api/docs', '/api/openapi.json', '/docs', '/redoc', '/openapi.json'):
                valid = False
            async def secured(message):
                if message['type'] == 'http.response.start':
                    message['headers'] = [(k,v) for k,v in message.get('headers',[]) if not k.lower().startswith(b'access-control-')]
                    message['headers'] += [(b'x-frame-options',b'DENY'),(b'referrer-policy',b'no-referrer'),(b'content-security-policy',b"frame-ancestors 'none'"),(b'x-content-type-options',b'nosniff')]
                await send(message)
            if not valid:
                from starlette.responses import JSONResponse
                response = JSONResponse({'error_code':'DESKTOP_ACCESS_DENIED','message':'本机请求未获授权。','retryable':False}, status_code=403)
                return await response(scope, receive, secured)
            await app(scope, receive, secured)
        return guarded


def navigation_allowed(url, origin):
    return url == origin + '/'


class RecentProjectBridge:
    def __init__(self, path, origin, current_url, *, credentials=None):
        self._credentials = credentials
        self._path = path
        self._origin = origin
        self._current_url = current_url
        self._lock = threading.Lock()

    def _trusted(self):
        return navigation_allowed(self._current_url(), self._origin)

    def _read(self):
        from backend.app.models import DesktopState
        from pydantic import ValidationError
        try:
            return DesktopState.model_validate_json(self._path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return DesktopState(mode="live")
        except (ValidationError, UnicodeError):
            raise DesktopError('DESKTOP_STATE_INVALID') from None

    def _write(self, state):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self._path.parent,
                                             prefix='.desktop-state-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(state.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _error(code):
        messages = {'DESKTOP_ACCESS_DENIED':'当前窗口无权访问桌面状态。',
                    'DESKTOP_STATE_INVALID':'桌面状态无效，请检查后重试。',
                    'DESKTOP_DATA_UNAVAILABLE':'无法读写桌面状态，请重试。',
                    'DESKTOP_SETTINGS_INVALID':'设置输入无效。'}
        return {'ok':False,'error':{'error_code':code,'message':messages[code],'retryable':code=='DESKTOP_DATA_UNAVAILABLE'}}

    def get_recent_project(self):
        try:
            if not self._trusted():
                return self._error('DESKTOP_ACCESS_DENIED')
            with self._lock:
                state = self._read()
            return {'ok':True,'value':{'project_id':state.project_id}}
        except DesktopError as error:
            return self._error(str(error))
        except Exception:
            return self._error('DESKTOP_DATA_UNAVAILABLE')

    def set_recent_project(self, payload):
        from backend.app.models import DesktopRecentProjectRequest
        from pydantic import ValidationError
        try:
            if not self._trusted():
                return self._error('DESKTOP_ACCESS_DENIED')
            request = DesktopRecentProjectRequest.model_validate(payload)
            with self._lock:
                state = self._read()
                state.project_id = request.project_id
                self._write(state)
            return {'ok':True,'value':{'saved':True}}
        except ValidationError:
            return self._error('DESKTOP_STATE_INVALID')
        except DesktopError as error:
            return self._error(str(error))
        except Exception:
            return self._error('DESKTOP_DATA_UNAVAILABLE')

    def _store(self):
        if self._credentials is None:
            from backend.app.desktop_credentials import CredentialStore
            self._credentials = CredentialStore()
        return self._credentials

    def _startup_configuration(self):
        with self._lock:
            return self._read().mode, self._store().read() or ''

    def get_desktop_settings(self, *args, **kwargs):
        from backend.app.desktop_credentials import CredentialError
        from backend.app.models import DesktopSettingsStatus
        try:
            if not self._trusted(): return self._error('DESKTOP_ACCESS_DENIED')
            if args or kwargs: return self._error('DESKTOP_SETTINGS_INVALID')
            with self._lock:
                status=DesktopSettingsStatus(mode=self._read().mode, key_configured=self._store().configured())
            return {'ok':True,'value':status.model_dump()}
        except CredentialError as error:
            return error.envelope()
        except DesktopError as error:
            return self._error(str(error))
        except Exception:
            return self._error('DESKTOP_DATA_UNAVAILABLE')

    def save_desktop_settings(self, payload=None, *args, **kwargs):
        from backend.app.desktop_credentials import CredentialError
        from backend.app.models import DesktopSettingsRequest
        from pydantic import ValidationError
        try:
            if not self._trusted(): return self._error('DESKTOP_ACCESS_DENIED')
            if args or kwargs: return self._error('DESKTOP_SETTINGS_INVALID')
            request=DesktopSettingsRequest.model_validate(payload)
            with self._lock:
                old=self._read()
                state=old.model_copy(update={'mode':request.mode})
                # Persist non-secret state first; a failed credential write leaves the old Key intact.
                self._write(state)
                try:
                    if 'api_key' in request.model_fields_set:
                        self._store().write(request.api_key.get_secret_value())
                except Exception:
                    self._write(old)
                    raise
            return {'ok':True,'value':{'restart_required':True}}
        except ValidationError:
            return self._error('DESKTOP_SETTINGS_INVALID')
        except CredentialError as error:
            return error.envelope()
        except DesktopError as error:
            return self._error(str(error))
        except Exception:
            return self._error('DESKTOP_DATA_UNAVAILABLE')

    def clear_desktop_key(self, *args, **kwargs):
        from backend.app.desktop_credentials import CredentialError
        try:
            if not self._trusted(): return self._error('DESKTOP_ACCESS_DENIED')
            if args or kwargs: return self._error('DESKTOP_SETTINGS_INVALID')
            with self._lock:
                self._store().clear()
            return {'ok':True,'value':{'key_configured':False,'restart_required':True}}
        except CredentialError:
            return CredentialError(clearing=True).envelope()
        except Exception:
            return CredentialError(clearing=True).envelope()


def frontend_resources() -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return root / "frontend/dist"


def create_desktop_app(resources: Path, data_dir: Path, *, token=None, settings_bridge=None):
    resources = resources.resolve()
    data_dir = data_dir.resolve()
    if data_dir.is_relative_to(resources) or resources.is_relative_to(data_dir):
        raise DesktopError('DESKTOP_DATA_UNAVAILABLE')
    if not (resources / "index.html").is_file():
        raise DesktopError("DESKTOP_RESOURCE_MISSING")
    # Set before any module with a global Settings instance is imported.
    os.environ["PAPERLENS_DESKTOP"] = "1"
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from backend.app.main import create_app
    from backend.app.document_service import DocumentService
    from backend.app.settings import Settings

    mode, key = settings_bridge._startup_configuration() if settings_bridge is not None else ("live", "")
    settings = Settings(paperlens_env="test", paperlens_model_mode=mode,
                        paperlens_data_dir=data_dir, hy3_api_key=key)
    app = create_app(settings_override=settings,
                     document_service=DocumentService(settings, text_only=True))
    import secrets
    access = DesktopAccess(token or secrets.token_urlsafe(32))
    app.state.desktop_access = access
    gate = RequestGate()
    app.state.desktop_gate = gate
    app.add_middleware(gate)
    app.add_middleware(access)
    html = (resources / "index.html").read_text(encoding="utf-8")
    html = html.replace("<head>", '<head><meta name="paperlens-desktop" content="prototype">', 1)

    @app.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(html)

    app.mount("/", StaticFiles(directory=resources), name="desktop-assets")
    return app


class LocalServer:
    def __init__(self, app):
        import uvicorn
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.access = app.state.desktop_access
        self.access.origin = self.url
        self.server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", log_config=None, access_log=False,
            log_level="critical", timeout_graceful_shutdown=5,
        ))
        self.thread = threading.Thread(target=self.server.run,
                                       kwargs={"sockets": [self.socket]}, daemon=True)

    def start(self, timeout=30):
        self.thread.start()
        deadline = time.monotonic() + timeout
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            if not self.thread.is_alive():
                self.socket.close()
                raise DesktopError("DESKTOP_START_FAILED")
            if self.server.started:
                try:
                    with opener.open(urllib.request.Request(self.url + "/api/health", headers={"X-PaperLens-Token":self.access.token}), timeout=0.5) as response:
                        if json.load(response) == {"status":"ok", "service":"paperlens-api", "version":"0.1.0"}:
                            return
                except (OSError, ValueError):
                    pass
            time.sleep(0.05)
        self.stop()
        raise DesktopError("DESKTOP_START_TIMEOUT")

    def stop(self):
        self.server.should_exit = True
        if self.thread.ident is not None:
            self.thread.join(7)
        self.socket.close()
        if self.thread.is_alive():
            raise DesktopError("DESKTOP_EXIT_TIMEOUT")


def main() -> int:
    # Settings remain explicit Mock with no key until D3.
    from tools.desktop_verify import clean_environment, install_guard
    environment = clean_environment()
    os.environ.clear()
    os.environ.update(environment)
    os.environ["PYTHONNET_RUNTIME"] = "netfx"
    instance = None
    server = None
    code = None
    try:
        instance = InstanceLock()
        data = user_data_root()
        try:
            data.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise DesktopError('DESKTOP_DATA_UNAVAILABLE') from None
        install_guard(data)
        import webview
        bridge = RecentProjectBridge(data / "desktop-state.json", None, lambda: window.get_current_url())
        app = create_desktop_app(frontend_resources(), data / "data", token=webview.token, settings_bridge=bridge)
        server = LocalServer(app)
        server.start()
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        bridge._origin = server.url
        window = webview.create_window(f"PaperLens · {app.state.settings.paperlens_model_mode.upper()}", server.url,
                                       width=1280, height=850, js_api=bridge)
        def restrict_navigation():
            # pywebview 6.2.1's pinned Windows native surface, before first load.
            native = window.native.webview
            def navigating(sender, args):
                if not navigation_allowed(str(args.Uri), server.url):
                    args.Cancel = True
            native.NavigationStarting += navigating
            def initialized(sender, args):
                if args.IsSuccess:
                    core = native.CoreWebView2
                    core.NewWindowRequested -= window.native.browser.on_new_window_request
                    def refuse_new_window(sender, args):
                        args.Handled = True
                    core.NewWindowRequested += refuse_new_window
            native.CoreWebView2InitializationCompleted += initialized
        window.events.before_show += restrict_navigation
        def notify(message):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'PaperLens', 0x30)
        window.events.closing += lambda: close_window(app.state.desktop_gate, server, notify)
        # Only the trusted bundled page can use the explicit project/settings methods.
        webview.start(gui="edgechromium", debug=False, private_mode=False,
                      storage_path=str(data / "webview"))
    except Exception as error:
        from backend.app.desktop_credentials import CredentialError
        code = str(error) if isinstance(error, (DesktopError, CredentialError)) else "DESKTOP_START_FAILED"
    finally:
        if server is not None:
            try:
                server.stop()
            except Exception as error:
                if code is None:
                    code = str(error) if isinstance(error, DesktopError) else "DESKTOP_EXIT_TIMEOUT"
        if instance is not None:
            instance.close()
    if code is not None:
        import ctypes
        message = "PaperLens 已运行，请切换到现有窗口：DESKTOP_ALREADY_RUNNING" if code == "DESKTOP_ALREADY_RUNNING" else f"桌面原型未能完成启动或退出：{code}"
        ctypes.windll.user32.MessageBoxW(None, message, "PaperLens", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

