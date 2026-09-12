"""D1 desktop prototype: isolated Mock data only, no user credentials.

Run with pythonw.exe backend/app/desktop.py (or double-click via a shortcut).
Product data, instance locking and API authentication are delivered in D2.
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


def frontend_resources() -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return root / "frontend/dist"


def create_desktop_app(resources: Path, data_dir: Path):
    resources = resources.resolve()
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
    # This pre-D2 prototype never loads user state or secrets.
    from tools.desktop_verify import clean_environment, install_guard
    environment = clean_environment()
    os.environ.clear()
    os.environ.update(environment)
    os.environ["PYTHONNET_RUNTIME"] = "netfx"
    data = Path(tempfile.mkdtemp(prefix="paperlens-desktop-prototype-"))
    install_guard(data)
    server = None
    code = None
    try:
        import webview
        server = LocalServer(create_desktop_app(frontend_resources(), data / "data"))
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
        # D1 uses only the trusted bundled page and exposes no custom native API.
        webview.start(gui="edgechromium", debug=False, private_mode=True,
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
    if code is not None:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f"桌面原型未能完成启动或退出：{code}", "PaperLens", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

