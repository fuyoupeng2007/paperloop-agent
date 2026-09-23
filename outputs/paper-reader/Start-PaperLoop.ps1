$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    Write-Host 'Creating the local Python environment...'
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment could not be created. Install Python 3.11 or newer and retry.' }
}
$appPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $appPython -c 'import fastapi, uvicorn, pdfplumber, pypdf, pypdfium2, rapidocr, onnxruntime, httpx, filelock'
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing Python dependencies...'
    & $appPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python dependencies could not be installed.' }
}
if (-not (Test-Path -LiteralPath 'dist\index.html')) {
    if (-not (Test-Path -LiteralPath 'node_modules')) {
        Write-Host 'Installing interface dependencies...'
        npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Interface dependencies could not be installed.' }
    }
    Write-Host 'Building the interface...'
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Interface build failed.' }
}
Write-Host ''
Write-Host 'PaperLoop is ready at http://127.0.0.1:8765'
Write-Host 'Keep this window open while reading. Press Ctrl+C to stop.'
& $appPython -m uvicorn backend.app:app --host 127.0.0.1 --port 8765 --workers 1
