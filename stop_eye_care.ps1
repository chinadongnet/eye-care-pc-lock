# 结束本安装目录里的护眼锁屏助手。
# 只匹配命令行中的本目录 eye_care.py，不结束其它目录的同名脚本。
# 用法:
#   .\stop_eye_care.ps1
#   .\stop_eye_care.ps1 -DisableAutostart

[CmdletBinding()]
param(
    [switch]$DisableAutostart,
    [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$ScriptPath = Join-Path $Root "eye_care.py"
$AutostartName = "护眼锁屏助手"

function Test-OurEyeCare([string]$CommandLine) {
    if (-not $CommandLine) { return $false }
    $normCmd = $CommandLine.Replace("/", "\")
    $normScript = $ScriptPath.Replace("/", "\")
    return ($normCmd.IndexOf($normScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
}

$targets = @()
if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
    $targets = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'py.exe' OR Name = 'pyw.exe'" |
        Where-Object { Test-OurEyeCare $_.CommandLine })
}

if (-not $targets -or $targets.Count -eq 0) {
    if (-not $Quiet) { Write-Host "未发现本目录正在运行的 eye_care.py。" }
} else {
    foreach ($proc in $targets) {
        if (-not $Quiet) { Write-Host "正在结束本目录 PID $($proc.ProcessId) ..." }
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if (-not $Quiet) { Write-Host "已请求退出。" }
}

if ($DisableAutostart) {
    $startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
    foreach ($name in @("$AutostartName.lnk", "$AutostartName.bat")) {
        $path = Join-Path $startup $name
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            Write-Host "已关闭登录自启: $path"
        }
    }
} elseif (-not $Quiet) {
    Write-Host "登录自启未改动。若要关闭：.\stop_eye_care.ps1 -DisableAutostart  或托盘里取消「开机自动启动」。"
}
