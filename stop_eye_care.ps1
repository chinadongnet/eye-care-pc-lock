# 结束护眼锁屏助手进程（按命令行匹配 eye_care.py）
# 用法: .\stop_eye_care.ps1

$ErrorActionPreference = "Continue"
$targets = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like "*eye_care.py*") }

if (-not $targets) {
    Write-Host "未发现正在运行的 eye_care.py 进程。"
    exit 0
}

foreach ($p in $targets) {
    Write-Host "正在结束 PID $($p.ProcessId) ..."
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "已请求退出。"
