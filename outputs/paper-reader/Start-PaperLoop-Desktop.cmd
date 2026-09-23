@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "%~dp0desktop\dist\PaperLoop.Desktop.exe" (
  echo 找不到桌面程序。请保留完整 PaperLoop 文件夹，或先运行 desktop\Build-Desktop.ps1。
  pause
  exit /b 1
)
start "" "%~dp0desktop\dist\PaperLoop.Desktop.exe"
exit /b 0
