# 护眼锁屏助手

Windows / macOS 定时护眼工具：默认每 **20 分钟**弹出全屏置顶遮罩，提示按 **20-20-20** 法则远眺休息 **20 秒**，倒计时结束后自动关闭。

- 运行环境：Windows 10+ / Windows Server 2022，或 macOS 12+（Apple Silicon / Intel），Python 3.11+（需 tkinter）
- **无需管理员权限**；默认**无需 pip**（标准库：`tkinter` + `ctypes` + `subprocess`）
- macOS **菜单栏状态项**在已有 PyObjC AppKit 时自动启用（如 Anaconda）；否则仍用 HUD
- 主交互方式：全屏遮罩（不必每次输入密码）；可选真正锁屏

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `eye_care.py` | 主程序 |
| `config.json` | 默认配置（间隔、时长、文案等） |
| `start_eye_care.bat` | Windows 双击启动（后台） |
| `start_eye_care.ps1` | Windows PowerShell 启动（可传参数） |
| `stop_eye_care.ps1` | Windows 结束进程 |
| `start_eye_care.sh` | macOS：LaunchAgent 后台启动（无 Terminal）+ 登录自启；`--show-console` 前台 |
| `stop_eye_care.sh` | macOS：bootout LaunchAgent + 结束进程；`--disable-autostart` 关闭自启 |
| `start_eye_care.command` | macOS Finder 双击启动（显示控制台） |
| `requirements.txt` | 说明：默认无强制依赖；可选 macOS AppKit |

---

## 快速开始

### Windows

```bat
python eye_care.py
```

或双击 `start_eye_care.bat`，或：

```powershell
.\start_eye_care.ps1
.\start_eye_care.ps1 -ShowConsole
```

启动后：

- 出现**系统托盘图标**（数字倒计时）
- 右键托盘：`立即开始休息` / `开机自动启动` / `关于` / `退出`
- 悬浮 HUD 也可右键打开同一套菜单
- 控制台模式下可用 **Ctrl+C** 退出

### macOS

建议使用带 Tcl/Tk 的 Python（[python.org](https://www.python.org/downloads/) 安装包，或 `brew install python-tk`）。

```bash
chmod +x start_eye_care.sh stop_eye_care.sh start_eye_care.command
# 推荐：LaunchAgent 后台启动（无 Terminal 窗口，并写入登录自启）
./start_eye_care.sh
# 前台调试（当前终端）：
./start_eye_care.sh --show-console
# 或直接：
python3 eye_care.py
```

`./start_eye_care.sh` 默认通过 **LaunchAgent**（`net.chinadong.eye-care`）由 launchd 托管，**不弹出 Terminal**；与菜单「开机自动启动」共用同一 plist。

也可在 Finder 中双击 `start_eye_care.command`（会打开终端窗口，仅适合调试）。

启动后：

- 屏幕**右上角菜单栏**出现状态项（剩余分钟数字，或休息中显示「休」；护眼色前景）
- 点击菜单栏图标：`立即开始休息` / `开机自动启动`（勾选） / `关于` / `退出`
- 屏幕右下角仍保留**悬浮倒计时 HUD**；**右键**（或 **Control+点击**）HUD 可打开同一套菜单
- 菜单栏依赖本机 Python 的 **PyObjC AppKit**（Anaconda 通常已有）。若没有 AppKit，程序仍运行，仅 HUD 菜单可用；可选 `pip install pyobjc-framework-Cocoa`

### 立即测一次遮罩（测完自动退出）

```bat
python eye_care.py --once --break-seconds 5
```

```bash
python3 eye_care.py --once --break-seconds 5
```

### 缩短间隔做联调

```bash
python3 eye_care.py --demo-seconds 10 --break-seconds 5 -v
```

---

## 如何停止

### Windows

1. **托盘右键 → 退出**（推荐）
2. 控制台窗口中 **Ctrl+C**
3. PowerShell：`.\stop_eye_care.ps1`

### macOS

1. **菜单栏图标 → 退出**（推荐；或 HUD 右键 → 退出）
2. `./stop_eye_care.sh`（`launchctl bootout` + 结束残留进程；**默认保留**登录自启 plist）
3. 关闭登录自启并停止：`./stop_eye_care.sh --disable-autostart`
4. 控制台模式下 **Ctrl+C**

退出时会关闭遮罩并清理菜单栏状态项 / HUD，避免残留置顶窗口。

---

## 配置

编辑同目录下的 `config.json`：

```json
{
  "interval_minutes": 20,
  "break_seconds": 20,
  "lock_workstation": false,
  "allow_skip": false,
  "title": "护眼休息",
  "message": "请远眺约 6 米（20 英尺）外，放松眼睛。\n遵循 20-20-20 法则：每 20 分钟 · 看向 20 英尺外 · 持续 20 秒。"
}
```

| 字段 | 含义 |
|------|------|
| `interval_minutes` | 两次休息之间的间隔（分钟） |
| `break_seconds` | 遮罩倒计时时长（秒） |
| `lock_workstation` | `true` 时在休息开始真正锁屏（见下节） |
| `allow_skip` | `true` 时允许倒计时未结束就跳过（会二次确认）；默认强制休息满时长 |
| `title` / `message` | 遮罩上的中文标题与说明 |

也可用命令行覆盖（不改文件）：

```bash
python3 eye_care.py --interval 15 --break-seconds 30
python3 eye_care.py --lock
python3 eye_care.py --allow-skip
python3 eye_care.py -c /path/to/config.json
```

---

## 全屏遮罩 vs 真正锁屏

**默认（推荐）**：只显示全屏置顶遮罩，阻断对其它窗口的操作，**不需要重新输入密码**。

若你希望休息时**锁定会话**（离开工位更安全）：

1. 在 `config.json` 将 `"lock_workstation": true`，或启动时加 `--lock`
2. **Windows**：调用 `ctypes.windll.user32.LockWorkStation`
3. **macOS**：通过 `osascript` 向 System Events 发送 **Control+Command+Q**（锁定屏幕）。可能需要在「系统设置 → 隐私与安全性 → 辅助功能」中授权运行该脚本的终端 / Python
4. 解锁后若倒计时尚未结束，遮罩仍可能继续显示直至结束

注意：真正锁屏后必须输入密码/PIN/Touch ID 才能回来，不适合高频强制休息场景。

---

## 加入开机启动（可选）

### Windows

**托盘 / HUD 菜单**：勾选「开机自动启动」即可写入当前用户 Startup（保留持久 CLI 参数，排除 `--once` / `--demo-seconds` / `-v`）。

也可手动：

1. `Win + R`，输入 `shell:startup`，回车
2. 为 `start_eye_care.bat` 创建快捷方式，放入该文件夹

### macOS

**推荐**：运行 `./start_eye_care.sh` 即写入并加载 LaunchAgent（后台运行 + 登录自启）。

**菜单栏 / HUD 菜单**：勾选「开机自动启动」会写入同一用户 LaunchAgent：

| 项 | 值 |
|----|----|
| 路径 | `~/Library/LaunchAgents/net.chinadong.eye-care.plist` |
| Label | `net.chinadong.eye-care` |
| 启停 | `./start_eye_care.sh` / `./stop_eye_care.sh` |
| 或 | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/net.chinadong.eye-care.plist` |
| 或 | `launchctl bootout gui/$(id -u)/net.chinadong.eye-care` |

同样保留持久 CLI 参数（排除 `--once` / `--demo-seconds` / verbose）。

**注意（macOS TCC）**：若应用放在 `Desktop` / `Documents` / `Downloads`，LaunchAgent 无法直接读取该路径。`start_eye_care.sh` 与菜单「开机自动启动」会把 `eye_care.py` / `config.json` 同步到 `~/Library/Application Support/eye-care-lock/` 再由 launchd 运行（无 Terminal）。日志在该目录的 `eye_care.log`。

关闭自启：菜单取消勾选，或 `./stop_eye_care.sh --disable-autostart`（删除 plist 并 bootout）。

---

## 多显示器

- **Windows**：枚举所有显示器，每块屏幕铺满置顶遮罩
- **macOS**：优先通过 AppKit `NSScreen`（PyObjC）枚举多屏并换算为 Tk 坐标；失败时回退到 tkinter 主屏（不使用 ctypes `CGDisplayBounds`，避免 Apple Silicon 上 CGRect 错算）
- 每块屏幕显示**同一套**完整遮罩（标题 / 文案 / 倒计时）；系统主屏优先 grab 焦点。菜单「立即开始休息」与定时器走同一 `BreakOverlay`

---

## 行为说明

1. 启动后在后台计时（Windows 托盘 / macOS 菜单栏 + HUD）
2. 到达间隔 → 全屏遮罩 + 中文护眼提示 + 倒计时
3. 默认**不可提前关闭**；倒计时结束后约 1.5 秒自动关闭，也可点「我已休息好」
4. 若开启 `allow_skip`，可点「跳过」并二次确认
5. 干净退出：菜单退出或 Ctrl+C，遮罩与状态 UI 一并清理

---

## 常见问题

**Q: 提示没有 tkinter？**  
- Windows：使用 [python.org](https://www.python.org/downloads/) 官方安装包，安装时不要取消 Tcl/Tk。  
- macOS：使用 python.org 安装包，或 `brew install python-tk`；确认 `python3 -c "import tkinter"` 成功。

**Q: Windows 托盘图标看不见？**  
查看任务栏「隐藏的图标」；或用 `start_eye_care.ps1 -ShowConsole` / `python eye_care.py -v` 看日志，再用 Ctrl+C 退出。也可右键悬浮 HUD。

**Q: macOS 锁屏没反应？**  
`--lock` 依赖辅助功能权限。到「系统设置 → 隐私与安全性 → 辅助功能」允许 Terminal / iTerm / Python。未授权时仅全屏遮罩仍可用。

**Q: Apple Silicon MacBook 注意事项？**  
使用 arm64 的 Python 3.12+（或 3.11+）并带 tkinter 即可；本程序无原生扩展、无 Rosetta 硬性要求。若 Homebrew Python 缺 Tk，安装 `python-tk` 或改用 python.org 安装包。

**Q: 能否在 Linux 用？**  
Linux 为有限模式（主屏遮罩 + HUD 菜单，无系统托盘 / 无锁屏 API）。完整支持面向 Windows 与 macOS（含菜单栏）。

**Q: macOS 菜单栏图标不出现？**  
需要当前解释器能 `import AppKit`（PyObjC）。Anaconda 一般已自带；否则 `pip install pyobjc-framework-Cocoa` 后重启应用。无 AppKit 时仍有右下角 HUD 右键菜单。菜单栏状态项**不需要**辅助功能权限。

---

## 许可与隐私

本地运行，无网络请求，无账号，不采集数据。

## 状态菜单

- **立即开始休息**：马上进入一次护眼休息
- **开机自动启动**：Windows 写 Startup；macOS 写 LaunchAgent；再点一次取消
- **关于 / 退出**
