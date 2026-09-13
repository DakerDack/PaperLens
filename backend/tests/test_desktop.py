from pathlib import Path
import socket

import pytest
from fastapi.testclient import TestClient as RawTestClient


def TestClient(app, **kwargs):
    if hasattr(app, 'state') and hasattr(app.state, 'desktop_access'):
        app.state.desktop_access.origin = 'http://testserver'
        kwargs['headers'] = {'X-PaperLens-Token':app.state.desktop_access.token}
    return RawTestClient(app, **kwargs)


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
    window = SimpleNamespace(events=SimpleNamespace(before_show=Event(), closing=Event()))
    webview = SimpleNamespace(token='synthetic-session', settings={}, create_window=Mock(return_value=window), start=Mock())
    downloads_at_creation = []
    webview.create_window.side_effect = lambda *args, **kwargs: (downloads_at_creation.append(webview.settings.get('ALLOW_DOWNLOADS')), window)[1]
    dialog = Mock()
    monkeypatch.setitem(sys.modules, 'webview', webview)
    monkeypatch.setattr(desktop.os, 'environ', {})
    monkeypatch.setattr(desktop_verify, 'clean_environment', lambda: {})
    monkeypatch.setattr(desktop_verify, 'install_guard', lambda path: None)
    monkeypatch.setattr(desktop, 'user_data_root', lambda: tmp_path)
    monkeypatch.setattr(desktop, 'InstanceLock', Mock())
    monkeypatch.setattr(desktop, 'create_desktop_app', Mock())
    monkeypatch.setattr(desktop, 'LocalServer', Mock(return_value=server))
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(MessageBoxW=dialog)))

    monkeypatch.setattr(desktop.tempfile, 'mkdtemp', lambda **kwargs: pytest.fail('main must not allocate disposable data'))
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



def test_data_root_uses_known_folder_not_environment(monkeypatch, tmp_path):
    from backend.app import desktop
    monkeypatch.setattr(desktop, 'local_app_data', lambda: tmp_path / '中文 user')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'decoy'))
    assert desktop.user_data_root() == tmp_path / '中文 user' / 'PaperLens'


def test_single_instance_releases_and_rejects_duplicate():
    from backend.app.desktop import InstanceLock, DesktopError
    from uuid import uuid4
    name = 'PaperLens.Test.' + uuid4().hex
    first = InstanceLock(name)
    try:
        with pytest.raises(DesktopError, match='^DESKTOP_ALREADY_RUNNING$'):
            InstanceLock(name)
    finally:
        first.close()
    replacement = InstanceLock(name)
    replacement.close()


def test_close_gate_waits_for_active_request_and_rejects_new_work():
    import asyncio
    from backend.app.desktop import RequestGate
    gate = RequestGate()
    entered = asyncio.Event()
    finish = asyncio.Event()
    messages = []
    async def app(scope, receive, send):
        entered.set()
        await finish.wait()
    async def send(message):
        messages.append(message)
    async def run():
        task = asyncio.create_task(gate(app)({'type':'http','path':'/api/projects'}, None, send))
        await entered.wait()
        assert gate.begin_close() is False
        await gate(app)({'type':'http','path':'/api/projects'}, None, send)
        assert messages[0]['status'] == 403
        assert b'DESKTOP_ACCESS_DENIED' in messages[1]['body']
        finish.set()
        await task
        assert gate.begin_close() is True
    asyncio.run(run())


def test_window_close_retries_busy_and_timeout():
    from unittest.mock import Mock
    from backend.app.desktop import close_window, DesktopError
    gate = Mock()
    gate.begin_close.side_effect = [False, True, True]
    server = Mock()
    server.stop.side_effect = [DesktopError('DESKTOP_EXIT_TIMEOUT'), None]
    notify = Mock()
    assert close_window(gate, server, notify) is False
    server.stop.assert_not_called()
    assert close_window(gate, server, notify) is False
    assert close_window(gate, server, notify) is True
    assert server.stop.call_count == 2
    assert notify.call_count == 2


def test_instance_lock_released_after_process_termination():
    import subprocess
    import sys
    from uuid import uuid4
    from backend.app.desktop import InstanceLock, DesktopError
    name = 'PaperLens.Test.' + uuid4().hex
    code = f"from backend.app.desktop import InstanceLock;import sys,os;lock=InstanceLock({name!r});print('ready',flush=True);sys.stdin.readline();os._exit(17)"
    child = subprocess.Popen([sys.executable, '-B', '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        with pytest.raises(DesktopError, match='DESKTOP_ALREADY_RUNNING'):
            InstanceLock(name)
        child.communicate('crash\n', timeout=10)
        assert child.returncode == 17
        replacement = InstanceLock(name)
        replacement.close()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


def test_desktop_project_survives_service_restart(tmp_path):
    from backend.app.desktop import create_desktop_app
    assets=tmp_path/'resources';assets.mkdir()
    (assets/'index.html').write_text('<head>synthetic</head>')
    data=tmp_path/'中文 user'/'data'
    fixture=Path(__file__).parent/'fixtures/simple_2page.pdf'
    with TestClient(create_desktop_app(assets,data)) as client:
        uploaded=client.post('/api/projects',data={'rights_confirmed':'true'},files={'file':('synthetic.pdf',fixture.read_bytes(),'application/pdf')})
        assert uploaded.status_code==201
        project_id=uploaded.json()['project_id']
        before=client.get('/api/projects/'+project_id).json()
    with TestClient(create_desktop_app(assets,data)) as client:
        assert client.get('/api/projects/'+project_id).json()==before
        assert client.get('/api/projects/'+project_id+'/pdf').content==fixture.read_bytes()
    assert not (assets/'paperlens.db').exists()


def test_global_mutex_has_only_current_user_access():
    import ctypes
    from ctypes import wintypes
    from uuid import uuid4
    from backend.app.desktop import InstanceLock
    lock=InstanceLock('PaperLens.Test.'+uuid4().hex)
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    api.GetSecurityInfo.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.c_wchar_p),ctypes.c_void_p]
    descriptor=ctypes.c_void_p();text=ctypes.c_wchar_p()
    try:
        assert lock.name.startswith('Global\\PaperLens.Test.')
        assert api.GetSecurityInfo(lock.handle,6,4,None,None,None,None,ctypes.byref(descriptor))==0
        assert api.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor,1,4,ctypes.byref(text),None)
        assert text.value.startswith('D:P')
        assert text.value.count('(A;')==1
        assert lock.name.rsplit('.',1)[1] in text.value
    finally:
        if text: lock.kernel.LocalFree(text)
        if descriptor: lock.kernel.LocalFree(descriptor)
        lock.close()


def test_known_folder_ignores_spoofed_environment(monkeypatch,tmp_path):
    from backend.app.desktop import local_app_data
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'spoofed'))
    path=local_app_data()
    assert path.is_absolute()
    assert path != tmp_path/'spoofed'


def test_abrupt_exit_preserves_commit_and_rolls_back_new_project(tmp_path):
    import subprocess
    import sys
    from backend.app.desktop import create_desktop_app
    assets=tmp_path/'assets';assets.mkdir();(assets/'index.html').write_text('<head>test</head>')
    data=tmp_path/'data'
    fixture=Path(__file__).parent/'fixtures/simple_2page.pdf'
    with TestClient(create_desktop_app(assets,data)) as client:
        saved=client.post('/api/projects',data={'rights_confirmed':'true'},files={'file':('synthetic.pdf',fixture.read_bytes(),'application/pdf')})
        assert saved.status_code==201
        saved_id=saved.json()['project_id']
    code=f'''
import os
from pathlib import Path
from contextlib import contextmanager
from fastapi.testclient import TestClient
from backend.app.desktop import create_desktop_app
app=create_desktop_app(Path({str(assets)!r}),Path({str(data)!r}))
app.state.desktop_access.origin='http://testserver'
with TestClient(app,headers={{'X-PaperLens-Token':app.state.desktop_access.token}}) as client:
    original=app.state.project_store._transaction
    @contextmanager
    def crash_before_commit(*args,**kwargs):
        with original(*args,**kwargs) as connection:
            yield connection
            os._exit(23)
    app.state.project_store._transaction=crash_before_commit
    app.state.project_id_factory=lambda: 'a'*32
    client.post('/api/projects',data={{'rights_confirmed':'true'}},files={{'file':('synthetic.pdf',Path({str(fixture.resolve())!r}).read_bytes(),'application/pdf')}})
'''
    result=subprocess.run([sys.executable,'-B','-c',code],timeout=20,capture_output=True)
    assert result.returncode==23
    with TestClient(create_desktop_app(assets,data)) as client:
        assert client.get('/api/projects/'+saved_id).status_code==200
        assert client.get('/api/projects/'+saved_id+'/pdf').content==fixture.read_bytes()
        assert client.get('/api/projects/'+'a'*32).status_code==404


@pytest.mark.parametrize('nested_data', [True,False])
def test_resource_and_data_roots_cannot_overlap(tmp_path,nested_data):
    from backend.app.desktop import create_desktop_app,DesktopError
    resources=tmp_path/'resources';resources.mkdir();(resources/'index.html').write_text('<head>test</head>')
    data=resources/'data' if nested_data else tmp_path
    with pytest.raises(DesktopError,match='^DESKTOP_DATA_UNAVAILABLE$'):
        create_desktop_app(resources,data)


@pytest.mark.parametrize('duplicate',[True,False])
def test_main_preflight_failure_never_starts_service(monkeypatch,tmp_path,duplicate):
    import ctypes
    from types import SimpleNamespace
    from unittest.mock import Mock
    from backend.app import desktop
    from tools import desktop_verify
    monkeypatch.setattr(desktop.os,'environ',{})
    monkeypatch.setattr(desktop_verify,'clean_environment',lambda:{})
    lock=Mock()
    factory=Mock(return_value=lock)
    if duplicate: factory.side_effect=desktop.DesktopError('DESKTOP_ALREADY_RUNNING')
    monkeypatch.setattr(desktop,'InstanceLock',factory)
    blocked=tmp_path/'blocked';blocked.write_text('synthetic')
    root=Mock(return_value=blocked/'data')
    monkeypatch.setattr(desktop,'user_data_root',root)
    service=Mock();monkeypatch.setattr(desktop,'LocalServer',service)
    dialog=Mock();monkeypatch.setattr(ctypes,'windll',SimpleNamespace(user32=SimpleNamespace(MessageBoxW=dialog)))
    assert desktop.main()==1
    service.assert_not_called()
    if duplicate:
        root.assert_not_called()
        assert '已运行' in dialog.call_args.args[1]
    else:
        lock.close.assert_called_once()
        assert 'DESKTOP_DATA_UNAVAILABLE' in dialog.call_args.args[1]


@pytest.mark.parametrize('path', ['/api/health','/api/projects/test/pdf','/api/projects/test/export','/api/missing'])
def test_access_requires_token_on_every_api(tmp_path,path):
    from backend.app.desktop import DesktopAccess
    from starlette.responses import PlainTextResponse
    async def app(scope,receive,send):
        if scope['type']=='lifespan':
            await receive();await send({'type':'lifespan.startup.complete'});await receive();await send({'type':'lifespan.shutdown.complete'});return
        await PlainTextResponse('allowed')(scope,receive,send)
    access=DesktopAccess('synthetic-token')
    access.origin='http://127.0.0.1:43210'
    with TestClient(access(app),base_url=access.origin) as client:
        for headers in ({},{'X-PaperLens-Token':'wrong'},{'X-PaperLens-Token':'synthetic-token','Origin':'null'},{'X-PaperLens-Token':'synthetic-token','Host':'evil.invalid:43210'},{'X-PaperLens-Token':'synthetic-token','Origin':'https://evil.invalid'}):
            response=client.get(path,headers=headers)
            assert response.status_code==403
            assert response.json()['error_code']=='DESKTOP_ACCESS_DENIED'
        assert client.get(path,headers={'X-PaperLens-Token':'synthetic-token','Origin':access.origin}).text=='allowed'
        assert client.options(path,headers={'X-PaperLens-Token':'synthetic-token'}).status_code==403
        assert client.get(path+'?token=synthetic-token').status_code==403


def test_access_static_headers_and_restart_token():
    from backend.app.desktop import DesktopAccess
    from starlette.responses import PlainTextResponse
    async def app(scope,receive,send):
        if scope['type']=='lifespan':
            await receive();await send({'type':'lifespan.startup.complete'});await receive();await send({'type':'lifespan.shutdown.complete'});return
        await PlainTextResponse('static')(scope,receive,send)
    access=DesktopAccess('new-synthetic-token');access.origin='http://127.0.0.1:43210'
    with TestClient(access(app),base_url=access.origin) as client:
        response=client.get('/')
        assert response.status_code==200
        assert 'new-synthetic-token' not in response.text
        assert response.headers['x-frame-options']=='DENY'
        assert response.headers['referrer-policy']=='no-referrer'
        assert client.get('/api/health',headers={'X-PaperLens-Token':'old-synthetic-token'}).status_code==403
        for path in ('/api/docs','/api/openapi.json','/redoc'):
            assert client.get(path,headers={'X-PaperLens-Token':'new-synthetic-token'}).status_code==403


def test_real_desktop_denies_discovery_and_static_escape(tmp_path):
    from backend.app.desktop import create_desktop_app
    assets=tmp_path/'assets';assets.mkdir();(assets/'index.html').write_text('<head>public</head>')
    (tmp_path/'private.txt').write_text('synthetic-private')
    app=create_desktop_app(assets,tmp_path/'data',token='synthetic-token')
    app.state.desktop_access.origin='http://testserver'
    with RawTestClient(app) as client:
        assert client.get('/').status_code==200
        for path in ('/api/health','/api/projects/x/pdf','/api/projects/x/export','/api/missing'):
            assert client.get(path).status_code==403
        for content_type,body in [('application/json','{}'),('application/x-www-form-urlencoded','rights_confirmed=true')]:
            assert client.post('/api/projects',content=body,headers={'Origin':'https://evil.invalid','Content-Type':content_type}).status_code==403
        assert client.get('/api/health',headers={'X-PaperLens-Token':'synthetic-token'}).status_code==200
        assert client.get('/api/missing',headers={'X-PaperLens-Token':'synthetic-token'}).status_code==404
        assert client.get('/%2e%2e/private.txt').status_code==404
        assert client.get('/api/docs',headers={'X-PaperLens-Token':'synthetic-token','Origin':'http://localhost:5173'}).status_code==403
        assert 'access-control-allow-origin' not in client.get('/',headers={'Origin':'http://localhost:5173'}).headers


@pytest.mark.parametrize('url',['https://example.invalid/','file:///C:/synthetic','http://127.0.0.1:1235/','http://127.0.0.1:1234/other','javascript:alert(1)'])
def test_navigation_rejects_every_non_workbench_url(url):
    from backend.app.desktop import navigation_allowed
    assert not navigation_allowed(url,'http://127.0.0.1:1234')
    assert navigation_allowed('http://127.0.0.1:1234/','http://127.0.0.1:1234')
