from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

# Run separately with system PowerShell: desktop_verify deliberately disallows
# arbitrary child processes. Only the parsed launch branch and NoLinks function
# run here; Start-Process is replaced, so no installer/user profile is accessed.
INSTALL_LINK_PROBE = r'''
$ErrorActionPreference = 'Stop'
$source = Join-Path (Get-Location) 'tools/verify_windows_install.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Script parse failed' }
$function = $ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'NoLinks'}, $false)
. ([scriptblock]::Create($function.Extent.Text))
$body = $ast.Find({param($n) $n -is [System.Management.Automation.Language.TryStatementAst]}, $false).Body
$branches = @($body.Statements | Where-Object { $_ -is [System.Management.Automation.Language.IfStatementAst] -and $_.Extent.Text.Contains('Start-Process') })
if ($branches.Count -ne 1) { throw 'Expected one installer launch branch' }
$launch = [scriptblock]::Create($branches[0].Extent.Text)
function Start-Process { $script:starts++; return [pscustomobject]@{ExitCode=0} }
$root = Join-Path (Get-Location) ('build/d4b-evidence/links-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$failures = 0
foreach ($Stage in @('Install','Reinstall','Upgrade')) {
    foreach ($case in @('target-link','parent-link','ancestor-link','nested-link','missing-clean','existing-clean')) {
        $fixture = Join-Path $root ($Stage + '-' + $case)
        $outside = Join-Path $fixture 'synthetic-target'
        New-Item -ItemType Directory -Path $outside | Out-Null
        $programs = Join-Path $fixture 'Programs'
        $appRoot = Join-Path $programs 'PaperLens'
        switch ($case) {
            'target-link' {
                New-Item -ItemType Directory -Path $programs | Out-Null
                New-Item -ItemType Junction -Path $appRoot -Target $outside | Out-Null
            }
            'parent-link' { New-Item -ItemType Junction -Path $programs -Target $outside | Out-Null }
            'ancestor-link' {
                $ancestor = Join-Path $fixture 'ancestor'
                New-Item -ItemType Junction -Path $ancestor -Target $outside | Out-Null
                $appRoot = Join-Path $ancestor 'missing/Programs/PaperLens'
            }
            'nested-link' {
                New-Item -ItemType Directory -Path $appRoot | Out-Null
                New-Item -ItemType Junction -Path (Join-Path $appRoot '_internal') -Target $outside | Out-Null
            }
            'existing-clean' { New-Item -ItemType Directory -Path $appRoot | Out-Null }
        }
        $installer = 'SYNTHETIC-NOT-EXECUTABLE'; $log = Join-Path $fixture 'unused.log'
        $script:starts = 0; $errorCode = ''
        try { . $launch } catch { $errorCode = $_.Exception.Message }
        $reject = $case.EndsWith('-link')
        $pass = if ($reject) { $errorCode -eq 'INSTALL_LINK_REFUSED' -and $script:starts -eq 0 } else { !$errorCode -and $script:starts -eq 1 }
        Write-Output "$Stage/$case PASS=$pass START_CALLS=$script:starts ERROR=$errorCode"
        if (!$pass) { $failures++ }
    }
}
if ($failures) { throw "$failures link checks failed" }
Write-Output '18 link checks passed; Start-Process stub only; no installer executed'
'''


def test_install_acceptance_has_no_credential_or_data_deletion_commands():
    source = (ROOT / 'tools/verify_windows_install.ps1').read_text(encoding='utf-8').lower()
    for forbidden in ('remove-item', 'cmdkey', 'credread', 'credwrite', 'creddelete',
                      'stop-process', 'taskkill', 'sendkeys', 'invoke-expression'):
        assert forbidden not in source
    assert source.index('if (!$execute)') < source.index('windowsidentity')
    assert source.index('install_test_user_required') < source.index('new-item')
    assert source.index('install_existing_state_refused') < source.index('new-item')

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


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != '--write-link-probe':
        raise SystemExit('usage: test_desktop_package.py --write-link-probe <path>')
    Path(sys.argv[2]).write_text(INSTALL_LINK_PROBE, encoding='utf-8-sig')
