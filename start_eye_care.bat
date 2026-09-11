@echo off
REM 护眼锁屏助手启动脚本（Windows）
REM 使用 pythonw 可隐藏控制台；若需看日志请改用 python。

setlocal
cd /d "%~dp0"

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "护眼锁屏助手" pythonw "%~dp0eye_care.py" %*
  echo 已在后台启动护眼锁屏助手。可在系统托盘右键退出。
  exit /b 0
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  start "护眼锁屏助手" python "%~dp0eye_care.py" %*
  echo 已启动护眼锁屏助手（带控制台窗口，Ctrl+C 可退出）。
  exit /b 0
)

echo 未找到 python / pythonw，请先安装 Python 3.11+ 并勾选 Add to PATH。
pause
exit /b 1
