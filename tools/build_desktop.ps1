param([ValidateSet('OfflineTest')][string]$Configuration = 'OfflineTest')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv-desktop\Scripts\python.exe'
Push-Location $root
try {
    & $python -B tools/desktop_verify.py --suite frontend
    if ($LASTEXITCODE -ne 0) { throw 'DESKTOP_BUILD_FRONTEND_FAILED' }
    foreach ($folder in @('cmaps', 'standard_fonts', 'wasm')) {
        $source = Join-Path $root "frontend\node_modules\pdfjs-dist\$folder"
        $target = Join-Path $root "frontend\dist\pdfjs\$folder"
        New-Item -ItemType Directory -Force $target | Out-Null
        Copy-Item -Path "$source\*" -Destination $target -Recurse -Force
    }
    $output = Join-Path $root ('dist\OfflineTest-' + [guid]::NewGuid().ToString('N'))
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
    $manifest = @(Get-ChildItem -LiteralPath $bundle -File -Recurse | ForEach-Object {
        [ordered]@{path=$_.FullName.Substring($bundle.Length + 1); size=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    })
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $output 'PaperLens-manifest.json') -Encoding utf8
    Write-Output "OFFLINE_PROTOTYPE=$bundle\PaperLens.exe"
    Write-Output 'INSTALLATION_AND_NATIVE_DOWNLOAD_NOT_VERIFIED_BY_BUILD'
} finally { Pop-Location }



