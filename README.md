# 护眼锁屏助手

Windows 定时护眼工具：默认每 **20 分钟**弹出全屏置顶遮罩，提示按 **20-20-20** 法则远眺休息 **20 秒**，倒计时结束后自动关闭。

- 运行环境：Windows 10+ / Windows Server 2022，Python 3.11+（官方安装包自带 tkinter）
- **无需管理员权限**，**无需 pip 安装**（仅标准库）
- 主交互方式：全屏遮罩（不必每次输入密码）；可选真正锁屏

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `eye_care.py` | 主程序 |
| `config.json` | 默认配置（间隔、时长、文案等） |
| `start_eye_care.bat` | 双击启动（后台） |
| `start_eye_care.ps1` | PowerShell 启动（可传参数） |
| `stop_eye_care.ps1` | 结束进程 |
| `requirements.txt` | 说明：无第三方依赖 |

---

## 快速开始

在资源管理器中进入本目录，任选其一：

```bat
python eye_care.py
```

或双击：

```bat
start_eye_care.bat
```

或 PowerShell：

```powershell
.\start_eye_care.ps1
.\start_eye_care.ps1 -ShowConsole
```

启动后：

- Windows 下会出现**系统托盘图标**（默认应用图标）
- 右键托盘：`立即开始休息` / `关于` / `退出`
- 控制台模式下可用 **Ctrl+C** 退出

### 立即测一次遮罩（测完自动退出）

```bat
python eye_care.py --once --break-seconds 5
```

### 缩短间隔做联调

```bat
python eye_care.py --demo-seconds 10 --break-seconds 5 -v
```

---

## 如何停止

1. **托盘右键 → 退出**（推荐）
2. 控制台窗口中 **Ctrl+C**
3. PowerShell：

```powershell
.\stop_eye_care.ps1
```

退出时会关闭遮罩并移除托盘图标，避免残留置顶窗口。

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
| `lock_workstation` | `true` 时在休息开始调用 Windows `LockWorkStation`（真正锁屏） |
| `allow_skip` | `true` 时允许倒计时未结束就跳过（会二次确认）；默认强制休息满时长 |
| `title` / `message` | 遮罩上的中文标题与说明 |

也可用命令行覆盖（不改文件）：

```bat
python eye_care.py --interval 15 --break-seconds 30
python eye_care.py --lock
python eye_care.py --allow-skip
python eye_care.py -c D:\my\config.json
```

---

## 全屏遮罩 vs 真正锁屏

**默认（推荐）**：只显示全屏置顶遮罩，阻断对其它窗口的操作，**不需要重新输入密码**。

若你希望休息时**锁定 Windows 会话**（离开工位更安全）：

1. 在 `config.json` 将 `"lock_workstation": true`，或启动时加 `--lock`
2. 程序会在休息开始时调用 `ctypes.windll.user32.LockWorkStation`
3. 解锁后若倒计时尚未结束，遮罩仍可能继续显示直至结束

注意：真正锁屏后必须输入密码/PIN 才能回来，不适合高频强制休息场景。

---

## 加入开机启动（可选）

### 方法一：启动文件夹（简单）

1. `Win + R`，输入 `shell:startup`，回车
2. 为 `start_eye_care.bat` 创建快捷方式，放入该文件夹
3. 下次登录会自动后台启动

### 方法二：任务计划程序

1. 打开「任务计划程序」→ 创建基本任务
2. 触发器选「用户登录时」
3. 操作选「启动程序」，程序填：

   `pythonw.exe`

   参数填：

   `"C:\路径\到\eye_care.py"`

   起始于填项目目录

4. 勾选「使用最高权限」**不必**（本程序无需管理员）

---

## 多显示器

程序会枚举所有显示器，并在每块屏幕上铺满置顶遮罩。主屏显示完整倒计时与文案，副屏显示简洁「护眼休息中…」提示。

---

## 行为说明

1. 启动后在后台计时（托盘驻留）
2. 到达间隔 → 全屏遮罩 + 中文护眼提示 + 倒计时
3. 默认**不可提前关闭**；倒计时结束后约 1.5 秒自动关闭，也可点「我已休息好」
4. 若开启 `allow_skip`，可点「跳过」并二次确认
5. 干净退出：托盘退出或 Ctrl+C，遮罩与托盘一并清理

---

## 常见问题

**Q: 提示没有 tkinter？**  
使用 [python.org](https://www.python.org/downloads/) 官方 Windows 安装包，安装时不要取消 Tcl/Tk 相关选项。

**Q: 托盘图标看不见？**  
查看任务栏「隐藏的图标」；或用 `start_eye_care.ps1 -ShowConsole` / `python eye_care.py -v` 看日志，再用 Ctrl+C 退出。

**Q: 能否在 Linux/macOS 用？**  
v1 仅面向 Windows。非 Windows 环境可能进入有限演示模式（无托盘、无锁屏 API）。

---

## 许可与隐私

本地运行，无网络请求，无账号，不采集数据。
