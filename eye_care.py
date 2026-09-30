"""
护眼锁屏助手 — Windows / macOS 定时全屏休息提醒。

默认仅使用 Python 标准库（tkinter + ctypes + subprocess）。
macOS 菜单栏状态项：若本机已有 PyObjC AppKit（如 Anaconda），自动启用；
否则回退为悬浮 HUD 右键菜单（仍可零依赖运行）。
目标系统：Windows 10+ / Windows Server 2022，macOS 12+（Apple Silicon / Intel），Python 3.11+。
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 平台检测
# ---------------------------------------------------------------------------
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

try:
    import tkinter as tk
    from tkinter import font as tkfont
except ImportError as exc:  # pragma: no cover
    hint = (
        "官方 Windows 安装包通常已包含"
        if IS_WINDOWS
        else "macOS 可用 python.org 安装包或 Homebrew python-tk"
        if IS_MAC
        else "请安装带 Tcl/Tk 的 Python"
    )
    print(f"错误：无法导入 tkinter。{hint}。", file=sys.stderr)
    raise SystemExit(1) from exc

import ctypes

if IS_WINDOWS:
    from ctypes import wintypes


APP_NAME = "护眼锁屏助手"
APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"
LOG = logging.getLogger("eye_care")

# Win32 常量（托盘 / 锁屏）
if IS_WINDOWS:
    WM_DESTROY = 0x0002
    WM_COMMAND = 0x0111
    WM_USER = 0x0400
    WM_TRAYICON = WM_USER + 20
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    IDI_APPLICATION = 32512
    MF_STRING = 0x0000
    MF_GRAYED = 0x0001
    MF_CHECKED = 0x0008
    MF_UNCHECKED = 0x0000
    MF_DISABLED = 0x0002
    MF_SEPARATOR = 0x0800
    TPM_LEFTALIGN = 0x0000
    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100
    IMAGE_ICON = 1
    LR_DEFAULTSIZE = 0x00000040
    LR_SHARED = 0x00008000

    ID_TRAY_BREAK = 1001
    ID_TRAY_EXIT = 1002
    ID_TRAY_ABOUT = 1003
    ID_TRAY_AUTOSTART = 1004

    SM_CXSMICON = 49
    SM_CYSMICON = 50
    DT_CENTER = 0x00000001
    DT_VCENTER = 0x00000004
    DT_SINGLELINE = 0x00000020
    FW_BOLD = 700
    DEFAULT_CHARSET = 1
    OUT_TT_PRECIS = 4
    CLIP_DEFAULT_PRECIS = 0
    CLEARTYPE_QUALITY = 5
    ANTIALIASED_QUALITY = 4
    DEFAULT_PITCH = 0
    FF_SWISS = 32
    TRANSPARENT = 1
    BI_RGB = 0
    DIB_RGB_COLORS = 0


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class Config:
    interval_minutes: float = 20.0
    break_seconds: int = 20
    lock_workstation: bool = False
    allow_skip: bool = False
    title: str = "护眼休息"
    message: str = (
        "请远眺约 6 米（20 英尺）外，放松眼睛。\n"
        "遵循 20-20-20 法则：每 20 分钟 · 看向 20 英尺外 · 持续 20 秒。"
    )

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        cfg = cls()
        if "interval_minutes" in data:
            cfg.interval_minutes = float(data["interval_minutes"])
        if "break_seconds" in data:
            cfg.break_seconds = int(data["break_seconds"])
        if "lock_workstation" in data:
            cfg.lock_workstation = bool(data["lock_workstation"])
        if "allow_skip" in data:
            cfg.allow_skip = bool(data["allow_skip"])
        if "title" in data:
            cfg.title = str(data["title"])
        if "message" in data:
            cfg.message = str(data["message"])
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes 必须大于 0")
        if self.break_seconds <= 0:
            raise ValueError("break_seconds 必须大于 0")
        if self.interval_minutes * 60 < self.break_seconds:
            LOG.warning("休息时长大于间隔，仍可运行，但几乎会连续休息。")


def load_config(path: Path) -> Config:
    if not path.is_file():
        LOG.info("未找到配置文件 %s，使用默认值。", path)
        return Config()
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("配置文件根节点必须是 JSON 对象")
    return Config.from_dict(data)


# ---------------------------------------------------------------------------
# 显示器枚举
# ---------------------------------------------------------------------------
MonitorRect = Tuple[int, int, int, int]  # left, top, right, bottom


def _primary_monitor_tk() -> List[MonitorRect]:
    """用 tkinter 探测主屏尺寸（回退路径）。"""
    root = tk.Tk()
    root.withdraw()
    try:
        w = root.winfo_screenwidth()
        h = root.winfo_screenheight()
    finally:
        root.destroy()
    return [(0, 0, w, h)]


def _get_monitors_mac() -> List[MonitorRect]:
    """通过 CoreGraphics/Quartz 枚举显示器；坐标转为 tk 常用的左上原点。"""
    try:
        cg = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        class CGSize(ctypes.Structure):
            _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

        class CGRect(ctypes.Structure):
            _fields_ = [("origin", CGPoint), ("size", CGSize)]

        CGDirectDisplayID = ctypes.c_uint32
        max_displays = 32
        display_count = ctypes.c_uint32(0)
        displays = (CGDirectDisplayID * max_displays)()

        cg.CGGetActiveDisplayList.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(CGDirectDisplayID),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        cg.CGGetActiveDisplayList.restype = ctypes.c_int32
        cg.CGMainDisplayID.argtypes = []
        cg.CGMainDisplayID.restype = CGDirectDisplayID
        cg.CGDisplayBounds.argtypes = [CGDirectDisplayID]
        cg.CGDisplayBounds.restype = CGRect

        err = cg.CGGetActiveDisplayList(max_displays, displays, ctypes.byref(display_count))
        if err != 0 or display_count.value == 0:
            raise RuntimeError(f"CGGetActiveDisplayList err={err}")

        main_bounds = cg.CGDisplayBounds(cg.CGMainDisplayID())
        main_height = float(main_bounds.size.height)
        monitors: List[MonitorRect] = []
        for i in range(int(display_count.value)):
            b = cg.CGDisplayBounds(displays[i])
            # Quartz：原点在主屏左下，y 向上；tk：原点主屏左上，y 向下
            left = int(round(b.origin.x))
            top = int(round(main_height - (b.origin.y + b.size.height)))
            right = int(round(b.origin.x + b.size.width))
            bottom = int(round(main_height - b.origin.y))
            if right > left and bottom > top:
                monitors.append((left, top, right, bottom))
        if monitors:
            return monitors
    except Exception as exc:  # noqa: BLE001
        LOG.warning("macOS 多显示器枚举失败，回退主屏：%s", exc)
    return _primary_monitor_tk()


def _get_monitors_windows() -> List[MonitorRect]:
    user32 = ctypes.windll.user32
    monitors: List[MonitorRect] = []

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    def _callback(hmonitor, _hdc, _lprect, _lparam):  # noqa: ANN001
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append((int(r.left), int(r.top), int(r.right), int(r.bottom)))
        return True

    cb = MonitorEnumProc(_callback)
    if not user32.EnumDisplayMonitors(None, None, cb, 0) or not monitors:
        w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        return [(0, 0, w, h)]
    return monitors


def get_monitors() -> List[MonitorRect]:
    """返回所有显示器的虚拟桌面坐标；失败时至少返回主屏。"""
    if IS_WINDOWS:
        return _get_monitors_windows()
    if IS_MAC:
        return _get_monitors_mac()
    return _primary_monitor_tk()


def lock_workstation() -> None:
    """锁屏：Windows 用 LockWorkStation；macOS 用 Control+Command+Q（需辅助功能权限）。"""
    if IS_WINDOWS:
        try:
            ctypes.windll.user32.LockWorkStation()
        except Exception:  # noqa: BLE001
            LOG.exception("LockWorkStation 调用失败")
        return
    if IS_MAC:
        try:
            # 等价于菜单「锁定屏幕」快捷键；可能需在「系统设置 → 隐私与安全性 → 辅助功能」授权终端/Python
            r = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to keystroke "q" using {control down, command down}',
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode != 0:
                LOG.warning(
                    "macOS 锁屏失败（returncode=%s）：%s",
                    r.returncode,
                    (r.stderr or r.stdout or "").strip()[:200],
                )
        except Exception:  # noqa: BLE001
            LOG.exception("macOS 锁屏（osascript）调用失败")
        return
    LOG.warning("当前平台不支持 lock_workstation，已跳过。")


def _apple_script_escape(s: str) -> str:
    """Escape a Python string for an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _macos_dialog_script(title: str, message: str, button: str = "好") -> str:
    """Build `display dialog` AppleScript (runs outside our process)."""
    parts = [
        _apple_script_escape(p)
        for p in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    if len(parts) == 1:
        msg_expr = f'"{parts[0]}"'
    else:
        msg_expr = " & return & ".join(f'"{p}"' for p in parts)
    title_q = _apple_script_escape(title)
    btn_q = _apple_script_escape(button)
    return (
        f'display dialog {msg_expr} with title "{title_q}" '
        f'buttons {{"{btn_q}"}} default button 1'
    )


def _macos_show_dialog_detached(title: str, message: str, button: str = "好") -> bool:
    """Show an About/info dialog via detached osascript.

    Do NOT call NSAlert.runModal or tkinter.messagebox from the status-item
    menu path: under launchd + Tk + AppKit they share NSApplication and can
    abort the Python process (PyEval_RestoreThread / Abort trap 6).
    A separate osascript process owns the modal dialog and cannot kill us.
    """
    if not IS_MAC:
        return False
    try:
        script = _macos_dialog_script(title, message, button=button)
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return True
    except Exception:  # noqa: BLE001
        LOG.debug("osascript display dialog 启动失败", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# 全屏遮罩
# ---------------------------------------------------------------------------
# 护眼向配色：深墨绿底 + 柔和青绿强调（避免刺眼高对比与常见“紫渐变”模板感）
BG = "#0c1a14"
FG = "#e8f5ef"
ACCENT = "#3d9b7a"
MUTED = "#8fb9a8"
WARN = "#d4a574"


class BreakOverlay:
    """在每个显示器上创建始终置顶的全屏遮罩，倒计时结束后自动关闭。"""

    def __init__(
        self,
        root: tk.Tk,
        config: Config,
        on_closed: Optional[Callable[[], None]] = None,
    ) -> None:
        self.root = root
        self.config = config
        self.on_closed = on_closed
        self.windows: List[tk.Toplevel] = []
        self._remaining = int(config.break_seconds)
        self._closed = False
        self._countdown_job: Optional[str] = None
        self._primary_labels: dict[str, tk.Label] = {}
        self._dismiss_btn: Optional[tk.Button] = None

    def show(self) -> None:
        monitors = get_monitors()
        LOG.info("开始护眼休息，显示器数=%d，时长=%ds", len(monitors), self.config.break_seconds)

        if self.config.lock_workstation:
            # 可选：真正锁屏。遮罩仍会显示；用户解锁后若倒计时未完会继续看到遮罩。
            lock_workstation()

        # 主屏（含原点或面积最大）优先作为可交互/信息完整的遮罩
        primary = max(monitors, key=lambda m: (m[2] - m[0]) * (m[3] - m[1]))
        for rect in monitors:
            self._create_window(rect, is_primary=(rect == primary))

        self._tick()

    def _pick_font(self, size: int, bold: bool = False) -> tuple:
        families = set(tkfont.families(self.root))
        candidates = (
            "PingFang SC",
            "Hiragino Sans GB",
            "Heiti SC",
            "STHeiti",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "微软雅黑",
            "SimHei",
            "Noto Sans CJK SC",
            "WenQuanYi Micro Hei",
        )
        for name in candidates:
            if name in families:
                return (name, size, "bold" if bold else "normal")
        return ("TkDefaultFont", size, "bold" if bold else "normal")

    def _create_window(self, rect: MonitorRect, is_primary: bool) -> None:
        left, top, right, bottom = rect
        width = max(1, right - left)
        height = max(1, bottom - top)

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.configure(bg=BG)
        win.overrideredirect(True)
        # 置顶 + 尽量抢占焦点；Windows 上再尝试 topmost 属性
        win.attributes("-topmost", True)
        try:
            # 略微透明，减少“死黑”压迫感，同时仍明显阻断视线
            win.attributes("-alpha", 0.96)
        except tk.TclError:
            pass

        win.geometry(f"{width}x{height}+{left}+{top}")
        win.deiconify()
        win.lift()
        win.focus_force()

        # 拦截关闭键，防止 Alt+F4 提前退出（休息期间）
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = tk.Frame(win, bg=BG)
        frame.place(relx=0.5, rely=0.5, anchor="center")

        if is_primary:
            title = tk.Label(
                frame,
                text=self.config.title,
                font=self._pick_font(42, bold=True),
                fg=ACCENT,
                bg=BG,
            )
            title.pack(pady=(0, 24))

            msg = tk.Label(
                frame,
                text=self.config.message,
                font=self._pick_font(18),
                fg=FG,
                bg=BG,
                justify="center",
            )
            msg.pack(pady=(0, 36))

            countdown = tk.Label(
                frame,
                text=str(self._remaining),
                font=self._pick_font(96, bold=True),
                fg=FG,
                bg=BG,
            )
            countdown.pack(pady=(0, 8))
            self._primary_labels["countdown"] = countdown

            hint = tk.Label(
                frame,
                text="请远眺放松，倒计时结束后将自动关闭",
                font=self._pick_font(14),
                fg=MUTED,
                bg=BG,
            )
            hint.pack(pady=(0, 28))
            self._primary_labels["hint"] = hint

            if self.config.allow_skip:
                btn = tk.Button(
                    frame,
                    text="跳过本次休息（不推荐）",
                    font=self._pick_font(12),
                    fg=BG,
                    bg=WARN,
                    activebackground="#e0b888",
                    relief="flat",
                    padx=16,
                    pady=8,
                    command=self._skip_with_warning,
                )
                btn.pack()
                self._dismiss_btn = btn
            else:
                # 倒计时结束后才出现“我已休息好”按钮（可选提前关，但默认时间已到）
                btn = tk.Button(
                    frame,
                    text="我已休息好",
                    font=self._pick_font(14, bold=True),
                    fg=BG,
                    bg=ACCENT,
                    activebackground="#4cb08c",
                    relief="flat",
                    padx=20,
                    pady=10,
                    command=self.close,
                    state="disabled",
                )
                btn.pack()
                self._dismiss_btn = btn
        else:
            # 副屏：简洁遮罩，避免多处倒计时干扰
            label = tk.Label(
                frame,
                text="护眼休息中…",
                font=self._pick_font(28, bold=True),
                fg=ACCENT,
                bg=BG,
            )
            label.pack()

        # 阻断键盘/鼠标落到下层窗口（各屏尽量 grab；主屏优先）
        try:
            if is_primary:
                win.grab_set_global()
            else:
                win.grab_set()
        except tk.TclError:
            try:
                win.grab_set()
            except tk.TclError:
                pass

        # 吞掉常见按键，降低误操作穿透
        for seq in ("<Escape>", "<Alt-F4>", "<Return>", "<space>"):
            win.bind(seq, lambda _e: "break")

        self.windows.append(win)

    def _skip_with_warning(self) -> None:
        # 二次确认：明确提示跳过不利于护眼
        dlg = tk.Toplevel(self.root)
        dlg.title("确认跳过")
        dlg.attributes("-topmost", True)
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        tk.Label(
            dlg,
            text="跳过将缩短休息时间，降低护眼效果。\n确定要跳过吗？",
            font=self._pick_font(12),
            fg=FG,
            bg=BG,
            justify="center",
            padx=24,
            pady=16,
        ).pack()
        bar = tk.Frame(dlg, bg=BG)
        bar.pack(pady=(0, 16))

        def confirm() -> None:
            dlg.destroy()
            self.close()

        tk.Button(bar, text="继续休息", command=dlg.destroy, padx=12, pady=4).pack(side="left", padx=8)
        tk.Button(bar, text="仍然跳过", command=confirm, padx=12, pady=4).pack(side="left", padx=8)
        dlg.transient(self.windows[0] if self.windows else self.root)
        dlg.grab_set()
        dlg.focus_force()

    def _tick(self) -> None:
        if self._closed:
            return
        label = self._primary_labels.get("countdown")
        if label is not None:
            label.configure(text=str(self._remaining))

        if self._remaining <= 0:
            hint = self._primary_labels.get("hint")
            if hint is not None:
                hint.configure(text="休息结束，可以继续工作了")
            if self._dismiss_btn is not None and not self.config.allow_skip:
                self._dismiss_btn.configure(state="normal")
            # 自动关闭：给用户约 1.5 秒读完“休息结束”
            self._countdown_job = self.root.after(1500, self.close)
            return

        self._remaining -= 1
        self._countdown_job = self.root.after(1000, self._tick)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._countdown_job is not None:
            try:
                self.root.after_cancel(self._countdown_job)
            except Exception:  # noqa: BLE001
                pass
            self._countdown_job = None

        for win in self.windows:
            try:
                win.grab_release()
            except Exception:  # noqa: BLE001
                pass
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.windows.clear()
        LOG.info("护眼遮罩已关闭")
        if self.on_closed:
            self.on_closed()



# ---------------------------------------------------------------------------
# 开机自启（Windows Startup / macOS LaunchAgent）
# ---------------------------------------------------------------------------
AUTOSTART_NAME = "护眼锁屏助手"
MAC_LAUNCH_AGENT_LABEL = "net.chinadong.eye-care"
# 当前进程用于开机自启的持久参数（不含 --once / --demo-seconds 等临时项）
_PERSISTENT_ARGV: List[str] = []


def persistent_argv_from(argv: Sequence[str]) -> List[str]:
    """从启动参数中筛出应写入自启快捷方式的项。"""
    out: List[str] = []
    it = iter(list(argv))
    for arg in it:
        if arg in ("--once", "-v", "--verbose"):
            continue
        if arg == "--demo-seconds" or arg.startswith("--demo-seconds="):
            if arg == "--demo-seconds":
                next(it, None)
            continue
        out.append(arg)
    return out


def remember_persistent_argv(argv: Optional[Sequence[str]] = None) -> None:
    global _PERSISTENT_ARGV
    raw = list(argv) if argv is not None else list(sys.argv[1:])
    _PERSISTENT_ARGV = persistent_argv_from(raw)


def _autostart_argument_string(script: Path) -> str:
    parts = [f'"{script}"']
    for a in _PERSISTENT_ARGV:
        if any(ch.isspace() for ch in a):
            parts.append(f'"{a}"')
        else:
            parts.append(a)
    return " ".join(parts)


def _python_for_autostart() -> Path:
    """选择适合后台自启的解释器路径。"""
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.is_file():
            return candidate
    return exe


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("找不到 APPDATA，无法配置开机自启")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_candidates() -> List[Path]:
    d = _startup_dir()
    return [d / f"{AUTOSTART_NAME}.lnk", d / f"{AUTOSTART_NAME}.bat"]


def _mac_launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _mac_plist_path() -> Path:
    return _mac_launch_agents_dir() / f"{MAC_LAUNCH_AGENT_LABEL}.plist"


def _mac_tcc_protected_roots() -> List[Path]:
    home = Path.home()
    return [home / "Desktop", home / "Documents", home / "Downloads"]


def _mac_service_dir(script: Path) -> Path:
    """LaunchAgent 运行目录：避开 Desktop/Documents/Downloads（TCC 会导致 posix_spawn EPERM）。"""
    script = script.resolve()
    for root in _mac_tcc_protected_roots():
        try:
            script.relative_to(root.resolve())
        except ValueError:
            continue
        return Path.home() / "Library" / "Application Support" / "eye-care-lock"
    return script.parent


def _mac_sync_service_files(script: Path) -> Path:
    """若脚本在 TCC 目录，同步到 Application Support 并返回服务端脚本路径。"""
    script = script.resolve()
    service_dir = _mac_service_dir(script)
    if service_dir == script.parent:
        return script
    service_dir.mkdir(parents=True, exist_ok=True)
    import shutil

    for name in ("eye_care.py", "config.json"):
        src = script.parent / name
        if src.is_file():
            shutil.copy2(src, service_dir / name)
    return service_dir / "eye_care.py"


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_mac_plist(python: Path, script: Path) -> str:
    args = [str(python), str(script), *_PERSISTENT_ARGV]
    arg_xml = "\n".join(f"        <string>{_xml_escape(a)}</string>" for a in args)
    log_path = script.parent / "eye_care.log"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{MAC_LAUNCH_AGENT_LABEL}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"{arg_xml}\n"
        "    </array>\n"
        "    <key>WorkingDirectory</key>\n"
        f"    <string>{_xml_escape(str(script.parent))}</string>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>KeepAlive</key>\n"
        "    <false/>\n"
        "    <key>ProcessType</key>\n"
        "    <string>Interactive</string>\n"
        "    <key>LimitLoadToSessionType</key>\n"
        "    <string>Aqua</string>\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{_xml_escape(str(log_path))}</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{_xml_escape(str(log_path))}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _mac_launchctl(action: str, plist: Path) -> None:
    """best-effort load/unload；失败只记日志。"""
    uid = os.getuid()
    domain = f"gui/{uid}"
    try:
        if action == "bootout":
            subprocess.run(
                ["launchctl", "bootout", domain, str(plist)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        elif action == "bootstrap":
            subprocess.run(
                ["launchctl", "bootstrap", domain, str(plist)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        elif action == "unload":
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        elif action == "load":
            subprocess.run(
                ["launchctl", "load", str(plist)],
                capture_output=True,
                text=True,
                timeout=10,
            )
    except Exception as exc:  # noqa: BLE001
        LOG.debug("launchctl %s 失败: %s", action, exc)


def is_autostart_enabled() -> bool:
    try:
        if IS_WINDOWS:
            return any(p.is_file() for p in autostart_candidates())
        if IS_MAC:
            return _mac_plist_path().is_file()
    except Exception:  # noqa: BLE001
        return False
    return False


def _set_autostart_windows(enabled: bool) -> bool:
    startup = _startup_dir()
    startup.mkdir(parents=True, exist_ok=True)
    lnk = startup / f"{AUTOSTART_NAME}.lnk"
    bat = startup / f"{AUTOSTART_NAME}.bat"
    if not enabled:
        for p in (lnk, bat):
            if p.is_file():
                p.unlink()
        LOG.info("已关闭开机自启")
        return True

    pythonw = _python_for_autostart()
    script = Path(__file__).resolve()
    args_str = _autostart_argument_string(script)
    ps_args = args_str.replace("'", "''")
    ps = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut(\"{lnk}\"); "
        f"$s.TargetPath = \"{pythonw}\"; "
        f"$s.Arguments = '{ps_args}'; "
        f"$s.WorkingDirectory = \"{script.parent}\"; "
        f"$s.WindowStyle = 7; "
        f"$s.Description = '护眼锁屏助手 — 登录后自动启动'; "
        f"$s.Save()"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and lnk.is_file():
            if bat.is_file():
                bat.unlink()
            LOG.info("已开启开机自启: %s", lnk)
            return True
        LOG.warning("创建快捷方式失败，改用 bat: %s", (r.stderr or r.stdout)[:200])
    except Exception as exc:  # noqa: BLE001
        LOG.warning("创建快捷方式异常，改用 bat: %s", exc)

    bat.write_text(
        "@echo off\r\n"
        + f'start "" "{pythonw}" {args_str}\r\n',
        encoding="utf-8",
    )
    LOG.info("已开启开机自启: %s", bat)
    return True


def _set_autostart_mac(enabled: bool) -> bool:
    agents = _mac_launch_agents_dir()
    agents.mkdir(parents=True, exist_ok=True)
    plist = _mac_plist_path()
    if not enabled:
        if plist.is_file():
            _mac_launchctl("bootout", plist)
            _mac_launchctl("unload", plist)
            try:
                plist.unlink()
            except OSError as exc:
                LOG.error("删除 LaunchAgent 失败: %s", exc)
                return False
        LOG.info("已关闭开机自启（LaunchAgent）")
        return True

    python = _python_for_autostart()
    script = _mac_sync_service_files(Path(__file__).resolve())
    body = _build_mac_plist(python, script)
    # 先卸再写，避免残留旧定义
    if plist.is_file():
        _mac_launchctl("bootout", plist)
        _mac_launchctl("unload", plist)
    plist.write_text(body, encoding="utf-8")
    _mac_launchctl("bootstrap", plist)
    _mac_launchctl("load", plist)
    LOG.info("已开启开机自启: %s", plist)
    return True


def set_autostart_enabled(enabled: bool) -> bool:
    """启用/关闭登录自启。成功返回 True。"""
    try:
        if IS_WINDOWS:
            return _set_autostart_windows(enabled)
        if IS_MAC:
            return _set_autostart_mac(enabled)
        LOG.warning("当前平台不支持开机自启切换")
        return False
    except Exception as exc:  # noqa: BLE001
        LOG.error("配置开机自启失败: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Windows 系统托盘（ctypes，无第三方依赖）
# ---------------------------------------------------------------------------
class TrayIcon:
    """精简版 Shell_NotifyIcon 托盘；在独立线程跑消息循环。"""

    # 与 HUD 一致的护眼色 (R,G,B)
    ICON_OK = ((47, 79, 62), (200, 230, 201))
    ICON_MID = ((74, 70, 48), (240, 230, 184))
    ICON_NEAR = ((74, 53, 53), (245, 208, 200))
    ICON_REST = ((46, 69, 80), (178, 223, 219))

    def __init__(
        self,
        on_break: Callable[[], None],
        on_exit: Callable[[], None],
        tooltip: str = APP_NAME,
    ) -> None:
        self.on_break = on_break
        self.on_exit = on_exit
        self.tooltip = tooltip
        self.status_label = tooltip
        self._thread: Optional[threading.Thread] = None
        self._hwnd = None
        self._nid = None
        self._running = False
        self._hicon = None
        self._owns_icon = False
        self._last_icon_key: Optional[Tuple[str, str]] = None
        self._icon_lock = threading.Lock()

    @staticmethod
    def _colors_for(minutes: int, resting: bool):
        if resting:
            return TrayIcon.ICON_REST
        if minutes <= 5:
            return TrayIcon.ICON_NEAR
        if minutes <= 10:
            return TrayIcon.ICON_MID
        return TrayIcon.ICON_OK

    @staticmethod
    def _rgb(rgb: Tuple[int, int, int]) -> int:
        r, g, b = rgb
        return r | (g << 8) | (b << 16)

    def _create_number_icon(self, label: str, bg: Tuple[int, int, int], fg: Tuple[int, int, int]):
        """用 GDI 画一张带数字的托盘图标，返回 HICON。"""
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # 64-bit safe prototypes for GDI/user32 icon drawing
        HGDIOBJ = ctypes.c_void_p
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.SelectObject.argtypes = [wintypes.HDC, HGDIOBJ]
        gdi32.SelectObject.restype = HGDIOBJ
        gdi32.DeleteObject.argtypes = [HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        gdi32.CreateBitmap.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p
        ]
        gdi32.CreateBitmap.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.SetBkMode.restype = ctypes.c_int
        gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        gdi32.SetTextColor.restype = wintypes.COLORREF
        gdi32.PatBlt.argtypes = [
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD
        ]
        gdi32.PatBlt.restype = wintypes.BOOL
        gdi32.CreateFontW.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPCWSTR,
        ]
        gdi32.CreateFontW.restype = wintypes.HFONT
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
        user32.FillRect.restype = ctypes.c_int
        user32.DrawTextW.argtypes = [
            wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
            ctypes.POINTER(wintypes.RECT), wintypes.UINT,
        ]
        user32.DrawTextW.restype = ctypes.c_int
        user32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
        user32.CreateIconIndirect.restype = wintypes.HICON
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyIcon.restype = wintypes.BOOL

        size = user32.GetSystemMetrics(SM_CXSMICON) or 16
        # 两位数在 16px 太挤，尽量用 32
        if size < 24:
            size = 32

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        class ICONINFO(ctypes.Structure):
            _fields_ = [
                ("fIcon", wintypes.BOOL),
                ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP),
            ]

        hdc_screen = user32.GetDC(None)
        hdc = gdi32.CreateCompatibleDC(hdc_screen)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = size
        bmi.bmiHeader.biHeight = -size  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(
            hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        if not hbmp:
            user32.ReleaseDC(None, hdc_screen)
            gdi32.DeleteDC(hdc)
            return None

        old = gdi32.SelectObject(hdc, hbmp)
        brush = gdi32.CreateSolidBrush(self._rgb(bg))
        rect = wintypes.RECT(0, 0, size, size)
        user32.FillRect(hdc, ctypes.byref(rect), brush)
        gdi32.DeleteObject(brush)

        # 字号随位数调整
        font_px = size - 6 if len(label) <= 1 else max(10, size // 2 + 2)
        hfont = gdi32.CreateFontW(
            -font_px,
            0,
            0,
            0,
            FW_BOLD,
            0,
            0,
            0,
            DEFAULT_CHARSET,
            OUT_TT_PRECIS,
            CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY,
            DEFAULT_PITCH | FF_SWISS,
            "Segoe UI",
        )
        old_font = gdi32.SelectObject(hdc, hfont)
        gdi32.SetBkMode(hdc, TRANSPARENT)
        gdi32.SetTextColor(hdc, self._rgb(fg))
        user32.DrawTextW(
            hdc,
            label,
            -1,
            ctypes.byref(rect),
            DT_CENTER | DT_VCENTER | DT_SINGLELINE,
        )
        gdi32.SelectObject(hdc, old_font)
        gdi32.DeleteObject(hfont)
        gdi32.SelectObject(hdc, old)

        # 1-bit mask: all opaque
        hdc_mask = gdi32.CreateCompatibleDC(hdc_screen)
        hmask = gdi32.CreateBitmap(size, size, 1, 1, None)
        old_mask = gdi32.SelectObject(hdc_mask, hmask)
        # black = opaque in icon mask when using color bitmap
        gdi32.PatBlt(hdc_mask, 0, 0, size, size, 0x00000042)  # BLACKNESS
        gdi32.SelectObject(hdc_mask, old_mask)
        gdi32.DeleteDC(hdc_mask)

        ii = ICONINFO()
        ii.fIcon = True
        ii.xHotspot = 0
        ii.yHotspot = 0
        ii.hbmMask = hmask
        ii.hbmColor = hbmp
        hicon = user32.CreateIconIndirect(ctypes.byref(ii))

        gdi32.DeleteObject(hbmp)
        gdi32.DeleteObject(hmask)
        gdi32.DeleteDC(hdc)
        user32.ReleaseDC(None, hdc_screen)
        return hicon

    def _apply_icon(self, label: str, resting: bool, minutes: int) -> None:
        if not IS_WINDOWS or self._nid is None:
            return
        key = ("rest" if resting else "min", label)
        if key == self._last_icon_key:
            return
        bg, fg = self._colors_for(minutes, resting)
        try:
            hicon = self._create_number_icon(label, bg, fg)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("创建数字托盘图标失败: %s", exc)
            return
        if not hicon:
            return
        with self._icon_lock:
            old = self._hicon if self._owns_icon else None
            self._nid.hIcon = hicon
            self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            ok = ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
            if ok:
                self._hicon = hicon
                self._owns_icon = True
                self._last_icon_key = key
                if old:
                    ctypes.windll.user32.DestroyIcon(old)
            else:
                ctypes.windll.user32.DestroyIcon(hicon)

    def set_status(
        self,
        tip: str,
        menu_label: Optional[str] = None,
        minutes: Optional[int] = None,
        resting: bool = False,
    ) -> None:
        """更新悬停提示、菜单状态行，以及托盘数字图标。"""
        self.tooltip = tip
        if menu_label is not None:
            self.status_label = menu_label
        if not IS_WINDOWS or self._nid is None:
            return
        try:
            self._nid.szTip = tip[:127]
            self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
        except Exception:  # noqa: BLE001
            pass
        if resting:
            self._apply_icon("休", True, 0)
        elif minutes is not None:
            label = str(max(0, int(minutes)))
            if len(label) > 2:
                label = "99"
            self._apply_icon(label, False, int(minutes))

    def start(self) -> None:
        if not IS_WINDOWS:
            LOG.info("非 Windows：跳过系统托盘，请用控制台 Ctrl+C 退出。")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if not IS_WINDOWS or self._hwnd is None:
            return
        try:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        with self._icon_lock:
            if self._owns_icon and self._hicon:
                try:
                    ctypes.windll.user32.DestroyIcon(self._hicon)
                except Exception:  # noqa: BLE001
                    pass
                self._hicon = None
                self._owns_icon = False

    def _run(self) -> None:  # pragma: no cover - Windows only
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        # Some Windows Python builds omit these aliases in ctypes.wintypes.
        if not hasattr(wintypes, "HCURSOR"):
            wintypes.HCURSOR = wintypes.HANDLE
        if not hasattr(wintypes, "HBRUSH"):
            wintypes.HBRUSH = wintypes.HANDLE

        # 64-bit: Win32 APIs need explicit pointer-sized prototypes.
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HCURSOR),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
            ]

        def wnd_proc(hwnd, msg, wparam, lparam):  # noqa: ANN001
            if msg == WM_TRAYICON:
                if lparam in (WM_RBUTTONUP, WM_LBUTTONUP):
                    self._show_menu(hwnd)
                return 0
            if msg == WM_COMMAND:
                cmd = int(wparam) & 0xFFFF
                if cmd == ID_TRAY_BREAK:
                    self.on_break()
                elif cmd == ID_TRAY_AUTOSTART:
                    want = not is_autostart_enabled()
                    if not set_autostart_enabled(want):
                        user32.MessageBoxW(
                            hwnd,
                            "无法修改开机自启。\n请检查 Startup 目录权限后重试。",
                            APP_NAME,
                            0x10,  # MB_ICONERROR
                        )
                elif cmd == ID_TRAY_ABOUT:
                    user32.MessageBoxW(
                        hwnd,
                        "定时全屏护眼提醒。\n默认每 20 分钟休息 20 秒。\n配置见 config.json。",
                        APP_NAME,
                        0,
                    )
                elif cmd == ID_TRAY_EXIT:
                    self.on_exit()
                return 0
            if msg == WM_DESTROY:
                if self._nid is not None:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                    self._nid = None
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wnd_proc)  # 必须挂到实例上，防止被 GC
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "EyeCareTrayHiddenWindow"

        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            # 重复注册时忽略（例如热重载）；其它错误仍尝试继续创建窗口
            err = kernel32.GetLastError()
            if err not in (0, 1410):  # ERROR_CLASS_ALREADY_EXISTS
                LOG.debug("RegisterClassW 返回错误码 %s（将继续尝试）", err)

        hwnd = user32.CreateWindowExW(
            0,
            class_name,
            APP_NAME,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        self._hwnd = hwnd

        # MAKEINTRESOURCE(IDI_APPLICATION) —— 将资源 ID 当作指针传递
        id_app = ctypes.cast(IDI_APPLICATION, wintypes.LPCWSTR)
        hicon = user32.LoadIconW(None, id_app)
        if not hicon:
            hicon = user32.LoadImageW(
                None,
                id_app,
                IMAGE_ICON,
                0,
                0,
                LR_SHARED | LR_DEFAULTSIZE,
            )

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = hicon
        nid.szTip = self.tooltip[:127]
        self._nid = nid
        ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        if not ok:
            LOG.error("Shell_NotifyIconW(NIM_ADD) 失败，错误码=%s", kernel32.GetLastError())
        else:
            LOG.info("托盘图标已注册 (hwnd=%s)", hwnd)
            # 初始数字图标；主循环随后会按真实剩余分钟刷新
            try:
                self._apply_icon("20", False, 20)
            except Exception:  # noqa: BLE001
                pass

        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _show_menu(self, hwnd) -> None:  # pragma: no cover
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(
            menu, MF_STRING | MF_GRAYED | MF_DISABLED, 0, self.status_label[:127]
        )
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_TRAY_BREAK, "立即开始休息")
        auto_flags = MF_STRING | (MF_CHECKED if is_autostart_enabled() else MF_UNCHECKED)
        user32.AppendMenuW(menu, auto_flags, ID_TRAY_AUTOSTART, "开机自动启动")
        user32.AppendMenuW(menu, MF_STRING, ID_TRAY_ABOUT, "关于")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_TRAY_EXIT, "退出")

        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(
            menu,
            TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD,
            pt.x,
            pt.y,
            0,
            hwnd,
            None,
        )
        user32.DestroyMenu(menu)
        if cmd:
            user32.PostMessageW(hwnd, WM_COMMAND, cmd, 0)


# ---------------------------------------------------------------------------
# macOS 状态 UI（优先 AppKit 菜单栏；否则 HUD 右键菜单）
# ---------------------------------------------------------------------------
def _appkit_available() -> bool:
    """PyObjC AppKit 是否可导入（Anaconda 常自带；否则可选 pip install pyobjc-framework-Cocoa）。"""
    if not IS_MAC:
        return False
    try:
        from AppKit import NSStatusBar  # noqa: F401
        return True
    except ImportError:
        return False


class MacStatusUI:
    """与 TrayIcon 对齐的 start/stop/set_status API。

    有 AppKit 时在菜单栏（右上角）显示剩余分钟 / 休息状态，菜单与 Windows 托盘一致；
    同时保留悬浮 HUD 右键菜单。无 AppKit 时仅 HUD。
    """

    # 菜单栏不用浅色护眼色（浅色菜单栏上几乎看不见）；
    # 用系统 labelColor / template 图像，浅色与深色菜单栏皆清晰。

    def __init__(
        self,
        on_break: Callable[[], None],
        on_exit: Callable[[], None],
        tooltip: str = APP_NAME,
    ) -> None:
        self.on_break = on_break
        self.on_exit = on_exit
        self.tooltip = tooltip
        self.status_label = tooltip
        self._hud: Optional["CountdownHud"] = None
        self._tk_root: Optional[tk.Misc] = None
        self._running = False
        self._use_menubar = False
        self._status_item = None
        self._menu = None
        self._target = None
        self._delegate = None
        self._last_title_key: Optional[Tuple[str, bool]] = None
        self._pump_job: Optional[str] = None
        self._minutes: Optional[int] = None
        self._resting = False

    def attach_hud(self, hud: "CountdownHud") -> None:
        self._hud = hud
        try:
            self._tk_root = hud.win.master  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            self._tk_root = None
        hud.bind_status_menu(
            on_break=self.on_break,
            on_exit=self.on_exit,
            on_about=self._show_about,
            on_toggle_autostart=self._toggle_autostart,
            get_status_label=lambda: self.status_label,
            get_autostart=is_autostart_enabled,
        )

    def start(self) -> None:
        self._running = True
        if IS_MAC and _appkit_available():
            try:
                self._start_menubar()
                self._use_menubar = True
                LOG.info(
                    "macOS 菜单栏状态项已启用：右上角显示剩余分钟数字"
                    "（如 20）或休息中「休」；系统自动着色，点击打开菜单；"
                    "悬浮 HUD 亦可右键。"
                )
                return
            except Exception:  # noqa: BLE001
                LOG.exception("macOS 菜单栏状态项创建失败，回退到 HUD 菜单")
                self._teardown_menubar()
                self._use_menubar = False
        LOG.info(
            "macOS：使用悬浮 HUD — 右键（或 Control+点击）打开菜单"
            "（立即休息 / 开机自启 / 关于 / 退出）。"
            "若需菜单栏图标，请使用带 PyObjC AppKit 的 Python"
            "（如 Anaconda，或 pip install pyobjc-framework-Cocoa）。"
        )

    def stop(self) -> None:
        self._running = False
        self._cancel_pump()
        self._teardown_menubar()

    def set_status(
        self,
        tip: str,
        menu_label: Optional[str] = None,
        minutes: Optional[int] = None,
        resting: bool = False,
    ) -> None:
        self.tooltip = tip
        if menu_label is not None:
            self.status_label = menu_label
        self._minutes = minutes
        self._resting = resting
        if self._use_menubar and self._status_item is not None:
            self._apply_menubar_status(tip, minutes, resting)

    def _start_menubar(self) -> None:
        # 必须先有 tk.Tk()（EyeCareApp 已创建），否则会与 Tk 的 NSApplication 冲突
        from AppKit import (  # noqa: WPS433
            NSAttributedString,
            NSColor,
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSImage,
            NSMenu,
            NSMenuItem,
            NSObject,
            NSStatusBar,
            NSImageOnly,
            NSSquareStatusItemLength,
            NSVariableStatusItemLength,
        )
        from Foundation import NSMakeRect, NSMakeSize  # noqa: WPS433
        ui = self

        class _MenuTarget(NSObject):
            def doBreak_(self, _sender):  # noqa: N802, ANN001
                # AppKit 菜单回调里不要直接碰 tkinter；丢回 Tk 主线程
                ui._on_main(ui.on_break)

            def doAutostart_(self, _sender):  # noqa: N802, ANN001
                ui._on_main(ui._toggle_autostart)

            def doAbout_(self, _sender):  # noqa: N802, ANN001
                ui._on_main(ui._show_about)

            def doExit_(self, _sender):  # noqa: N802, ANN001
                ui._on_main(ui.on_exit)

        class _MenuDelegate(NSObject):
            def menuNeedsUpdate_(self, menu):  # noqa: N802, ANN001
                ui._refresh_menu_items(menu)

        target = _MenuTarget.alloc().init()
        delegate = _MenuDelegate.alloc().init()
        menu = NSMenu.alloc().init()
        menu.setDelegate_(delegate)

        # 占位项；menuNeedsUpdate_ 会刷新标题 / 勾选
        status_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            self.status_label[:64], None, ""
        )
        status_item.setEnabled_(False)
        menu.addItem_(status_item)
        menu.addItem_(NSMenuItem.separatorItem())

        for title, action in (
            ("立即开始休息", "doBreak:"),
            ("开机自动启动", "doAutostart:"),
            ("关于", "doAbout:"),
        ):
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            mi.setTarget_(target)
            menu.addItem_(mi)

        menu.addItem_(NSMenuItem.separatorItem())
        exit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出", "doExit:", ""
        )
        exit_item.setTarget_(target)
        menu.addItem_(exit_item)

        # 固定略宽于正方形，避免内容尚未绘制时长度为 0 / 被挤进溢出区
        bar_item = NSStatusBar.systemStatusBar().statusItemWithLength_(28.0)
        button = bar_item.button()
        if button is not None:
            button.setToolTip_(self.tooltip)
            # 先放明文标题，保证即便图像失败也立刻可见
            button.setTitle_("20")
        bar_item.setMenu_(menu)
        try:
            bar_item.setVisible_(True)
        except Exception:  # noqa: BLE001
            pass

        # 强引用，防止 PyObjC 对象被 GC
        self._target = target
        self._delegate = delegate
        self._menu = menu
        self._status_item = bar_item
        self._NSColor = NSColor
        self._NSFont = NSFont
        self._NSAttributedString = NSAttributedString
        self._NSForegroundColorAttributeName = NSForegroundColorAttributeName
        self._NSFontAttributeName = NSFontAttributeName
        self._NSImage = NSImage
        self._NSMakeSize = NSMakeSize
        self._NSMakeRect = NSMakeRect
        self._NSSquareStatusItemLength = NSSquareStatusItemLength
        self._NSVariableStatusItemLength = NSVariableStatusItemLength
        self._NSImageOnly = NSImageOnly

        self._apply_menubar_status(self.tooltip, self._minutes if self._minutes is not None else 20, False)
        self._schedule_pump()

    def _teardown_menubar(self) -> None:
        item = self._status_item
        self._status_item = None
        self._menu = None
        self._target = None
        self._delegate = None
        self._last_title_key = None
        if item is None:
            return
        try:
            from AppKit import NSStatusBar  # noqa: WPS433

            NSStatusBar.systemStatusBar().removeStatusItem_(item)
        except Exception:  # noqa: BLE001
            LOG.debug("移除菜单栏状态项失败", exc_info=True)

    def _schedule_pump(self) -> None:
        """轻度泵送 Cocoa runloop，确保菜单栏点击在 tk 主循环下可靠响应。"""
        self._cancel_pump()
        root = self._tk_root
        if root is None:
            return

        def _pump() -> None:
            self._pump_job = None
            if not self._running or not self._use_menubar:
                return
            try:
                from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop  # noqa: WPS433

                NSRunLoop.currentRunLoop().runMode_beforeDate_(
                    NSDefaultRunLoopMode,
                    NSDate.dateWithTimeIntervalSinceNow_(0.02),
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                self._pump_job = root.after(80, _pump)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                self._pump_job = None

        try:
            self._pump_job = root.after(80, _pump)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            self._pump_job = None

    def _cancel_pump(self) -> None:
        job = self._pump_job
        self._pump_job = None
        root = self._tk_root
        if job and root is not None:
            try:
                root.after_cancel(job)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass

    def _menubar_title(self, minutes: Optional[int], resting: bool) -> str:
        if resting:
            return "休"
        if minutes is None:
            return "·"
        title = str(max(0, int(minutes)))
        if len(title) > 2:
            return "99"
        return title

    def _make_template_status_image(self, title: str):
        """黑+透明 template 图：系统按菜单栏外观自动反色，浅/深皆清晰。"""
        NSImage = self._NSImage
        NSMakeSize = self._NSMakeSize
        NSMakeRect = self._NSMakeRect
        NSFont = self._NSFont
        NSColor = self._NSColor
        NSAttributedString = self._NSAttributedString
        fg_attr = self._NSForegroundColorAttributeName
        font_attr = self._NSFontAttributeName

        # 菜单栏标准高度约 22pt；略宽以容纳两位数
        w, h = (26.0, 22.0) if len(title) >= 2 else (22.0, 22.0)
        size = NSMakeSize(w, h)
        img = NSImage.alloc().initWithSize_(size)
        img.lockFocus()
        try:
            # template 图像必须以黑色绘制；透明处不着色
            # 数字用等宽数字字体；「休」等用系统字体以免缺字
            if title.isdigit():
                font = NSFont.monospacedDigitSystemFontOfSize_weight_(13.0, 0.5)
            else:
                font = NSFont.systemFontOfSize_weight_(13.0, 0.5)
            attrs = {
                fg_attr: NSColor.blackColor(),
                font_attr: font,
            }
            astr = NSAttributedString.alloc().initWithString_attributes_(title, attrs)
            ts = astr.size()
            x = max(0.0, (w - ts.width) / 2.0)
            y = max(0.0, (h - ts.height) / 2.0 - 0.5)
            astr.drawInRect_(NSMakeRect(x, y, ts.width, ts.height))
        finally:
            img.unlockFocus()
        img.setTemplate_(True)
        try:
            img.setSize_(size)
        except Exception:  # noqa: BLE001
            pass
        return img

    def _apply_menubar_status(
        self,
        tip: str,
        minutes: Optional[int],
        resting: bool,
    ) -> None:
        item = self._status_item
        if item is None:
            return
        title = self._menubar_title(minutes, resting)
        key = (title, resting)
        button = item.button()
        if button is not None:
            try:
                button.setToolTip_(tip.replace("\n", " — ")[:256])
            except Exception:  # noqa: BLE001
                pass
        if key == self._last_title_key:
            return
        self._last_title_key = key
        if button is None:
            # 旧系统偶发无 button；尽量用 length + title API
            try:
                item.setLength_(28.0)
                item.setTitle_(title)
            except Exception:  # noqa: BLE001
                pass
            return

        # 1) 明文标题（高对比）— 即便图像失败也可见
        try:
            button.setTitle_(title)
        except Exception:  # noqa: BLE001
            pass

        # 2) 优先 template 图像（系统自动适配浅/深菜单栏）
        used_image = False
        try:
            img = self._make_template_status_image(title)
            button.setImage_(img)
            button.setImagePosition_(self._NSImageOnly)  # 只显示图标，避免与标题叠字
            # 图像模式下清空标题，避免与图像叠字；图像失败时保留标题
            button.setTitle_("")
            used_image = True
            # 按位数调整宽度，避免挤进 Control Center 溢出
            item.setLength_(28.0 if len(title) >= 2 else 24.0)
        except Exception:  # noqa: BLE001
            LOG.debug("菜单栏 template 图像失败，回退标题", exc_info=True)
            try:
                button.setImage_(None)
            except Exception:  # noqa: BLE001
                pass

        if not used_image:
            # 3) labelColor 属性标题：跟随系统外观，浅/深皆清晰
            try:
                color = self._NSColor.labelColor()
                font = self._NSFont.monospacedDigitSystemFontOfSize_weight_(13.0, 0.5)
                attrs = {
                    self._NSForegroundColorAttributeName: color,
                    self._NSFontAttributeName: font,
                }
                astr = self._NSAttributedString.alloc().initWithString_attributes_(
                    title, attrs
                )
                button.setAttributedTitle_(astr)
            except Exception:  # noqa: BLE001
                try:
                    button.setTitle_(title)
                except Exception:  # noqa: BLE001
                    pass
            try:
                item.setLength_(self._NSVariableStatusItemLength)
            except Exception:  # noqa: BLE001
                try:
                    item.setLength_(28.0)
                except Exception:  # noqa: BLE001
                    pass

    def _refresh_menu_items(self, menu) -> None:  # noqa: ANN001
        try:
            count = menu.numberOfItems()
            if count < 1:
                return
            status = menu.itemAtIndex_(0)
            if status is not None:
                status.setTitle_(self.status_label[:64])
            # 索引：0 状态, 1 分隔, 2 立即, 3 自启, 4 关于, 5 分隔, 6 退出
            if count > 3:
                auto = menu.itemAtIndex_(3)
                if auto is not None:
                    auto.setState_(1 if is_autostart_enabled() else 0)
        except Exception:  # noqa: BLE001
            LOG.debug("刷新菜单栏菜单失败", exc_info=True)

    def _on_main(self, fn: Callable[[], None]) -> None:
        """把回调丢到 Tk 主线程，避免 AppKit 菜单动作里直接调 tkinter 导致崩溃退出。"""
        root = self._tk_root
        if root is not None:
            try:
                root.after(0, fn)  # type: ignore[union-attr]
                return
            except Exception:  # noqa: BLE001
                LOG.debug("调度到 Tk 主线程失败，改为直接调用", exc_info=True)
        try:
            fn()
        except Exception:  # noqa: BLE001
            LOG.exception("菜单回调执行失败")

    def _flash_menu_status(self, text: str, restore_after_ms: int = 4000) -> None:
        """Temporarily show a short note on the disabled status menu item."""
        prev = self.status_label
        self.status_label = text[:64]
        menu = self._menu
        if menu is not None:
            try:
                self._refresh_menu_items(menu)
            except Exception:  # noqa: BLE001
                LOG.debug("刷新关于状态标签失败", exc_info=True)
        root = self._tk_root
        if root is None:
            return

        def _restore() -> None:
            # 勿覆盖期间被倒计时刷新写过的新状态
            if self.status_label == text[:64]:
                self.status_label = prev
                if self._menu is not None:
                    try:
                        self._refresh_menu_items(self._menu)
                    except Exception:  # noqa: BLE001
                        pass

        try:
            root.after(restore_after_ms, _restore)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    def _show_about(self) -> None:
        msg = (
            "定时全屏护眼提醒。\n"
            "默认每 20 分钟休息 20 秒。\n"
            "配置见 config.json。\n\n"
            "macOS：菜单栏图标或右键悬浮倒计时打开菜单。"
        )
        LOG.info("关于：定时全屏护眼提醒，见 config.json")
        self._flash_menu_status("关于：已弹出系统对话框")
        # 切勿对共享 NSApp 调 NSAlert.runModal / tkinter.messagebox：
        # launchd + Tk + AppKit 下会 abort（GIL / Abort trap 6）。
        if IS_MAC and _macos_show_dialog_detached(APP_NAME, msg):
            return
        # 非 macOS 或 osascript 不可用时仅记日志，不阻塞进程
        LOG.info("关于内容：%s", msg.replace("\n", " | "))

    def _toggle_autostart(self) -> None:
        want = not is_autostart_enabled()
        if set_autostart_enabled(want):
            return
        err = "无法修改开机自启。\n请检查 ~/Library/LaunchAgents 权限后重试。"
        LOG.error("无法修改开机自启")
        self._flash_menu_status("自启修改失败")
        if IS_MAC and _macos_show_dialog_detached(APP_NAME, err):
            return



# ---------------------------------------------------------------------------
# 屏幕常显倒计时 HUD（护眼色）
# ---------------------------------------------------------------------------
class CountdownHud:
    # 右下角小浮窗：整分钟倒计时 20→1，护眼色区分紧迫度

    COLOR_OK = ("#2F4F3E", "#C8E6C9")  # 青绿 ≥11
    COLOR_MID = ("#4A4630", "#F0E6B8")  # 暖杏 6-10
    COLOR_NEAR = ("#4A3535", "#F5D0C8")  # 浅陶 1-5
    COLOR_REST = ("#2E4550", "#B2DFDB")  # 雾青 休息中

    def __init__(self, master: tk.Misc) -> None:
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        self.win.configure(bg="#2F4F3E")
        self._drag_x = 0
        self._drag_y = 0
        self._menu_on_break: Optional[Callable[[], None]] = None
        self._menu_on_exit: Optional[Callable[[], None]] = None
        self._menu_on_about: Optional[Callable[[], None]] = None
        self._menu_on_toggle_autostart: Optional[Callable[[], None]] = None
        self._menu_get_status: Optional[Callable[[], str]] = None
        self._menu_get_autostart: Optional[Callable[[], bool]] = None
        self._press_x_root = 0
        self._press_y_root = 0
        self._dragging = False

        num_font = ("PingFang SC", 28, "bold") if IS_MAC else ("Segoe UI", 28, "bold")
        unit_font = ("PingFang SC", 9) if IS_MAC else ("Microsoft YaHei UI", 9)

        self.frame = tk.Frame(self.win, bg="#2F4F3E", padx=14, pady=10)
        self.frame.pack(fill="both", expand=True)
        self.lbl_num = tk.Label(
            self.frame,
            text="--",
            font=num_font,
            fg="#C8E6C9",
            bg="#2F4F3E",
        )
        self.lbl_num.pack()
        self.lbl_unit = tk.Label(
            self.frame,
            text="分钟后休息",
            font=unit_font,
            fg="#A5D6A7",
            bg="#2F4F3E",
        )
        self.lbl_unit.pack()

        for w in (self.win, self.frame, self.lbl_num, self.lbl_unit):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._end_drag)
            w.bind("<Button-3>", self._on_context_menu)
            w.bind("<Control-Button-1>", self._on_context_menu)
            if IS_MAC:
                # macOS tk 常把右键映射为 Button-2
                w.bind("<Button-2>", self._on_context_menu)

        self.win.update_idletasks()
        self._place_bottom_right()

    def bind_status_menu(
        self,
        on_break: Callable[[], None],
        on_exit: Callable[[], None],
        on_about: Callable[[], None],
        on_toggle_autostart: Callable[[], None],
        get_status_label: Callable[[], str],
        get_autostart: Callable[[], bool],
    ) -> None:
        """绑定状态菜单（托盘 / 菜单栏之外的备用入口；macOS 亦保留）。"""
        self._menu_on_break = on_break
        self._menu_on_exit = on_exit
        self._menu_on_about = on_about
        self._menu_on_toggle_autostart = on_toggle_autostart
        self._menu_get_status = get_status_label
        self._menu_get_autostart = get_autostart

    def _place_bottom_right(self) -> None:
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        h = self.win.winfo_reqheight()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = max(0, sw - w - 24)
        y = max(0, sh - h - 72)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    def _start_drag(self, event) -> None:  # noqa: ANN001
        self._drag_x = event.x_root - self.win.winfo_x()
        self._drag_y = event.y_root - self.win.winfo_y()
        self._press_x_root = event.x_root
        self._press_y_root = event.y_root
        self._dragging = False

    def _on_drag(self, event) -> None:  # noqa: ANN001
        if abs(event.x_root - self._press_x_root) > 4 or abs(event.y_root - self._press_y_root) > 4:
            self._dragging = True
        self.win.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    def _end_drag(self, event) -> None:  # noqa: ANN001
        # 预留：左键短按不弹菜单，避免与拖动冲突；菜单用右键 / Control+点击
        _ = event

    def _on_context_menu(self, event) -> str:  # noqa: ANN001
        if self._menu_on_break is None:
            return "break"
        menu = tk.Menu(self.win, tearoff=0)
        status = self._menu_get_status() if self._menu_get_status else APP_NAME
        menu.add_command(label=status[:64], state="disabled")
        menu.add_separator()
        menu.add_command(label="立即开始休息", command=self._menu_on_break)
        auto_on = bool(self._menu_get_autostart() if self._menu_get_autostart else False)
        auto_label = "开机自动启动 ✓" if auto_on else "开机自动启动"
        menu.add_command(label=auto_label, command=self._menu_on_toggle_autostart)
        menu.add_command(label="关于", command=self._menu_on_about)
        menu.add_separator()
        menu.add_command(label="退出", command=self._menu_on_exit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:  # noqa: BLE001
                pass
        return "break"

    def _apply_colors(self, bg: str, fg: str) -> None:
        for w in (self.win, self.frame, self.lbl_num, self.lbl_unit):
            w.configure(bg=bg)
        self.lbl_num.configure(fg=fg)
        self.lbl_unit.configure(fg=fg)

    def update(self, remaining_seconds: int, resting: bool) -> None:
        if resting:
            bg, fg = self.COLOR_REST
            self.lbl_num.configure(text="休")
            self.lbl_unit.configure(text="护眼休息中")
            self._apply_colors(bg, fg)
            return

        minutes = max(0, (remaining_seconds + 59) // 60)
        if minutes <= 0:
            bg, fg = self.COLOR_NEAR
            self.lbl_num.configure(text="0")
            self.lbl_unit.configure(text="即将休息")
        elif minutes <= 5:
            bg, fg = self.COLOR_NEAR
            self.lbl_num.configure(text=str(minutes))
            self.lbl_unit.configure(text="分钟后休息")
        elif minutes <= 10:
            bg, fg = self.COLOR_MID
            self.lbl_num.configure(text=str(minutes))
            self.lbl_unit.configure(text="分钟后休息")
        else:
            bg, fg = self.COLOR_OK
            self.lbl_num.configure(text=str(minutes))
            self.lbl_unit.configure(text="分钟后休息")
        self._apply_colors(bg, fg)

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 主应用
# ---------------------------------------------------------------------------
class EyeCareApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        # 防止意外关闭主窗口导致无主循环；真正退出走 request_exit
        self.root.protocol("WM_DELETE_WINDOW", self.request_exit)

        self._overlay: Optional[BreakOverlay] = None
        self._break_lock = threading.Lock()
        self._stopping = False
        self._next_break_at = time.monotonic() + config.interval_minutes * 60
        self._scheduler_job: Optional[str] = None

        on_break = lambda: self.root.after(0, self.start_break)
        on_exit = lambda: self.root.after(0, self.request_exit)
        tip = f"{APP_NAME}（间隔 {config.interval_minutes:g} 分钟）"
        if IS_WINDOWS:
            self.tray = TrayIcon(on_break=on_break, on_exit=on_exit, tooltip=tip)
        else:
            # macOS / 其它：MacStatusUI（HUD 菜单）；保持与 TrayIcon 相同的 start/stop/set_status
            self.tray = MacStatusUI(on_break=on_break, on_exit=on_exit, tooltip=tip)
        self.hud = CountdownHud(self.root)
        self._wire_hud_menu()
        atexit.register(self._cleanup)

    def _wire_hud_menu(self) -> None:
        """HUD 右键菜单：macOS 主入口；Windows 作为托盘补充。"""
        def on_about() -> None:
            if IS_WINDOWS and isinstance(self.tray, TrayIcon):
                try:
                    ctypes.windll.user32.MessageBoxW(
                        None,
                        "定时全屏护眼提醒。\n默认每 20 分钟休息 20 秒。\n配置见 config.json。",
                        APP_NAME,
                        0,
                    )
                    return
                except Exception:  # noqa: BLE001
                    pass
            try:
                from tkinter import messagebox

                messagebox.showinfo(
                    APP_NAME,
                    "定时全屏护眼提醒。\n默认每 20 分钟休息 20 秒。\n配置见 config.json。",
                )
            except Exception:  # noqa: BLE001
                LOG.info("关于：见 config.json")

        def on_toggle_autostart() -> None:
            want = not is_autostart_enabled()
            ok = set_autostart_enabled(want)
            if not ok:
                try:
                    from tkinter import messagebox

                    messagebox.showerror(APP_NAME, "无法修改开机自启，请检查权限后重试。")
                except Exception:  # noqa: BLE001
                    LOG.error("无法修改开机自启")

        if isinstance(self.tray, MacStatusUI):
            self.tray.attach_hud(self.hud)
        else:
            self.hud.bind_status_menu(
                on_break=lambda: self.root.after(0, self.start_break),
                on_exit=lambda: self.root.after(0, self.request_exit),
                on_about=on_about,
                on_toggle_autostart=on_toggle_autostart,
                get_status_label=lambda: self.tray.status_label,
                get_autostart=is_autostart_enabled,
            )

    def run(self) -> None:
        interval_sec = self.config.interval_minutes * 60
        LOG.info(
            "%s 已启动 | 间隔 %.3g 分钟 | 休息 %d 秒 | 锁屏=%s | 允许跳过=%s",
            APP_NAME,
            self.config.interval_minutes,
            self.config.break_seconds,
            self.config.lock_workstation,
            self.config.allow_skip,
        )
        if IS_WINDOWS:
            LOG.info("系统托盘图标已启用：右键可「立即休息 / 开机自启 / 退出」；HUD 亦可右键。")
        elif IS_MAC:
            LOG.info(
                "macOS：菜单栏状态项（若 AppKit 可用）+ 悬浮 HUD 右键菜单。"
                "按 Ctrl+C 也可退出。"
            )
        else:
            LOG.info("有限模式（非 Windows/macOS）。HUD 右键菜单可用；按 Ctrl+C 退出。")

        self.tray.start()
        self._schedule_check()
        try:
            self.root.mainloop()
        finally:
            self._cleanup()

    def _schedule_check(self) -> None:
        if self._stopping:
            return
        now = time.monotonic()
        if now >= self._next_break_at and self._overlay is None:
            self.start_break()
        self._refresh_tray_countdown()
        # 每秒检查一次，便于托盘“立即休息”与退出及时响应
        self._scheduler_job = self.root.after(1000, self._schedule_check)

    def _refresh_tray_countdown(self) -> None:
        if self._overlay is not None:
            tip = f"{APP_NAME}\n休息中…"
            menu = "状态：休息中…"
            remaining = 0
            resting = True
        else:
            remaining = max(0, int(self._next_break_at - time.monotonic() + 0.999))
            minutes, seconds = divmod(remaining, 60)
            hours, minutes = divmod(minutes, 60)
            if hours > 0:
                clock = f"{hours:d}:{minutes:02d}:{seconds:02d}"
            else:
                clock = f"{minutes:02d}:{seconds:02d}"
            tip = f"{APP_NAME}\n下次休息：{clock}"
            menu = f"下次休息：{clock}"
            resting = False
        if resting:
            mins_for_icon = 0
        else:
            mins_for_icon = max(0, (remaining + 59) // 60)
        self.tray.set_status(tip, menu, minutes=mins_for_icon, resting=resting)
        try:
            self.hud.update(remaining, resting)
        except Exception:  # noqa: BLE001
            pass

    def start_break(self) -> None:
        if self._stopping:
            return
        with self._break_lock:
            if self._overlay is not None:
                return
            self._overlay = BreakOverlay(self.root, self.config, on_closed=self._on_break_closed)
            self._overlay.show()

    def _on_break_closed(self) -> None:
        self._overlay = None
        wait = self.config.interval_minutes * 60
        self._next_break_at = time.monotonic() + wait
        if wait < 60:
            LOG.info("下次休息将在 %.0f 秒后", wait)
        else:
            LOG.info("下次休息将在 %.1f 分钟后", self.config.interval_minutes)

    def request_exit(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        LOG.info("正在退出…")
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None
        self._cleanup()
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _cleanup(self) -> None:
        try:
            self.tray.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, "hud", None) is not None:
                self.hud.destroy()
                self.hud = None  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            pass
        if self._overlay is not None:
            try:
                self._overlay.close()
            except Exception:  # noqa: BLE001
                pass
            self._overlay = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Windows / macOS 护眼锁屏助手：定时全屏遮罩提醒远眺休息。",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"配置文件路径（默认：{DEFAULT_CONFIG_PATH.name}）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="休息间隔（分钟），覆盖配置文件",
    )
    parser.add_argument(
        "--break-seconds",
        type=int,
        default=None,
        dest="break_seconds",
        help="休息时长（秒），覆盖配置文件",
    )
    parser.add_argument(
        "--lock",
        action="store_true",
        help="休息开始时真正锁屏（Windows: LockWorkStation；macOS: Control+Command+Q）",
    )
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="允许在倒计时结束前跳过（会二次确认）",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="立即开始一次休息，结束后退出（便于测试）",
    )
    parser.add_argument(
        "--demo-seconds",
        type=float,
        default=None,
        help="调试：将间隔设为 N 秒（例如 5），便于快速验证",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出调试日志",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    remember_persistent_argv(raw_argv)
    args = parse_args(raw_argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        config = load_config(args.config)
    except Exception as exc:  # noqa: BLE001
        LOG.error("读取配置失败：%s", exc)
        return 2

    if args.interval is not None:
        config.interval_minutes = args.interval
    if args.break_seconds is not None:
        config.break_seconds = args.break_seconds
    if args.lock:
        config.lock_workstation = True
    if args.allow_skip:
        config.allow_skip = True
    if args.demo_seconds is not None:
        config.interval_minutes = max(args.demo_seconds, 0.05) / 60.0

    try:
        config.validate()
    except ValueError as exc:
        LOG.error("%s", exc)
        return 2

    app = EyeCareApp(config)

    def _sig_handler(_signum, _frame) -> None:  # noqa: ANN001
        app.root.after(0, app.request_exit)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:  # noqa: BLE001
        pass

    if args.once:
        # 立刻休息一次，结束后退出
        def after_closed() -> None:
            app.request_exit()

        def kickoff() -> None:
            overlay = BreakOverlay(app.root, config, on_closed=after_closed)
            app._overlay = overlay
            overlay.show()

        app.root.after(200, kickoff)
        app.tray.start()
        try:
            app.root.mainloop()
        finally:
            app._cleanup()
        return 0

    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
