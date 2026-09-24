param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist'),
    [string]$AppVersion = '0.3.0'
)

$ErrorActionPreference = 'Stop'
$env:DOTNET_CLI_HOME = Join-Path $PSScriptRoot '.build-home'
$env:NUGET_PACKAGES = Join-Path $PSScriptRoot '.packages'
$env:DOTNET_NOLOGO = '1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
if ($AppVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'AppVersion 必须使用 0.3.0 这样的三段数字版本号。' }
dotnet publish (Join-Path $PSScriptRoot 'PaperLoop.Desktop.csproj') -c Release -r win-x64 --self-contained true "-p:Version=$AppVersion" -o $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw '桌面程序构建失败。' }
