"""Desktop entry: text-only Mock until the settings card enables provider configuration.

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


def frontend_resources() -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return root / "frontend/dist"


def create_desktop_app(resources: Path, data_dir: Path):
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

    settings = Settings(paperlens_env="test", paperlens_model_mode="mock",
                        paperlens_data_dir=data_dir, hy3_api_key="")
    app = create_app(settings_override=settings,
                     document_service=DocumentService(settings, text_only=True))
    gate = RequestGate()
    app.state.desktop_gate = gate
    app.add_middleware(gate)
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
                    with opener.open(self.url + "/api/health", timeout=0.5) as response:
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
        app = create_desktop_app(frontend_resources(), data / "data")
        server = LocalServer(app)
        server.start()
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        window = webview.create_window("PaperLens · 桌面原型 · MOCK", server.url,
                                       width=1280, height=850)
        def restrict_navigation():
            # pywebview 6.2.1's pinned Windows native surface, before first load.
            native = window.native.webview
            def navigating(sender, args):
                if str(args.Uri) != server.url + "/":
                    args.Cancel = True
            native.NavigationStarting += navigating
        window.events.before_show += restrict_navigation
        def notify(message):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'PaperLens', 0x30)
        window.events.closing += lambda: close_window(app.state.desktop_gate, server, notify)
        # D1 uses only the trusted bundled page and exposes no custom native API.
        webview.start(gui="edgechromium", debug=False, private_mode=False,
                      storage_path=str(data / "webview"))
    except Exception as error:
        code = str(error) if isinstance(error, DesktopError) else "DESKTOP_START_FAILED"
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

