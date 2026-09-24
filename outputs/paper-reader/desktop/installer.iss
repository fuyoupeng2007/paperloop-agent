#ifndef StageDir
  #error StageDir must point to the clean distribution staging directory.
#endif
#ifndef OutputDir
  #error OutputDir must point to the installer output directory.
#endif
#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif

[Setup]
AppId={{3606B2A9-527D-4F5F-9D3F-1467199AA8D8}
AppName=PaperLoop
AppVersion={#AppVersion}
AppPublisher=PaperLoop
AppPublisherURL=https://github.com/fuyoupeng2007/paperloop-agent
DefaultDirName={localappdata}\Programs\PaperLoop
DefaultGroupName=PaperLoop
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=PaperLoop-Setup-{#AppVersion}-win-x64
SetupIconFile=assets\paperloop.ico
UninstallDisplayIcon={app}\PaperLoop.Desktop.exe
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
AppMutex=PaperLoop.Desktop.Installed
UninstallDisplayName=PaperLoop
VersionInfoVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\prerequisites\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{userprograms}\PaperLoop"; Filename: "{app}\PaperLoop.Desktop.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\PaperLoop"; Filename: "{app}\PaperLoop.Desktop.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\PaperLoop.Desktop.exe"; Description: "{cm:LaunchProgram,PaperLoop}"; Flags: nowait postinstall skipifsilent

[Code]
function ValidWebViewVersion(const Root: Integer; const Key: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0');
end;

function HasWebView2(): Boolean;
var
  Key: String;
begin
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := ValidWebViewVersion(HKLM32, Key) or ValidWebViewVersion(HKCU, Key);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Bootstrapper: String;
begin
  Result := '';
  if HasWebView2() then
    Exit;

  ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  Bootstrapper := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
  WizardForm.StatusLabel.Caption := 'Installing Microsoft Edge WebView2 Runtime...';
  if not Exec(Bootstrapper, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := 'Unable to start Microsoft Edge WebView2 Runtime setup. Please run the installer again.';
    Exit;
  end;
  if (ResultCode <> 0) and (ResultCode <> 3010) then
  begin
    Result := Format('Microsoft Edge WebView2 Runtime setup failed (code %d). Check your Internet connection and try again.', [ResultCode]);
    Exit;
  end;
  NeedsRestart := ResultCode = 3010;
  if not HasWebView2() then
    Result := 'Microsoft Edge WebView2 Runtime was not detected after setup. Please install the Microsoft runtime and run PaperLoop setup again.';
end;

// User papers, API settings, annotations and WebView cache live outside {app}.
// No UninstallDelete entry is used: uninstalling preserves those user files.
