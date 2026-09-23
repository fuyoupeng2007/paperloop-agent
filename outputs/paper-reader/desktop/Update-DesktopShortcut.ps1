param(
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'PaperLoop.lnk')
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$programPath = Join-Path $PSScriptRoot 'dist\PaperLoop.Desktop.exe'
$iconPath = Join-Path $PSScriptRoot 'assets\paperloop.ico'
$shortcutFullPath = [IO.Path]::GetFullPath($ShortcutPath)
if ([IO.Path]::GetExtension($shortcutFullPath) -ne '.lnk') {
    throw 'ShortcutPath must end with .lnk.'
}
foreach ($requiredFile in @($programPath, $iconPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required application file is missing: $requiredFile"
    }
}
if (-not (Test-Path -LiteralPath ([IO.Path]::GetDirectoryName($shortcutFullPath)) -PathType Container)) {
    throw 'The shortcut destination directory does not exist.'
}

$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutFullPath)
$shortcut.TargetPath = $programPath
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = 'PaperLoop - Read, translate and understand research papers'
$shortcut.WindowStyle = 1
$shortcut.Arguments = ''
$shortcut.Save()

$verified = $shortcutShell.CreateShortcut($shortcutFullPath)
[pscustomobject]@{
    Shortcut = $shortcutFullPath
    Target = $verified.TargetPath
    Icon = $verified.IconLocation
    WorkingDirectory = $verified.WorkingDirectory
}
