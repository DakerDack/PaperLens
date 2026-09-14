# PyInstaller onedir prototype; only explicitly selected application assets.
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'backend/app/desktop.py')],
    pathex=[str(root)],
    datas=[(str(root / 'frontend/dist'), 'frontend/dist')] + [
        (str(root / 'backend/tests/fixtures' / name), 'backend/tests/fixtures')
        for name in ('generation_valid.json', 'deep_audit_v3_valid.json', 'source_blocks.json')
    ],
    hiddenimports=['uvicorn.logging', 'uvicorn.loops.asyncio',
                   'uvicorn.protocols.http.h11_impl',
                   'uvicorn.protocols.websockets.websockets_impl',
                   'uvicorn.lifespan.on'],
    excludes=['pytest', 'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
              'IPython', 'mineru', 'eval'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PaperLens',
          console=False, debug=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PaperLens')

