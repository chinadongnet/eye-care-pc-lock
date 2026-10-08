# 护眼锁屏助手 — Windows 启动
# 对齐本机已部署的 macOS：确认解释器带 tkinter，只启动一份，并写入登录自启。
# 用法:
#   .\start_eye_care.ps1
#   .\start_eye_care.ps1 -Once
#   .\start_eye_care.ps1 -Interval 15 -BreakSeconds 20
#   .\start_eye_care.ps1 -Config .\custom.json
#   .\start_eye_care.ps1 -ShowConsole
#   .\start_eye_care.ps1 -NoAutostart
#   .\start_eye_care.ps1 -DemoSeconds 10 -VerboseLog

[CmdletBinding()]
param(
    [double]$Interval,
    [int]$BreakSeconds,
    [switch]$Lock,
    [switch]$AllowSkip,
    [switch]$Once,
    [switch]$ShowConsole,
    [string]$Config,
    [double]$DemoSeconds,
    [switch]$VerboseLog,
    [switch]$NoAutostart
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
Set-Location -LiteralPath $Root
$ScriptPath = Join-Path $Root "eye_care.py"
$AutostartName = "护眼锁屏助手"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Error "找不到 $ScriptPath"
    exit 1
}

function Test-StoreStub([string]$Exe) {
    $leaf = [System.IO.Path]::GetFileName($Exe)
    return ($Exe -like "*\WindowsApps\*" -and ($leaf -eq "python.exe" -or $leaf -eq "pythonw.exe"))
}

function Test-Tk([string]$Exe, [string[]]$Prefix) {
    $arg = @()
    if ($Prefix) { $arg += $Prefix }
    $arg += @("-c", "import tkinter")
    & $Exe @arg *> $null
    return ($LASTEXITCODE -eq 0)
}

function Find-Python {
    $found = New-Object System.Collections.Generic.List[object]
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py -and -not (Test-StoreStub $py.Source)) {
        $found.Add(@{ Exe = $py.Source; Prefix = @("-3"); Leaf = "py.exe" })
    }
    foreach ($name in @("python", "pythonw")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and -not (Test-StoreStub $cmd.Source)) {
            $found.Add(@{ Exe = $cmd.Source; Prefix = @(); Leaf = [System.IO.Path]::GetFileName($cmd.Source).ToLowerInvariant() })
        }
    }

    $console = $null
    $windowless = $null
    foreach ($item in $found) {
        if (-not (Test-Tk $item.Exe $item.Prefix)) { continue }
        $dir = Split-Path -Parent $item.Exe
        if ($item.Leaf -eq "py.exe") {
            if (-not $console) { $console = $item }
            $pyw = Join-Path $dir "pyw.exe"
            if ((Test-Path -LiteralPath $pyw) -and -not $windowless) {
                $windowless = @{ Exe = $pyw; Prefix = @("-3"); Leaf = "pyw.exe" }
            }
        } elseif ($item.Leaf -eq "python.exe") {
            if (-not $console) { $console = $item }
            $pyw = Join-Path $dir "pythonw.exe"
            if ((Test-Path -LiteralPath $pyw) -and -not (Test-StoreStub $pyw) -and -not $windowless) {
                $windowless = @{ Exe = $pyw; Prefix = @(); Leaf = "pythonw.exe" }
            }
        } elseif (-not $windowless) {
            $windowless = $item
        }
    }
    return @{ Console = $console; Windowless = $windowless }
}

function Quote-WinArg([string]$Text) {
    if ($Text -match '[\s"]') {
        return '"' + ($Text -replace '"', '\"') + '"'
    }
    return $Text
}

function Install-LoginAutostart([hashtable]$Runtime, [string[]]$PersistArgs) {
    $startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
    if (-not (Test-Path -LiteralPath $startup)) {
        New-Item -ItemType Directory -Path $startup | Out-Null
    }
    $lnk = Join-Path $startup "$AutostartName.lnk"
    $bat = Join-Path $startup "$AutostartName.bat"
    $pieces = @()
    if ($Runtime.Prefix) {
        $pieces += @($Runtime.Prefix | ForEach-Object { Quote-WinArg $_ })
    }
    $pieces += @($PersistArgs | ForEach-Object { Quote-WinArg $_ })
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($lnk)
    $shortcut.TargetPath = $Runtime.Exe
    $shortcut.Arguments = ($pieces -join " ")
    $shortcut.WorkingDirectory = $Root
    $shortcut.WindowStyle = 7
    $shortcut.Description = "护眼锁屏助手 — 登录后自动启动"
    $shortcut.Save()
    if (Test-Path -LiteralPath $bat) {
        Remove-Item -LiteralPath $bat -Force
    }
    Write-Host "已写入登录自启（下次登录生效，不会再启动第二份）：$lnk"
}

$runtimes = Find-Python
if (-not $runtimes.Console -and -not $runtimes.Windowless) {
    Write-Error "未找到带 tkinter 的 Python 3。请安装 python.org 的 Python 3.11+（勾选 tcl/tk 与 Add to PATH），不要只用 Microsoft Store 占位符。"
    exit 1
}

$runArgs = @($ScriptPath)
$persistArgs = @($ScriptPath)
if ($Config) {
    $runArgs += @("--config", $Config)
    $persistArgs += @("--config", $Config)
}
if ($PSBoundParameters.ContainsKey("Interval")) {
    $runArgs += @("--interval", "$Interval")
    $persistArgs += @("--interval", "$Interval")
}
if ($PSBoundParameters.ContainsKey("BreakSeconds")) {
    $runArgs += @("--break-seconds", "$BreakSeconds")
    $persistArgs += @("--break-seconds", "$BreakSeconds")
}
if ($Lock) {
    $runArgs += "--lock"
    $persistArgs += "--lock"
}
if ($AllowSkip) {
    $runArgs += "--allow-skip"
    $persistArgs += "--allow-skip"
}
# 与 macOS 启动脚本一致：一次性参数不写入登录自启
if ($Once) { $runArgs += "--once" }
if ($PSBoundParameters.ContainsKey("DemoSeconds")) { $runArgs += @("--demo-seconds", "$DemoSeconds") }
if ($VerboseLog) { $runArgs += "-v" }

$stopScript = Join-Path $Root "stop_eye_care.ps1"
if (Test-Path -LiteralPath $stopScript) {
    & $stopScript -Quiet
}

if ($ShowConsole) {
    $runtime = $runtimes.Console
    if (-not $runtime) { $runtime = $runtimes.Windowless }
    & $runtime.Exe @(@($runtime.Prefix) + @($runArgs))
    exit $LASTEXITCODE
}

$runtime = $runtimes.Windowless
if (-not $runtime) { $runtime = $runtimes.Console }
$quoted = @(@($runtime.Prefix) + @($runArgs)) | ForEach-Object { Quote-WinArg $_ }
Start-Process -FilePath $runtime.Exe -ArgumentList ($quoted -join " ") -WorkingDirectory $Root | Out-Null
Write-Host "已在后台启动护眼锁屏助手。系统托盘右键可退出；或运行 .\stop_eye_care.ps1"

if (-not $NoAutostart) {
    $autoRuntime = $runtimes.Windowless
    if (-not $autoRuntime) { $autoRuntime = $runtimes.Console }
    Install-LoginAutostart $autoRuntime $persistArgs
}
