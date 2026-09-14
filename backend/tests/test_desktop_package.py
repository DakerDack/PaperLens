from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def test_installer_preserves_data_and_uses_per_user_x64():
    source=(ROOT/'packaging/windows.iss').read_text(encoding='utf-8')
    for required in ('PrivilegesRequired=lowest','ArchitecturesAllowed=x64compatible','DefaultDirName={localappdata}\\Programs\\PaperLens','UninstallDisplayIcon={app}\\PaperLens.exe','[Icons]','PrepareToInstall','WebView2','NetFramework'):
        assert required in source
    assert '[UninstallDelete]' not in source
    assert 'taskkill' not in source.lower()
    assert 'CloseApplications=no' in source

def test_release_build_requires_signed_offline_runtime_and_licenses():
    source=(ROOT/'tools/build_desktop.ps1').read_text(encoding='utf-8')
    for required in ('Release','WebView2Installer','Get-AuthenticodeSignature','Microsoft Corporation','THIRD_PARTY_NOTICES.md','THIRD_PARTY_LICENSES.md','ISCC','DESKTOP_BUILD_INSTALLER_FAILED'):
        assert required in source


def test_license_collection_keeps_nested_native_notices():
    source=(ROOT/'tools/build_desktop.ps1').read_text(encoding='utf-8')
    assert "str(f).lower()" in source
    assert 'Python-LICENSE.txt' in source
    assert 'A428FB8A2E762AF3EB0A6EDBBB88E9B42CCFEE80FD9B423958BCACF9B9ABBFE4' in source
    notices=(ROOT/'docs/THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
    assert 'WebView2 SDK 1.0.3856.49' in notices
    assert 'Copyright (C) Microsoft Corporation' in notices
