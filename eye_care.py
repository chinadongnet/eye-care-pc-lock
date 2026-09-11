"""
护眼锁屏助手 — Windows 定时全屏休息提醒。

仅使用 Python 标准库（tkinter + ctypes），无需 pip 安装。
目标系统：Windows 10+ / Windows Server 2022，Python 3.11+。
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import signal
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

try:
    import tkinter as tk
    from tkinter import font as tkfont
except ImportError as exc:  # pragma: no cover
    print("错误：无法导入 tkinter。请安装带 Tcl/Tk 的 Python（官方 Windows 安装包通常已包含）。", file=sys.stderr)
    raise SystemExit(1) from exc

if IS_WINDOWS:
    import ctypes
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


def get_monitors() -> List[MonitorRect]:
    """返回所有显示器的虚拟桌面坐标；失败时至少返回主屏。"""
    if not IS_WINDOWS:
        # 非 Windows：用 tkinter 探测主屏尺寸（开发/冒烟用）
        root = tk.Tk()
        root.withdraw()
        w = root.winfo_screenwidth()
        h = root.winfo_screenheight()
        root.destroy()
        return [(0, 0, w, h)]

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


def lock_workstation() -> None:
    """调用 Windows LockWorkStation（需用户会话，无需管理员）。"""
    if not IS_WINDOWS:
        LOG.warning("当前非 Windows，跳过 LockWorkStation。")
        return
    try:
        ctypes.windll.user32.LockWorkStation()
    except Exception:  # noqa: BLE001
        LOG.exception("LockWorkStation 调用失败")


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
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "SimHei", "Noto Sans CJK SC", "WenQuanYi Micro Hei"):
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
# 开机自启（当前用户 Startup 目录）
# ---------------------------------------------------------------------------
AUTOSTART_NAME = "护眼锁屏助手"
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


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("找不到 APPDATA，无法配置开机自启")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_candidates() -> List[Path]:
    d = _startup_dir()
    return [d / f"{AUTOSTART_NAME}.lnk", d / f"{AUTOSTART_NAME}.bat"]


def is_autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return any(p.is_file() for p in autostart_candidates())
    except Exception:  # noqa: BLE001
        return False


def set_autostart_enabled(enabled: bool) -> bool:
    """启用/关闭登录自启。成功返回 True。"""
    if not IS_WINDOWS:
        return False
    try:
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

        pythonw = Path(sys.executable)
        # Prefer pythonw.exe alongside python.exe
        if pythonw.name.lower() == "python.exe":
            candidate = pythonw.with_name("pythonw.exe")
            if candidate.is_file():
                pythonw = candidate
        script = Path(__file__).resolve()
        args_str = _autostart_argument_string(script)
        # Create .lnk via PowerShell for consistency with Explorer Startup
        # Escape for PowerShell single-quoted string: ' -> ''
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
        # Use bat fallback if PowerShell fails
        try:
            r = __import__("subprocess").run(
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

        self.frame = tk.Frame(self.win, bg="#2F4F3E", padx=14, pady=10)
        self.frame.pack(fill="both", expand=True)
        self.lbl_num = tk.Label(
            self.frame,
            text="--",
            font=("Segoe UI", 28, "bold"),
            fg="#C8E6C9",
            bg="#2F4F3E",
        )
        self.lbl_num.pack()
        self.lbl_unit = tk.Label(
            self.frame,
            text="分钟后休息",
            font=("Microsoft YaHei UI", 9),
            fg="#A5D6A7",
            bg="#2F4F3E",
        )
        self.lbl_unit.pack()

        for w in (self.win, self.frame, self.lbl_num, self.lbl_unit):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        self.win.update_idletasks()
        self._place_bottom_right()

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

    def _on_drag(self, event) -> None:  # noqa: ANN001
        self.win.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

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

        self.tray = TrayIcon(
            on_break=lambda: self.root.after(0, self.start_break),
            on_exit=lambda: self.root.after(0, self.request_exit),
            tooltip=f"{APP_NAME}（间隔 {config.interval_minutes:g} 分钟）",
        )
        self.hud = CountdownHud(self.root)
        atexit.register(self._cleanup)

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
            LOG.info("系统托盘图标已启用：右键可「立即休息 / 退出」。")
        else:
            LOG.info("演示模式（非 Windows）。按 Ctrl+C 退出。")

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
        description="Windows 护眼锁屏助手：定时全屏遮罩提醒远眺休息。",
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
        help="休息开始时调用 LockWorkStation（真正锁屏，需重新登录）",
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
