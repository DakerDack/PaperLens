#ifndef BundleDir
#error BundleDir required
#endif
#ifndef WebView2Installer
#error WebView2Installer required
#endif
#ifndef OutputPath
#error OutputPath required
#endif
[Setup]
AppId={{9D2B16D3-73C6-40B5-A5B0-63D10525E849}
AppName=PaperLens
AppVersion=0.1.0
AppPublisher=PaperLens contributors
DefaultDirName={localappdata}\Programs\PaperLens
DefaultGroupName=PaperLens
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir={#OutputPath}
OutputBaseFilename=PaperLens-0.1.0-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\PaperLens.exe
LicenseFile={#BundleDir}\LICENSE
InfoAfterFile={#BundleDir}\UNINSTALL-NOTE.txt
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebView2Installer}"; DestName: "WebView2Runtime.exe"; Flags: dontcopy
[Icons]
Name: "{userprograms}\PaperLens"; Filename: "{app}\PaperLens.exe"
Name: "{userdesktop}\PaperLens"; Filename: "{app}\PaperLens.exe"; Tasks: desktopicon
[Code]
function WebView2Present(): Boolean;
var Version: String;
begin
  Result := (RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
  if not Result then
    Result := (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;
function NetFrameworkPresent(): Boolean;
var Release: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM32, 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', Release) and (Release >= 528040);
end;
function PaperLensRunning(): Boolean;
var Locator, Services, Items: Variant;
begin
  Result := True;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('', 'root\CIMV2');
    Items := Services.ExecQuery('SELECT Name FROM Win32_Process WHERE Name = ''PaperLens.exe''');
    Result := Items.Count > 0;
  except
    Result := True;
  end;
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  if PaperLensRunning() then begin
    Result := 'Please close PaperLens normally before installing. Process verification must be available.';
    exit;
  end;
  if not NetFrameworkPresent() then begin
    Result := '.NET Framework 4.8 or later is required. Update Windows and retry.';
    exit;
  end;
  if not WebView2Present() then begin
    ExtractTemporaryFile('WebView2Runtime.exe');
    if not Exec(ExpandConstant('{tmp}\WebView2Runtime.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) then begin
      Result := 'WebView2 installation could not start.';
      exit;
    end;
    if Code = 3010 then begin
      NeedsRestart := True;
      Result := 'Restart Windows to complete WebView2 installation, then run this installer again.';
      exit;
    end;
    if (Code <> 0) or not WebView2Present() then
      Result := 'WebView2 installation failed. Error code: ' + IntToStr(Code);
  end;
end;
function InitializeUninstall(): Boolean;
begin
  Result := not PaperLensRunning();
  if not Result then MsgBox('Close PaperLens normally before uninstalling.', mbError, MB_OK);
end;
