param(
    [string]$AppVersion = '0.3.0',
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'release'),
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$buildRoot = Join-Path $appRoot 'build'
$stageRoot = Join-Path $buildRoot 'stage'
$python = Join-Path $appRoot '.venv/Scripts/python.exe'
$compiler = Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 7/ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { $compiler = $found.Source }
    else { throw '请安装 Inno Setup 7，或把 ISCC.exe 加入 PATH。' }
}
if ($AppVersion -ne '0.3.0') { throw '请先同步 backend/app.py 和 desktop/PaperLoop.Desktop.csproj 的版本号。' }
New-Item -ItemType Directory -Force -Path $buildRoot, $OutputDirectory | Out-Null
Push-Location $appRoot
try {
    if (-not $SkipBuild) {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
        & $python -m PyInstaller --noconfirm --distpath build/backend --workpath build/pyinstaller desktop/PaperLoop.Backend.spec
        if ($LASTEXITCODE -ne 0) { throw '后端打包失败。' }
        & (Join-Path $appRoot 'desktop/Build-Desktop.ps1') -OutputDirectory (Join-Path $buildRoot 'desktop') -AppVersion $AppVersion
        if ($LASTEXITCODE -ne 0) { throw '桌面程序构建失败。' }
    }
    # Only remove the exact generated staging directory, never data or user paths.
    $expectedStage = [IO.Path]::GetFullPath((Join-Path $appRoot 'build/stage'))
    if ([IO.Path]::GetFullPath($stageRoot) -ne $expectedStage) { throw '非法构建目录。' }
    if (Test-Path -LiteralPath $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $stageRoot, (Join-Path $stageRoot 'backend'), (Join-Path $stageRoot 'prerequisites') | Out-Null
    Copy-Item -Path (Join-Path $buildRoot 'desktop/*') -Destination $stageRoot -Recurse
    Copy-Item -Path (Join-Path $buildRoot 'backend/PaperLoop.Backend/*') -Destination (Join-Path $stageRoot 'backend') -Recurse
    # Windows 10+ supplies UCRT. Copying its system DLL into the install tree
    # can be held open by Windows security scanning and abort Inno's rename.
    $systemUcrtCopy = Join-Path $stageRoot 'backend/_internal/ucrtbase.dll'
    if (Test-Path -LiteralPath $systemUcrtCopy) { Remove-Item -LiteralPath $systemUcrtCopy -Force }
    'PaperLoop Windows distribution 0.3.0' | Set-Content -LiteralPath (Join-Path $stageRoot 'PaperLoop.distributed') -Encoding utf8
    Copy-Item -LiteralPath (Join-Path $appRoot 'INSTALL.md') -Destination (Join-Path $stageRoot '使用说明.md')

    $bootstrapper = Join-Path $buildRoot 'prerequisites/MicrosoftEdgeWebview2Setup.exe'
    if (-not (Test-Path -LiteralPath $bootstrapper)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $bootstrapper) | Out-Null
        Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $bootstrapper
    }
    $signature = Get-AuthenticodeSignature -FilePath $bootstrapper
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw '微软 WebView2 引导安装程序签名验证失败，停止打包。'
    }
    Copy-Item -LiteralPath $bootstrapper -Destination (Join-Path $stageRoot 'prerequisites/MicrosoftEdgeWebview2Setup.exe')
    & $python desktop/collect_notices.py $stageRoot
    if ($LASTEXITCODE -ne 0) { throw '第三方说明收集失败。' }
    foreach ($required in @('PaperLoop.Desktop.exe','coreclr.dll','backend/PaperLoop.Backend.exe','backend/_internal/dist/index.html')) {
        if (-not (Test-Path -LiteralPath (Join-Path $stageRoot $required))) { throw "安装包缺少 $required" }
    }
    & $compiler "/DStageDir=$stageRoot" "/DOutputDir=$([IO.Path]::GetFullPath($OutputDirectory))" "/DAppVersion=$AppVersion" (Join-Path $appRoot 'desktop/installer.iss')
    if ($LASTEXITCODE -ne 0) { throw '安装包编译失败。' }
    $installer = Join-Path $OutputDirectory "PaperLoop-Setup-$AppVersion-win-x64.exe"
    $hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($installer))" | Set-Content -LiteralPath (Join-Path $OutputDirectory 'SHA256SUMS.txt') -Encoding ascii
    Get-Item -LiteralPath $installer | Select-Object FullName, Length
} finally { Pop-Location }
