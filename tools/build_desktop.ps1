param(
    [ValidateSet('OfflineTest','Release')][string]$Configuration = 'OfflineTest',
    [string]$WebView2Installer,
    [string]$ProxyToolsLicense = (Join-Path $PSScriptRoot '..\build\proxy_tools-LICENSE.txt'),
    [string]$ISCC = (Join-Path $PSScriptRoot '..\build\inno-6.7.3\ISCC.exe')
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv-desktop\Scripts\python.exe'
Push-Location $root
try {
    if ($Configuration -eq 'Release') {
        if (-not $WebView2Installer -or -not (Test-Path -LiteralPath $WebView2Installer)) { throw 'DESKTOP_BUILD_RUNTIME_MISSING' }
        $signature = Get-AuthenticodeSignature -LiteralPath $WebView2Installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'DESKTOP_BUILD_RUNTIME_SIGNATURE_INVALID' }
        if (-not (Test-Path -LiteralPath $ISCC)) { throw 'DESKTOP_BUILD_ISCC_MISSING' }
    }
    & $python -B tools/desktop_verify.py --suite frontend
    if ($LASTEXITCODE -ne 0) { throw 'DESKTOP_BUILD_FRONTEND_FAILED' }
    foreach ($folder in @('cmaps', 'standard_fonts', 'wasm')) {
        $source = Join-Path $root "frontend\node_modules\pdfjs-dist\$folder"
        $target = Join-Path $root "frontend\dist\pdfjs\$folder"
        New-Item -ItemType Directory -Force $target | Out-Null
        Copy-Item -Path "$source\*" -Destination $target -Recurse -Force
    }
    $output = Join-Path $root ('dist\' + $Configuration + '-' + [guid]::NewGuid().ToString('N'))
    & $python -B -c "import subprocess,sys,tempfile; from tools.desktop_verify import clean_environment; sys.exit(subprocess.call([sys.executable,'-B','-m','PyInstaller','--noconfirm','--distpath',sys.argv[1],'desktop.spec'],env=clean_environment() | {'USERPROFILE':tempfile.mkdtemp(prefix='paperlens-build-')}))" $output
    if ($LASTEXITCODE -ne 0) { throw 'DESKTOP_BUILD_FREEZE_FAILED' }
    $bundle = Join-Path $output 'PaperLens'
    foreach ($relative in @('PaperLens.exe', '_internal\frontend\dist\index.html', '_internal\python313.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path $bundle $relative))) { throw "DESKTOP_BUILD_RESOURCE_MISSING: $relative" }
    }
    $assets = Join-Path $root 'frontend\dist'
    foreach ($file in Get-ChildItem -LiteralPath $assets -File -Recurse) {
        $relative = $file.FullName.Substring($assets.Length + 1)
        $packed = Join-Path $bundle "_internal\frontend\dist\$relative"
        if (-not (Test-Path -LiteralPath $packed) -or (Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $packed).Hash) {
            throw "DESKTOP_BUILD_RESOURCE_MISMATCH: $relative"
        }
    }
    Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination $bundle
    foreach ($notice in @('THIRD_PARTY_NOTICES.md','THIRD_PARTY_LICENSES.md')) {
        Copy-Item -LiteralPath (Join-Path $root "docs\$notice") -Destination $bundle
    }
    'Uninstall removes application files and shortcuts only. PaperLens data, settings, credentials and shared WebView2 are retained.' | Set-Content -LiteralPath (Join-Path $bundle 'UNINSTALL-NOTE.txt') -Encoding utf8
    if (-not (Test-Path -LiteralPath $ProxyToolsLicense) -or (Get-FileHash -LiteralPath $ProxyToolsLicense -Algorithm SHA256).Hash -ne 'A428FB8A2E762AF3EB0A6EDBBB88E9B42CCFEE80FD9B423958BCACF9B9ABBFE4') { throw 'DESKTOP_BUILD_PROXY_LICENSE_INVALID' }
    $licenses = Join-Path $bundle 'licenses'
    New-Item -ItemType Directory -Force $licenses | Out-Null
    Copy-Item -LiteralPath $ProxyToolsLicense -Destination (Join-Path $licenses 'proxy_tools-LICENSE.txt')
    if ($Configuration -eq 'Release') { Copy-Item -LiteralPath (Join-Path (Split-Path $ISCC) 'license.txt') -Destination (Join-Path $licenses 'Inno-Setup-LICENSE.txt') }
    # Distribution license files retain original text, including bundled native notices.
    & $python -B -c "import importlib.metadata as m,pathlib,shutil,sys; out=pathlib.Path(sys.argv[1]); [( (out/d.metadata['Name']/str(f)).parent.mkdir(parents=True,exist_ok=True), shutil.copyfile(d.locate_file(f),out/d.metadata['Name']/str(f))) for d in m.distributions() for f in (d.files or []) if not '..' in f.parts and any(x in str(f).lower() for x in ('license','copying','notice')) and d.locate_file(f).is_file()]" $licenses
    if ($LASTEXITCODE -ne 0) { throw 'DESKTOP_BUILD_LICENSES_FAILED' }
    & $python -B -c "import sys,pathlib,shutil; shutil.copyfile(pathlib.Path(sys.base_prefix)/'LICENSE.txt', pathlib.Path(sys.argv[1])/'Python-LICENSE.txt')" $licenses
    if ($LASTEXITCODE -ne 0) { throw 'DESKTOP_BUILD_LICENSES_FAILED' }
    foreach ($file in Get-ChildItem -LiteralPath $bundle -File -Recurse) {
        if ($file.Name -like '.env*' -or $file.Extension -in @('.db','.sqlite3','.pdf','.log') -or $file.Name -eq 'desktop-state.json') { throw 'DESKTOP_BUILD_PRIVATE_RESOURCE_REFUSED' }
    }
    $manifest = @(Get-ChildItem -LiteralPath $bundle -File -Recurse | ForEach-Object {
        [ordered]@{path=$_.FullName.Substring($bundle.Length + 1); size=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    })
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $output 'PaperLens-manifest.json') -Encoding utf8
    if ($Configuration -eq 'Release') {
        $installerOutput = Join-Path $root ('dist\installer\' + [guid]::NewGuid().ToString('N'))
        & $ISCC "/DBundleDir=$bundle" "/DWebView2Installer=$WebView2Installer" "/DOutputPath=$installerOutput" (Join-Path $root 'packaging\windows.iss')
        if ($LASTEXITCODE -ne 0) { throw 'DESKTOP_BUILD_INSTALLER_FAILED' }
        Get-FileHash -LiteralPath (Join-Path $installerOutput 'PaperLens-0.1.0-windows-x64-setup.exe') -Algorithm SHA256
        Write-Output "INSTALLER_DIR=$installerOutput"
    }
    Write-Output "OFFLINE_PROTOTYPE=$bundle\PaperLens.exe"
    Write-Output 'INSTALLATION_AND_NATIVE_DOWNLOAD_NOT_VERIFIED_BY_BUILD'
} finally { Pop-Location }



