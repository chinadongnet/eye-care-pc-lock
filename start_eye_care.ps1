# 护眼锁屏助手 — PowerShell 启动脚本
# 用法:
#   .\start_eye_care.ps1
#   .\start_eye_care.ps1 -Once
#   .\start_eye_care.ps1 -Interval 15 -BreakSeconds 20
#   .\start_eye_care.ps1 -Lock -AllowSkip
#   .\start_eye_care.ps1 -ShowConsole   # 显示控制台日志

[CmdletBinding()]
param(
    [double]$Interval,
    [int]$BreakSeconds,
    [switch]$Lock,
    [switch]$AllowSkip,
    [switch]$Once,
    [switch]$ShowConsole,
    [string]$Config
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$pythonw = Get-Command pythonw -ErrorAction SilentlyContinue
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $pythonw -and -not $python) {
    Write-Error "未找到 python / pythonw。请安装 Python 3.11+ 并加入 PATH。"
    exit 1
}

$exe = if ($ShowConsole -and $python) {
    $python.Source
} elseif ($pythonw) {
    $pythonw.Source
} else {
    $python.Source
}

$argsList = @("$PSScriptRoot\eye_care.py")
if ($Config) { $argsList += @("--config", $Config) }
if ($PSBoundParameters.ContainsKey("Interval")) { $argsList += @("--interval", "$Interval") }
if ($PSBoundParameters.ContainsKey("BreakSeconds")) { $argsList += @("--break-seconds", "$BreakSeconds") }
if ($Lock) { $argsList += "--lock" }
if ($AllowSkip) { $argsList += "--allow-skip" }
if ($Once) { $argsList += "--once" }

if ($ShowConsole) {
    & $exe @argsList
} else {
    Start-Process -FilePath $exe -ArgumentList $argsList -WorkingDirectory $PSScriptRoot | Out-Null
    Write-Host "已在后台启动护眼锁屏助手。系统托盘右键可退出；或运行 .\stop_eye_care.ps1"
}
