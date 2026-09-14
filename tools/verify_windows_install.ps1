param(
    [Parameter(Mandatory=$true)][string]$InstallerPath,
    [Parameter(Mandatory=$true)][string]$InstallerSHA256,
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [string]$PreviousInstallerPath,
    [string]$PreviousInstallerSHA256,
    [ValidateSet('Install','Upgrade','Reinstall','Uninstall')][string]$Stage = 'Install',
    [string]$TestUserSid,
    [switch]$Execute
)
$ErrorActionPreference = 'Stop'

function CheckedInstaller([string]$Path, [string]$Hash) {
    if ($Hash -notmatch '^[a-fA-F0-9]{64}$' -or !(Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'INSTALL_INPUT_INVALID'
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ((Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash -ne $Hash) {
        throw 'INSTALL_HASH_MISMATCH'
    }
    return $resolved
}
function NoLinks([string]$Path, [switch]$Shallow, [switch]$AllowMissing) {
    $candidate = [IO.Path]::GetFullPath($Path)
    $item = $null
    while ($null -eq $item) {
        try { $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] {
            if (!$AllowMissing) { throw }
            $parent = Split-Path -Path $candidate -Parent
            if (!$parent -or $parent -eq $candidate) { throw }
            $candidate = $parent
        }
    }
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'INSTALL_LINK_REFUSED' }
        $item = $item.Parent
    }
    if ($Shallow -or $candidate -ne [IO.Path]::GetFullPath($Path)) { return }
    foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
        if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'INSTALL_LINK_REFUSED' }
        if ($child.PSIsContainer) { NoLinks $child.FullName }
    }
}
function DataHashes {
    NoLinks $dataRoot
    return @((Get-ChildItem -LiteralPath $dataRoot -File -Recurse -Force | Sort-Object FullName | ForEach-Object {
        $_.FullName.Substring($dataRoot.Length) + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }))
}
try {
    $installer = CheckedInstaller $InstallerPath $InstallerSHA256
    if ($PreviousInstallerPath) { $previous = CheckedInstaller $PreviousInstallerPath $PreviousInstallerSHA256 }
    $manifest = @(Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json)
    $names = @{}
    if (!$manifest.Count) { throw 'INSTALL_MANIFEST_INVALID' }
    foreach ($entry in $manifest) {
        if (!$entry.path -or [IO.Path]::IsPathRooted($entry.path) -or
            $entry.path -match '(^|[\\/])\.\.($|[\\/])|[:*?"<>|]' -or
            $entry.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or $entry.size -lt 0 -or $names.ContainsKey($entry.path)) {
            throw 'INSTALL_MANIFEST_INVALID'
        }
        foreach ($part in ($entry.path -split '[\\/]')) {
            if (!$part -or $part.TrimEnd(' ', '.') -ne $part) { throw 'INSTALL_MANIFEST_INVALID' }
        }
        $names[$entry.path] = $true
    }
    if ($Stage -eq 'Upgrade' -and !$PreviousInstallerPath) {
        Write-Output 'UPGRADE=NOT_RUN; previous installer required'
        exit 0
    }
    if (!$Execute) {
        Write-Output 'PREFLIGHT_ONLY; installation, GUI, upgrade and uninstall NOT_RUN'
        exit 0
    }
    # The operator must identify a dedicated, disposable Windows TEST account.
    # No credential APIs are called, and the application is never auto-launched.
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if (!$TestUserSid -or $TestUserSid -ne $sid) { throw 'INSTALL_TEST_USER_REQUIRED' }
    $local = [Environment]::GetFolderPath('LocalApplicationData')
    $appRoot = Join-Path $local 'Programs\PaperLens'
    $dataRoot = Join-Path $local 'PaperLens'
    $evidence = Join-Path $local 'PaperLens-InstallAcceptance'
    $registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{9D2B16D3-73C6-40B5-A5B0-63D10525E849}_is1'
    $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'PaperLens.lnk'
    $desktop = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'PaperLens.lnk'
    if (Get-Process -Name PaperLens -ErrorAction SilentlyContinue) { throw 'INSTALL_APP_RUNNING' }
    NoLinks $local -Shallow
    if ($Stage -eq 'Install') {
        foreach ($path in @($appRoot,$dataRoot,$evidence,$registry,$menu,$desktop)) {
            if (Test-Path -LiteralPath $path) { throw 'INSTALL_EXISTING_STATE_REFUSED' }
        }
        New-Item -ItemType Directory -Path $evidence,$dataRoot | Out-Null
        $owner = @{ sid=$sid; installer=$InstallerSHA256; state='prepared' }
        $owner | ConvertTo-Json | Set-Content (Join-Path $evidence 'owner.json') -Encoding UTF8
        [IO.File]::WriteAllText((Join-Path $dataRoot 'acceptance-synthetic.txt'), [guid]::NewGuid().ToString())
    } else {
        if (!(Test-Path -LiteralPath $evidence)) { throw 'INSTALL_OWNERSHIP_REQUIRED' }
        NoLinks $evidence
        $owner = Get-Content (Join-Path $evidence 'owner.json') -Raw | ConvertFrom-Json
        if ($owner.sid -ne $sid -or $owner.state -notin @('installed','uninstalled')) { throw 'INSTALL_OWNERSHIP_REQUIRED' }
        if ($Stage -in @('Upgrade','Uninstall') -and $owner.state -ne 'installed') { throw 'INSTALL_OWNERSHIP_REQUIRED' }
        if ($Stage -eq 'Upgrade' -and ($owner.installer -ne $PreviousInstallerSHA256 -or $InstallerSHA256 -eq $PreviousInstallerSHA256)) {
            throw 'INSTALL_PREVIOUS_VERSION_MISMATCH'
        }
        if ($Stage -ne 'Upgrade' -and $owner.installer -ne $InstallerSHA256) { throw 'INSTALL_HASH_MISMATCH' }
    }
    $before = @(DataHashes)
    $stamp = [guid]::NewGuid().ToString('N')
    $log = Join-Path $evidence ($Stage + '-' + $stamp + '.log')
    if ($Stage -eq 'Uninstall') {
        NoLinks $appRoot
        # Only the uninstaller created by this test installation may run.
        $uninstaller = Join-Path $appRoot 'unins000.exe'
        if ((Get-FileHash -LiteralPath $uninstaller -Algorithm SHA256).Hash -ne $owner.uninstaller) { throw 'INSTALL_UNINSTALLER_MISMATCH' }
        $process = Start-Process -FilePath $uninstaller -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$log`"" -WindowStyle Hidden -Wait -PassThru
    } else {
        # Check before the installer can write through the target or an ancestor.
        NoLinks $appRoot -AllowMissing
        $process = Start-Process -FilePath $installer -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS=desktopicon /DIR=`"$appRoot`" /LOG=`"$log`"" -WindowStyle Hidden -Wait -PassThru
    }
    if ($process.ExitCode -ne 0) { throw "INSTALL_PROCESS_FAILED:$($process.ExitCode); log=$log" }
    $after = @(DataHashes)
    if (Compare-Object $before $after) { throw 'INSTALL_DATA_CHANGED' }
    if ($Stage -eq 'Uninstall') {
        foreach ($path in @((Join-Path $appRoot 'PaperLens.exe'),$registry,$menu,$desktop)) {
            if (Test-Path -LiteralPath $path) { throw 'INSTALL_UNINSTALL_INCOMPLETE' }
        }
        $owner.state = 'uninstalled'
    } else {
        NoLinks $appRoot
        foreach ($entry in $manifest) {
            $file = Get-Item -LiteralPath (Join-Path $appRoot $entry.path)
            if ($file.Length -ne $entry.size -or (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne $entry.sha256) {
                throw 'INSTALL_RESOURCE_MISMATCH'
            }
        }
        if (!(Test-Path $registry)) { throw 'INSTALL_REGISTRATION_MISSING' }
        $shell = New-Object -ComObject WScript.Shell
        foreach ($link in @($menu,$desktop)) {
            if (!(Test-Path -LiteralPath $link) -or $shell.CreateShortcut($link).TargetPath -ne (Join-Path $appRoot 'PaperLens.exe')) {
                throw 'INSTALL_SHORTCUT_INVALID'
            }
        }
        $owner = @{ sid=$sid; installer=$InstallerSHA256; state='installed'; uninstaller=(Get-FileHash (Join-Path $appRoot 'unins000.exe') -Algorithm SHA256).Hash }
    }
    $owner | ConvertTo-Json | Set-Content (Join-Path $evidence 'owner.json') -Encoding UTF8
    @{ stage=$Stage; process_exit=0; synthetic_data_preserved=$true; resource_count=$manifest.Count; gui='NOT_RUN'; log=$log } |
        ConvertTo-Json | Set-Content (Join-Path $evidence ($Stage + '-' + $stamp + '.json')) -Encoding UTF8
    Write-Output "$Stage completed; evidence=$evidence; GUI and real model NOT_RUN"
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
