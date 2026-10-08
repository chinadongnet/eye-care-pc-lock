@echo off
REM Windows 安装入口。双击或带参数调用时走 PowerShell 启动脚本：
REM 检查 tkinter、后台只启动一份、写入登录自启（不含 --once 等一次性参数）。
setlocal
cd /d "%~dp0"

where powershell >nul 2>&1
if errorlevel 1 (
  echo 未找到 PowerShell，无法完成 Windows 安装启动。
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_eye_care.ps1" %*
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" pause
exit /b %EXITCODE%
