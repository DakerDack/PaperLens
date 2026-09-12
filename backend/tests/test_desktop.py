from pathlib import Path
import socket

import pytest
from fastapi.testclient import TestClient


def test_desktop_missing_resources_fail_before_start(tmp_path):
    from backend.app.desktop import DesktopError, create_desktop_app
    with pytest.raises(DesktopError, match="DESKTOP_RESOURCE_MISSING"):
        create_desktop_app(tmp_path / "missing", tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_desktop_serves_workbench_and_real_text_api(tmp_path):
    from backend.app.desktop import create_desktop_app
    resources = tmp_path / "assets"
    resources.mkdir()
    (resources / "index.html").write_text('<html><head></head><body>workbench</body></html>')
    app = create_desktop_app(resources, tmp_path / "data")
    with TestClient(app) as client:
        assert 'paperlens-desktop' in client.get('/').text
        assert client.get('/api/health').json()['status'] == 'ok'
        assert client.get('/api/unknown').status_code == 404
        assert app.state.settings.paperlens_model_mode == 'mock'
        assert app.state.settings.hy3_api_key == ''
        assert app.state.document_service.text_only is True
        fixture = Path(__file__).parent / 'fixtures/simple_2page.pdf'
        response = client.post('/api/projects', data={'rights_confirmed':'true'},
                               files={'file': ('synthetic.pdf', fixture.read_bytes(), 'application/pdf')})
        assert response.status_code == 201


def test_desktop_server_uses_bound_loopback_and_stops(tmp_path):
    from backend.app.desktop import LocalServer, create_desktop_app
    resources = tmp_path / 'assets'
    resources.mkdir()
    (resources/'index.html').write_text('<html><head></head></html>')
    server = LocalServer(create_desktop_app(resources, tmp_path/'data'))
    server.start()
    port = server.port
    assert server.url == f'http://127.0.0.1:{port}'
    assert server.thread.is_alive()
    server.stop()
    assert not server.thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0


@pytest.mark.parametrize('startup_code,stop_fails,expected', [
    (None, True, 'DESKTOP_EXIT_TIMEOUT'),
    ('DESKTOP_START_TIMEOUT', True, 'DESKTOP_START_TIMEOUT'),
    (None, False, None),
])
def test_main_reports_cleanup_failure_without_masking_startup(
    monkeypatch, tmp_path, startup_code, stop_fails, expected
):
    import ctypes
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock
    from backend.app import desktop
    from tools import desktop_verify

    server = Mock(url='http://127.0.0.1:12345')
    if startup_code:
        server.start.side_effect = desktop.DesktopError(startup_code)
    if stop_fails:
        server.stop.side_effect = desktop.DesktopError('DESKTOP_EXIT_TIMEOUT')
    class Event:
        def __iadd__(self, handler):
            return self
    window = SimpleNamespace(events=SimpleNamespace(before_show=Event()))
    webview = SimpleNamespace(settings={}, create_window=Mock(return_value=window), start=Mock())
    downloads_at_creation = []
    webview.create_window.side_effect = lambda *args, **kwargs: (downloads_at_creation.append(webview.settings.get('ALLOW_DOWNLOADS')), window)[1]
    dialog = Mock()
    monkeypatch.setitem(sys.modules, 'webview', webview)
    monkeypatch.setattr(desktop.os, 'environ', {})
    monkeypatch.setattr(desktop_verify, 'clean_environment', lambda: {})
    monkeypatch.setattr(desktop_verify, 'install_guard', lambda path: None)
    monkeypatch.setattr(desktop.tempfile, 'mkdtemp', lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(desktop, 'create_desktop_app', Mock())
    monkeypatch.setattr(desktop, 'LocalServer', Mock(return_value=server))
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(MessageBoxW=dialog)))

    assert desktop.main() == (1 if expected else 0)
    assert downloads_at_creation == ([] if startup_code else [True])
    server.stop.assert_called_once_with()
    assert webview.start.call_count == (0 if startup_code else 1)
    if expected:
        dialog.assert_called_once_with(None, f'桌面原型未能完成启动或退出：{expected}', 'PaperLens', 0x10)
    else:
        dialog.assert_not_called()


@pytest.mark.parametrize('frozen', [False, True])
@pytest.mark.parametrize('present', [False, True])
def test_resource_location_ignores_cwd(monkeypatch, tmp_path, frozen, present):
    from backend.app import desktop

    source = tmp_path / '源码 space'
    bundle = tmp_path / '冻结 space'
    cwd = tmp_path / '另一个 cwd'
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(desktop, '__file__', str(source / 'backend/app/desktop.py'))
    monkeypatch.setattr(desktop.sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(desktop.sys, '_MEIPASS', str(bundle), raising=False)
    expected = (bundle if frozen else source) / 'frontend/dist'
    expected.mkdir(parents=True)
    # A decoy under cwd must never be selected, even when the real index is absent.
    (cwd / 'frontend/dist').mkdir(parents=True)
    (cwd / 'frontend/dist/index.html').write_text('<head>decoy</head>')
    if present:
        (expected / 'index.html').write_text('<head>synthetic</head>')
    assert desktop.frontend_resources() == expected
    if present:
        app = desktop.create_desktop_app(desktop.frontend_resources(), tmp_path / 'data')
        with TestClient(app) as client:
            assert 'synthetic' in client.get('/').text
            assert 'decoy' not in client.get('/').text
    else:
        with pytest.raises(desktop.DesktopError, match='^DESKTOP_RESOURCE_MISSING$'):
            desktop.create_desktop_app(desktop.frontend_resources(), tmp_path / 'data')
        assert not (tmp_path / 'data').exists()

