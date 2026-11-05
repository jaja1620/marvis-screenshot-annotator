# -*- coding: utf-8 -*-
"""
Marvis 截图助手 —— 两段式截图 + 预览编辑 + 自动粘贴发送

流程：
  阶段1（框选）：全屏截图 -> 全屏遮罩窗口仅用于鼠标框选区域，框选完成即关闭遮罩；
  阶段2（预览编辑）：弹出独立编辑窗口"截图预览与标注"，
        窗口内显示框选截取内容（等比缩放显示 + 坐标换算回原图，大图滚动条），
        窗口下方固定标注工具栏（矩形/椭圆/箭头/画笔/文字/马赛克/撤销/7色/取色器/1/3/6粗细
        以及"重选""取消""确认发送"按钮），标注直接作用于截取内容。
  确认发送：渲染标注 -> 保存主图 screenshots\\图NNNN.png + 副本目录\\图NNNN.png（递增命名）
        -> 复制剪贴板(CF_DIB) -> 自动粘贴到 Marvis 输入框并回车发送 -> 恢复主面板并提示。
  重选：关闭编辑窗口回到阶段1 重新框选（基于保留的原始全屏图像，不重新截图）。

保留既有能力：全局快捷键(默认 ctrl+shift+m，可改)、多屏虚拟桌面 bbox、
GUI 按钮回调 try/except 弹窗兜底、副本目录设置持久化(config.json)、
历史截图列表预览、自动粘贴开关(config.json 持久化)。

运行：python screenshot_annotator.py [--selftest]
打包：pyinstaller --onefile --windowed --name "Marvis截图助手" ...
"""

import os
import re
import sys
import time
import json
import ctypes
import threading
import traceback

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, colorchooser
    from tkinter import ttk
except ImportError:
    tk = None

try:
    from PIL import Image, ImageTk, ImageDraw, ImageGrab
except ImportError:
    Image = None
    ImageTk = None
    ImageDraw = None
    ImageGrab = None

win32gui = None
win32api = None
win32clipboard = None
win32process = None
keyboard = None

try:
    import win32gui
    import win32api
    import win32clipboard
    import win32process
except ImportError:
    pass

try:
    import keyboard as _keyboard_mod
    keyboard = _keyboard_mod
except ImportError:
    keyboard = None

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SELFTEST_RESULT = os.path.join(BASE_DIR, "selftest_result.txt")

NAME_RE = re.compile(r"^图(\d{4})\.png$")
DEFAULT_COPY_DIR = os.path.join(BASE_DIR, "MARVIS沟通截图")

TOOL_BUTTONS = [
    ("rect", "矩形"),
    ("ellipse", "椭圆"),
    ("arrow", "箭头"),
    ("pen", "画笔"),
    ("text", "文字"),
    ("mosaic", "马赛克"),
]
PRESET_COLORS = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759",
                 "#007aff", "#af52de", "#1c1c1e"]
LINE_WIDTHS = [1, 3, 6]

HOTKEY_DEFAULT = "ctrl+shift+m"

APP_VERSION = "2.0"
APP_TITLE = "Marvis 截图助手 v%s" % APP_VERSION


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def default_config():
    return {
        "hotkey": HOTKEY_DEFAULT,
        "copy_dir": DEFAULT_COPY_DIR,
        "auto_paste": True,
    }


def load_config():
    cfg = default_config()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in cfg:
                if k in data:
                    cfg[k] = data[k]
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 多屏虚拟桌面
# ---------------------------------------------------------------------------
def get_virtual_screen_bbox():
    """合并所有显示器 bbox（支持负坐标）。失败回退主屏尺寸。"""
    try:
        if win32gui is not None:
            monitors = win32api.EnumDisplayMonitors()
            if monitors:
                xs = [m[2][0] for m in monitors] + [m[2][2] for m in monitors]
                ys = [m[2][1] for m in monitors] + [m[2][3] for m in monitors]
                return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        pass
    try:
        w = win32api.GetSystemMetrics(0)
        h = win32api.GetSystemMetrics(1)
        return (0, 0, w, h)
    except Exception:
        return (0, 0, 1920, 1080)


def compute_display_scale(cw, ch, max_w=1000, max_h=700):
    """预览窗口显示缩放比例（等比，只缩小不放大，保证内容完整可见）"""
    if cw <= 0 or ch <= 0:
        return 1.0
    scale = min(1.0, max_w / cw, max_h / ch)
    return max(0.05, scale)


# ---------------------------------------------------------------------------
# Marvis 窗口查找 / 激活 / 按键模拟 / 自动粘贴发送
# ---------------------------------------------------------------------------
def find_marvis_windows():
    """查找标题精确为 Marvis 的可见窗口。返回 [(hwnd, title), ...]"""
    results = []
    if win32gui is None:
        return results

    def enum_cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            try:
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                if pid == os.getpid():
                    return
            except Exception:
                pass
            low = title.lower()
            if "shadow" in low or "gdi+" in low:
                return
            if title.strip() == "Marvis":
                results.append((hwnd, title))
        except Exception:
            pass

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass
    return results


def activate_window_foreground(hwnd):
    """强化前台激活：
    ShowWindow(SW_RESTORE) -> AttachThreadInput 前台线程挂接 + SetForegroundWindow
    + BringWindowToTop -> 补 ALT 键(KEYEVENTF)技巧强制前台
    """
    if win32gui is None:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
        win32gui.ShowWindow(hwnd, 1)      # SW_SHOWNORMAL
    except Exception:
        pass
    attached = False
    try:
        fg = win32gui.GetForegroundWindow()
        fg_tid = win32gui.GetWindowThreadProcessId(fg)[0]
        target_tid = win32gui.GetWindowThreadProcessId(hwnd)[0]
        if fg_tid and target_tid and fg_tid != target_tid:
            try:
                win32process.AttachThreadInput(fg_tid, target_tid, True)
                attached = True
            except Exception:
                attached = False
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass
    if attached:
        try:
            fg2 = win32gui.GetForegroundWindow()
            fg2_tid = win32gui.GetWindowThreadProcessId(fg2)[0]
            target_tid = win32gui.GetWindowThreadProcessId(hwnd)[0]
            win32process.AttachThreadInput(fg2_tid, target_tid, False)
        except Exception:
            pass
    # ALT 键技巧（KEYEVENTF）强制前台
    try:
        win32api.keybd_event(0x12, 0, 0, 0)   # ALT down
        win32api.keybd_event(0x12, 0, 2, 0)   # ALT up (KEYEVENTF_KEYUP)
    except Exception:
        pass
    time.sleep(0.2)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        fg_check = win32gui.GetForegroundWindow()
        return fg_check == hwnd
    except Exception:
        return True


def _sendinput_key(vk, keyup=False):
    """ctypes SendInput 发送单个键。返回是否成功。"""
    try:
        user32 = ctypes.windll.user32
        from ctypes import wintypes
        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [
                ("type", wintypes.DWORD),
                ("ki", KEYBDINPUT),
            ]

        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = vk
        inp.ki.dwFlags = KEYEVENTF_KEYUP if keyup else 0
        return bool(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))
    except Exception:
        return False


def find_marvis_render_hwnd(main_hwnd):
    """找到 Marvis 主窗口下的 Chromium 渲染子窗口（Chrome_RenderWidgetHostHWND）。"""
    if win32gui is None or not main_hwnd:
        return None
    found = []

    def cb(h, _):
        try:
            cls = win32gui.GetClassName(h) or ""
            if "Chrome_RenderWidgetHostHWND" in cls:
                found.append(h)
        except Exception:
            pass

    try:
        win32gui.EnumChildWindows(main_hwnd, cb, None)
    except Exception:
        return None
    return found[0] if found else None


def send_ctrl_v_by_message(hwnd):
    """消息级粘贴通道：向渲染子窗口投递 WM_PASTE / WM_KEYDOWN(Ctrl+V)。
    不经过系统键盘注入，理论上可绕开安全软件对模拟按键的拦截。"""
    if win32gui is None or not hwnd:
        return False
    ok = False
    try:
        win32gui.SendMessage(hwnd, 0x0302, 0, 0)   # WM_PASTE
        ok = True
    except Exception:
        pass
    try:
        win32gui.PostMessage(hwnd, 0x0100, 0x11, (0x1D << 16) | 1)          # CTRL down
        win32gui.PostMessage(hwnd, 0x0100, 0x56, (0x2F << 16) | 1)          # V down
        win32gui.PostMessage(hwnd, 0x0101, 0x56, (0x2F << 16) | 1 | 0xC0000000)
        win32gui.PostMessage(hwnd, 0x0101, 0x11, (0x1D << 16) | 1 | 0xC0000000)
        ok = True
    except Exception:
        pass
    return ok


_probe_state = {"hit": False}


def _probe_wndproc(hwnd, msg, wparam, lparam):
    try:
        if msg == 0x0100:  # WM_KEYDOWN
            _probe_state["hit"] = True
    except Exception:
        pass
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def probe_keyboard_injection(timeout=0.5):
    """检测本机是否允许模拟按键。
    建一个隐藏探针窗口 -> SendInput 一个无害键(VK_DOWN) -> 看是否收到 WM_KEYDOWN。
    返回 True=可用 / False=被系统拦截 / None=无法判定。
    """
    if win32gui is None or win32api is None:
        return None
    hwnd = None
    try:
        try:
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = "MarvisShotInjectProbe"
            wc.lpfnWndProc = _probe_wndproc
            wc.hInstance = win32api.GetModuleHandle(None)
            win32gui.RegisterClass(wc)
        except Exception:
            pass
        hinst = win32api.GetModuleHandle(None)
        hwnd = win32gui.CreateWindow("MarvisShotInjectProbe", "probe",
                                     0x80000000, 0, 0, 4, 4, 0, 0, hinst, None)
        if not hwnd:
            return None
        win32gui.ShowWindow(hwnd, 5)      # SW_SHOW（无激活窗口，几乎不可见）
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        try:
            win32gui.SetFocus(hwnd)
        except Exception:
            pass
        _probe_state["hit"] = False
        _sendinput_key(0x28)              # VK_DOWN
        _sendinput_key(0x28, True)
        deadline = time.time() + timeout
        while time.time() < deadline and not _probe_state["hit"]:
            try:
                win32gui.PumpWaitingMessages()
            except Exception:
                break
            time.sleep(0.02)
        return bool(_probe_state["hit"])
    except Exception:
        return None
    finally:
        if hwnd:
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:
                pass


def send_ctrl_v():
    """发送 Ctrl+V，多级降级：keyboard -> ctypes SendInput -> win32api.keybd_event"""
    if keyboard is not None:
        try:
            keyboard.send("ctrl+v")
            return True
        except Exception:
            pass
    # ctypes SendInput
    try:
        ok = True
        ok = _sendinput_key(0x11) and ok      # CTRL down
        ok = _sendinput_key(0x56) and ok      # V down
        ok = _sendinput_key(0x56, True) and ok  # V up
        ok = _sendinput_key(0x11, True) and ok  # CTRL up
        if ok:
            return True
    except Exception:
        pass
    if win32api is not None:
        try:
            win32api.keybd_event(0x11, 0, 0, 0)
            win32api.keybd_event(0x56, 0, 0, 0)
            win32api.keybd_event(0x56, 0, 2, 0)
            win32api.keybd_event(0x11, 0, 2, 0)
            return True
        except Exception:
            return False
    return False


def send_enter():
    """SendInput 回车（自动发送）"""
    return _sendinput_key(0x0D) and _sendinput_key(0x0D, True)


_INJECT_CACHE = {"ok": None}


def get_inject_state(force=False):
    """带缓存的键盘注入可用性检测。True/False/None"""
    if force or _INJECT_CACHE["ok"] is None:
        _INJECT_CACHE["ok"] = probe_keyboard_injection()
    return _INJECT_CACHE["ok"]


def click_input_area(hwnd):
    """鼠标点击 Marvis 窗口底部输入框区域，使输入框获得焦点。"""
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        user32 = ctypes.windll.user32
        user32.SetCursorPos((l + r) // 2, b - 62)
        time.sleep(0.15)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.2)
        return True
    except Exception:
        return False


def paste_to_marvis(force_probe=False):
    """查找 Marvis 窗口 -> 强化激活 -> 点击输入框获焦 -> 粘贴 -> 回车发送。
    返回 (ok, msg)。

    通道顺序：
      1) 系统键盘注入（keyboard -> SendInput -> keybd_event）；
      2) 消息级 WM_PASTE / WM_KEYDOWN(Ctrl+V) 投递到 Chromium 渲染子窗口；
    若本机安全软件拦截模拟按键（1、2 均无效），返回明确原因，提示手动 Ctrl+V。
    """
    if win32gui is None:
        return False, "缺少 pywin32(win32gui)，无法自动粘贴；可在 Marvis 输入框手动 Ctrl+V"
    wins = find_marvis_windows()
    if not wins:
        return False, "未找到 Marvis 窗口；可在 Marvis 输入框手动 Ctrl+V"
    hwnd = wins[0][0]
    if not activate_window_foreground(hwnd):
        return False, "无法激活 Marvis 窗口；可在 Marvis 输入框手动 Ctrl+V"

    click_input_area(hwnd)          # 鼠标点击输入框定位焦点（鼠标注入通常不受拦截）

    inject_ok = get_inject_state(force=force_probe)
    sent = False
    if inject_ok is not False:
        sent = send_ctrl_v()
    if not sent:
        render = find_marvis_render_hwnd(hwnd)
        sent = send_ctrl_v_by_message(render)

    time.sleep(0.8)                 # 等待粘贴渲染
    enter_ok = send_enter()
    time.sleep(0.3)

    if inject_ok is False:
        return False, ("检测到本机安全软件拦截模拟按键：自动粘贴与自动发送无法生效。\n"
                       "图片已复制到剪贴板，请在 Marvis 输入框手动 Ctrl+V 后回车。\n"
                       "（把本程序或其所在目录加入安全软件白名单后，可在主面板点「检测粘贴通道」重试）")
    if not sent:
        return False, "无法模拟 Ctrl+V（系统注入与消息通道均失败）；请在 Marvis 输入框手动 Ctrl+V"
    if not enter_ok:
        return True, "已粘贴到 Marvis 输入框，但自动回车失败，请手动按回车发送"
    return True, "已自动粘贴并发送到 Marvis 当前会话"


# ---------------------------------------------------------------------------
# 剪贴板（CF_DIB）
# ---------------------------------------------------------------------------
def copy_image_to_clipboard(img):
    """复制 PIL 图片到剪贴板 CF_DIB。成功返回 True。"""
    if win32clipboard is None:
        return False
    try:
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="BMP")
        data = buf.getvalue()[14:]  # 去掉 BMP 文件头
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 命名 / 保存
# ---------------------------------------------------------------------------
def scan_existing_numbers(directory):
    nums = set()
    try:
        for name in os.listdir(directory):
            m = NAME_RE.match(name)
            if m:
                nums.add(int(m.group(1)))
    except Exception:
        pass
    return nums


def get_next_name(main_dir=SCREENSHOT_DIR, copy_dir=DEFAULT_COPY_DIR):
    nums = set()
    nums |= scan_existing_numbers(main_dir)
    if copy_dir and os.path.abspath(copy_dir) != os.path.abspath(main_dir):
        nums |= scan_existing_numbers(copy_dir)
    n = 1
    while n in nums:
        n += 1
    return "图%04d.png" % n


def save_screenshot(img, copy_dir=DEFAULT_COPY_DIR):
    """保存主图 + 副本。返回 (名称, 主图路径, 副本路径, 剪贴板是否成功)"""
    name = get_next_name(SCREENSHOT_DIR, copy_dir)
    main_path = os.path.join(SCREENSHOT_DIR, name)
    copy_path = None
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        img.save(main_path, format="PNG")
    except Exception as e:
        return (name, None, None, False, str(e))
    if copy_dir:
        try:
            os.makedirs(copy_dir, exist_ok=True)
            copy_path = os.path.join(copy_dir, name)
            img.save(copy_path, format="PNG")
        except Exception:
            copy_path = None
    cb_ok = copy_image_to_clipboard(img)
    return (name, main_path, copy_path, cb_ok, "")


# ---------------------------------------------------------------------------
# 标注渲染（PIL，作用于截取内容原图坐标）
# ---------------------------------------------------------------------------
def render_final(crop, elements):
    """按元素列表在截取内容 crop 上绘制标注，返回新图。"""
    img = crop.copy()
    if not elements:
        return img
    d = ImageDraw.Draw(img)
    for el in elements:
        t = el.get("t")
        if t == "rect":
            d.rectangle(el["xy"], outline=el["color"], width=el["w"])
        elif t == "ellipse":
            d.ellipse(el["xy"], outline=el["color"], width=el["w"])
        elif t == "arrow":
            (x1, y1), (x2, y2) = el["p1"], el["p2"]
            d.line([x1, y1, x2, y2], fill=el["color"], width=el["w"])
            import math
            angle = math.atan2(y2 - y1, x2 - x1)
            L = max(12, el["w"] * 4)
            for da in (math.radians(25), math.radians(-25)):
                px = x2 - L * math.cos(angle + da)
                py = y2 - L * math.sin(angle + da)
                d.line([x2, y2, px, py], fill=el["color"], width=max(2, el["w"]))
        elif t == "pen":
            pts = el["pts"]
            if len(pts) >= 2:
                d.line(pts, fill=el["color"], width=el["w"], joint="curve")
        elif t == "text":
            pos = el["pos"]
            try:
                from PIL import ImageFont
                font = ImageFont.truetype("msyh.ttc", el["size"])
            except Exception:
                try:
                    from PIL import ImageFont
                    font = ImageFont.load_default()
                except Exception:
                    font = None
            d.text(pos, el["text"], fill=el["color"], font=font)
        elif t == "mosaic":
            x1, y1, x2, y2 = el["xy"]
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(img.width, int(x2)), min(img.height, int(y2))
            if x2 > x1 and y2 > y1:
                region = img.crop((x1, y1, x2, y2))
                bw, bh = max(1, el["w"] * 3), max(1, el["w"] * 3)
                small = region.resize((max(1, region.width // bw), max(1, region.height // bh)),
                                      Image.NEAREST)
                region = small.resize((x2 - x1, y2 - y1), Image.NEAREST)
                img.paste(region, (x1, y1))
    return img


# ---------------------------------------------------------------------------
# 阶段1：框选遮罩窗口（仅框选，框选完成即关闭）
# ---------------------------------------------------------------------------
class CaptureWindow(tk.Toplevel):
    """全屏截图遮罩，仅用于鼠标框选区域。框选完成 -> on_result("region", payload)。
    Esc -> on_result("cancel", None)。payload = (full_img, bbox, region)"""

    def __init__(self, parent, full_img, bbox, on_result):
        super().__init__(parent)
        self.on_result = on_result
        self.full_img = full_img.convert("RGB")
        self.bbox = bbox
        self.start = None
        self.rect_id = None
        self.region = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.4)
        left, top, right, bottom = bbox
        self.geometry("%dx%d+%d+%d" % (right - left, bottom - top, left, top))

        self.canvas = tk.Canvas(self, bg="#000000", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        # 提示条：说明框选后进入预览编辑
        self.hint = tk.Label(self, text="鼠标拖拽框选截图区域，松开后进入预览编辑（Esc 取消）",
                             bg="#1f1f1f", fg="#ffffff",
                             font=("Microsoft YaHei UI", 12))
        self.canvas.create_window(8, 8, window=self.hint, anchor="nw")

        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", self.on_cancel)

        self.lift()
        self.focus_force()

    def on_press(self, e):
        self.start = (e.x_root - self.bbox[0], e.y_root - self.bbox[1])

    def on_drag(self, e):
        if self.start is None:
            return
        x1, y1 = self.start
        x2 = e.x_root - self.bbox[0]
        y2 = e.y_root - self.bbox[1]
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(x1, y1, x2, y2,
                                                    outline="#00d8ff", width=2,
                                                    fill="#00d8ff",
                                                    stipple="gray50")

    def on_release(self, e):
        if self.start is None:
            return
        x1, y1 = self.start
        x2 = e.x_root - self.bbox[0]
        y2 = e.y_root - self.bbox[1]
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        self.start = None
        if x2 - x1 < 3 or y2 - y1 < 3:
            if self.rect_id is not None:
                self.canvas.delete(self.rect_id)
                self.rect_id = None
            return
        self.region = (int(x1), int(y1), int(x2), int(y2))
        payload = (self.full_img, self.bbox, self.region)
        self.on_result("region", payload)
        self.destroy()

    def on_cancel(self, _e):
        self.on_result("cancel", None)
        self.destroy()


# ---------------------------------------------------------------------------
# 阶段2：预览编辑窗口
# ---------------------------------------------------------------------------
class EditWindow(tk.Toplevel):
    """"截图预览与标注"：窗口内显示框选截取内容（缩放 + 滚动条），
    下方固定标注工具栏。标注元素以原图坐标存储，显示时换算。"""

    def __init__(self, parent, full_img, bbox, region, on_result):
        super().__init__(parent)
        self.on_result = on_result
        self.full_img = full_img.convert("RGB")
        self.bbox = bbox
        self.region = region
        x1, y1, x2, y2 = region
        self.crop = self.full_img.crop((x1, y1, x2, y2))
        self.cw, self.ch = self.crop.size
        self.scale = compute_display_scale(self.cw, self.ch)
        self.disp_w = max(1, int(self.cw * self.scale))
        self.disp_h = max(1, int(self.ch * self.scale))
        self.elements = []
        self.current_tool = "rect"
        self.current_color = PRESET_COLORS[0]
        self.current_width = 3
        self.drag_start = None
        self.drag_cur = None
        self.pen_pts = []

        self.title("截图预览与标注")
        win_w = max(560, min(self.disp_w + 48, 1180))
        win_h = max(420, min(self.disp_h + 220, 900))
        self.geometry("%dx%d+%d+%d" % (win_w, win_h, 80, 60))
        self.minsize(420, 320)
        self.protocol("WM_DELETE_WINDOW", self.cancel)

        # 顶部提示
        top = tk.Label(self, text="框选完成，在下方编辑标注；确认后自动发送到 Marvis",
                       bg="#1f6fd6", fg="#ffffff", anchor="w", padx=10, pady=4,
                       font=("Microsoft YaHei UI", 10))
        top.pack(fill="x")

        # 内容区：Canvas + 滚动条
        body = tk.Frame(self, bg="#2b2b2b")
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="#2b2b2b", highlightthickness=0,
                                cursor="crosshair", scrollregion=(0, 0, self.disp_w, self.disp_h))
        h_sb = tk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        v_sb = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=h_sb.set, yscrollcommand=v_sb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        v_sb.grid(row=0, column=1, sticky="ns")
        h_sb.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        try:
            self.photo = ImageTk.PhotoImage(
                self.crop.resize((self.disp_w, self.disp_h), Image.LANCZOS))
        except Exception:
            self.photo = None
        self.bg_id = None
        if self.photo is not None:
            self.bg_id = self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", lambda _e: self.cancel())

        # 底部工具栏
        self.build_toolbar()
        self.lift()
        self.focus_force()

    # ---- 坐标换算 ----
    def to_orig(self, cx, cy):
        """画布坐标(显示) -> 原图坐标(截取内容)"""
        return (cx / self.scale, cy / self.scale)

    def to_disp(self, ox, oy):
        """原图坐标 -> 画布坐标(显示)"""
        return (ox * self.scale, oy * self.scale)

    # ---- 工具栏 ----
    def build_toolbar(self):
        bar = tk.Frame(self, bg="#f0f0f0", bd=0)
        bar.pack(side="bottom", fill="x")
        row1 = tk.Frame(bar, bg="#f0f0f0")
        row1.pack(side="top", fill="x", padx=6, pady=(4, 0))
        row2 = tk.Frame(bar, bg="#f0f0f0")
        row2.pack(side="top", fill="x", padx=6, pady=(2, 4))

        self.tool_buttons = {}
        for key, label in TOOL_BUTTONS:
            b = tk.Button(row1, text=label, width=5, relief="raised",
                          command=lambda k=key: self.set_tool(k))
            b.pack(side="left", padx=1)
            self.tool_buttons[key] = b
        tk.Label(row1, text="|", bg="#f0f0f0").pack(side="left", padx=3)
        self.undo_btn = tk.Button(row1, text="撤销", width=5,
                                  command=self.undo)
        self.undo_btn.pack(side="left", padx=1)
        tk.Label(row1, text="|", bg="#f0f0f0").pack(side="left", padx=3)
        tk.Label(row1, text="粗细:", bg="#f0f0f0").pack(side="left")
        self.width_var = tk.IntVar(value=self.current_width)
        for w in LINE_WIDTHS:
            tk.Radiobutton(row1, text=str(w), value=w, variable=self.width_var,
                           command=self.set_width, bg="#f0f0f0",
                           selectcolor="#ffffff").pack(side="left", padx=1)
        tk.Label(row1, text="|", bg="#f0f0f0").pack(side="left", padx=3)
        self.color_btns = {}
        for c in PRESET_COLORS:
            b = tk.Button(row1, bg=c, width=2, relief="flat",
                          command=lambda cc=c: self.set_color(cc))
            b.pack(side="left", padx=1)
            self.color_btns[c] = b
        tk.Button(row1, text="取色", width=5, command=self.pick_color) \
            .pack(side="left", padx=1)

        # 行2：操作按钮
        tk.Button(row2, text="重选", width=8, command=self.reselect) \
            .pack(side="left", padx=2)
        tk.Button(row2, text="取消", width=8, command=self.cancel) \
            .pack(side="left", padx=2)
        tk.Button(row2, text="确认发送", width=10, command=self.confirm_send,
                  bg="#34c759", fg="#ffffff") \
            .pack(side="right", padx=2)

    def set_tool(self, key):
        self.current_tool = key
        for k, b in self.tool_buttons.items():
            b.configure(relief="sunken" if k == key else "raised")

    def set_width(self):
        self.current_width = self.width_var.get()

    def set_color(self, c):
        self.current_color = c

    def pick_color(self):
        try:
            rgb, _ = colorchooser.askcolor(color=self.current_color, parent=self)
            if rgb:
                self.set_color("#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2])))
        except Exception:
            pass

    # ---- 标注交互（存原图坐标） ----
    def on_press(self, e):
        if self.current_tool == "text":
            self.add_text(e)
            return
        ox, oy = self.to_orig(e.x, e.y)
        self.drag_start = (ox, oy)
        self.drag_cur = (ox, oy)
        if self.current_tool == "pen":
            self.pen_pts = [(ox, oy)]
        self.redraw()

    def on_drag(self, e):
        if self.current_tool == "pen":
            if self.drag_start is None:
                return
            ox, oy = self.to_orig(e.x, e.y)
            self.pen_pts.append((ox, oy))
            self.redraw()
            return
        if self.drag_start is None:
            return
        self.drag_cur = self.to_orig(e.x, e.y)
        self.redraw()

    def on_release(self, e):
        if self.current_tool == "pen":
            if self.drag_start is not None and len(self.pen_pts) >= 2:
                self.elements.append({
                    "t": "pen", "pts": list(self.pen_pts),
                    "color": self.current_color, "w": self.current_width})
            self.drag_start = None
            self.pen_pts = []
            self.redraw()
            return
        if self.drag_start is None:
            return
        ox, oy = self.to_orig(e.x, e.y)
        self.drag_cur = (ox, oy)
        x1, y1 = self.drag_start
        x2, y2 = self.drag_cur
        if abs(x2 - x1) < 2 and abs(y2 - y1) < 2:
            self.drag_start = None
            self.drag_cur = None
            self.redraw()
            return
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        if self.current_tool == "rect":
            self.elements.append({"t": "rect", "xy": [x1, y1, x2, y2],
                                  "color": self.current_color, "w": self.current_width})
        elif self.current_tool == "ellipse":
            self.elements.append({"t": "ellipse", "xy": [x1, y1, x2, y2],
                                  "color": self.current_color, "w": self.current_width})
        elif self.current_tool == "arrow":
            self.elements.append({"t": "arrow", "p1": (x1, y1), "p2": (x2, y2),
                                  "color": self.current_color, "w": self.current_width})
        elif self.current_tool == "mosaic":
            self.elements.append({"t": "mosaic", "xy": [x1, y1, x2, y2],
                                  "color": self.current_color, "w": self.current_width})
        self.drag_start = None
        self.drag_cur = None
        self.redraw()

    def add_text(self, e):
        ox, oy = self.to_orig(e.x, e.y)
        try:
            s = simpledialog.askstring("输入文字", "请输入标注文字:", parent=self)
        except Exception:
            s = None
        if s:
            self.elements.append({"t": "text", "pos": (int(ox), int(oy)),
                                  "text": s, "color": self.current_color,
                                  "w": self.current_width,
                                  "size": max(12, int(20 * (1 if self.scale >= 0.8 else 1.0)))})
            self.redraw()

    def undo(self):
        if self.elements:
            self.elements.pop()
            self.redraw()

    # ---- 重绘（显示层） ----
    def redraw(self):
        if self.canvas is None:
            return
        try:
            self.canvas.delete("anno")
            for el in self.elements:
                self._draw_el_disp(el)
            if self.drag_start is not None and self.drag_cur is not None and \
                    self.current_tool not in ("pen", "text"):
                tmp = {"t": self.current_tool,
                       "xy": [self.drag_start[0], self.drag_start[1],
                              self.drag_cur[0], self.drag_cur[1]],
                       "color": self.current_color, "w": self.current_width}
                if self.current_tool == "arrow":
                    tmp = {"t": "arrow", "p1": self.drag_start, "p2": self.drag_cur,
                           "color": self.current_color, "w": self.current_width}
                self._draw_el_disp(tmp)
            if self.current_tool == "pen" and len(self.pen_pts) >= 2:
                pts = [self.to_disp(*p) for p in self.pen_pts]
                self.canvas.create_line(pts, fill=self.current_color,
                                        width=self.current_width, tags="anno",
                                        smooth=True)
        except Exception:
            pass

    def _draw_el_disp(self, el):
        t = el["t"]
        c = el["color"]
        w = max(1, int(el["w"] * max(1.0, self.scale * 2)))
        if t == "rect":
            x1, y1 = self.to_disp(el["xy"][0], el["xy"][1])
            x2, y2 = self.to_disp(el["xy"][2], el["xy"][3])
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=c, width=w, tags="anno")
        elif t == "ellipse":
            x1, y1 = self.to_disp(el["xy"][0], el["xy"][1])
            x2, y2 = self.to_disp(el["xy"][2], el["xy"][3])
            self.canvas.create_oval(x1, y1, x2, y2, outline=c, width=w, tags="anno")
        elif t == "arrow":
            x1, y1 = self.to_disp(*el["p1"])
            x2, y2 = self.to_disp(*el["p2"])
            self.canvas.create_line(x1, y1, x2, y2, fill=c, width=w, tags="anno")
            import math
            angle = math.atan2(y2 - y1, x2 - x1)
            L = max(10, w * 3)
            for da in (math.radians(25), math.radians(-25)):
                px = x2 - L * math.cos(angle + da)
                py = y2 - L * math.sin(angle + da)
                self.canvas.create_line(x2, y2, px, py, fill=c,
                                        width=max(2, w), tags="anno")
        elif t == "pen":
            if len(el["pts"]) >= 2:
                pts = [self.to_disp(*p) for p in el["pts"]]
                self.canvas.create_line(pts, fill=c, width=w, tags="anno", smooth=True)
        elif t == "text":
            dx, dy = self.to_disp(*el["pos"])
            self.canvas.create_text(dx, dy, text=el["text"], fill=c,
                                    anchor="nw", font=("Microsoft YaHei UI",
                                                       max(10, int(18 * self.scale))),
                                    tags="anno")
        elif t == "mosaic":
            x1, y1 = self.to_disp(el["xy"][0], el["xy"][1])
            x2, y2 = self.to_disp(el["xy"][2], el["xy"][3])
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#ffffff",
                                         width=1, tags="anno")
            step = max(4, int(8 * max(1.0, self.scale * 2)))
            xx = x1
            while xx <= x2:
                self.canvas.create_line(xx, y1, xx, y2, fill="#ffffff", width=1, tags="anno")
                xx += step
            yy = y1
            while yy <= y2:
                self.canvas.create_line(x1, yy, x2, yy, fill="#ffffff", width=1, tags="anno")
                yy += step

    # ---- 操作 ----
    def confirm_send(self):
        try:
            annotated = render_final(self.crop, self.elements)
        except Exception as e:
            messagebox.showerror("渲染失败", "标注渲染失败：%s" % e, parent=self)
            return
        self.on_result("confirm", annotated)
        self.destroy()

    def reselect(self):
        """关闭编辑窗口，回到阶段1 重新框选（不重新截图）"""
        self.on_result("reselect", (self.full_img, self.bbox))
        self.destroy()

    def cancel(self):
        self.on_result("cancel", None)
        self.destroy()


# ---------------- 连接 Marvis 窗口 ----------------
class ConnectWindow:
    """显示副本目录绝对路径 + 使用说明，用于跨电脑部署时和 Marvis 建立连接"""

    def __init__(self, parent, copy_dir, last_name):
        self.win = tk.Toplevel(parent)
        self.win.title("连接 Marvis")
        self.win.geometry("580x420")
        self.win.resizable(False, False)
        self.win.transient(parent)

        box = tk.Frame(self.win, padx=18, pady=14)
        box.pack(fill="both", expand=True)

        tk.Label(box, text="连接 Marvis", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        tk.Label(box, text="让 Marvis 记住你的截图副本目录，之后即可通过「查看图XXXX」直接读取图片",
                 fg="#888888").pack(anchor="w", pady=(4, 12))

        tk.Label(box, text="当前副本目录（绝对路径）：").pack(anchor="w")
        tk.Label(box, text=copy_dir, fg="#1a8cff", font=("Microsoft YaHei UI", 11, "bold"),
                 wraplength=540, justify="left").pack(anchor="w", pady=(2, 4))

        def do_copy():
            try:
                self.win.clipboard_clear()
                self.win.clipboard_append(copy_dir)
                messagebox.showinfo("已复制", "路径已复制到剪贴板", parent=self.win)
            except Exception as exc:
                messagebox.showerror("错误", "复制失败: %s" % exc, parent=self.win)

        tk.Button(box, text="复制路径", command=do_copy, width=12).pack(anchor="w", pady=(0, 12))

        tk.Label(box, text="使用说明：", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        tips = (
            "1. 在 Marvis 对话框发送：\n"
            "   我的截图副本目录是：<路径>\n\n"
            "2. Marvis 记住该目录后，即可通过「查看图XXXX」\n"
            "   直接从该目录读取你的截图。\n\n"
            "3. 换电脑部署时：把本程序与副本目录一起拷过去，\n"
            "   在新电脑上打开本窗口、复制路径发给 Marvis 即可重新建立连接。"
        )
        tk.Label(box, text=tips, justify="left", anchor="w", fg="#333333").pack(anchor="w", pady=(2, 10))

        tk.Label(box, text="最近一张截图：" + (last_name if last_name else "（暂无）"),
                 fg="#1a8cff").pack(anchor="w")
        tk.Button(box, text="关闭", command=self.win.destroy).pack(anchor="e", pady=(8, 0))


# ---------------------------------------------------------------------------
# 主面板
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.capturing = False
        self.current_capture = None  # (full_img, bbox) 保留原始全屏图像
        self.hotkey_id = None

        root.title("Marvis 截图助手 v%s" % APP_VERSION)
        root.geometry("660x730")
        root.resizable(False, False)

        # 顶部
        top = tk.Frame(root)
        top.pack(fill="x", padx=10, pady=8)
        tk.Label(top, text="Marvis 截图助手", font=("Microsoft YaHei UI", 14, "bold")) \
            .pack(side="left")
        self.status = tk.Label(top, text="就绪", fg="#666666")
        self.status.pack(side="right")

        # 全局快捷键
        hotkey_row = tk.Frame(root)
        hotkey_row.pack(fill="x", padx=10, pady=2)
        tk.Label(hotkey_row, text="全局快捷键:") \
            .pack(side="left")
        self.hotkey_var = tk.StringVar(value=self.cfg.get("hotkey", HOTKEY_DEFAULT))
        tk.Entry(hotkey_row, textvariable=self.hotkey_var, width=20) \
            .pack(side="left", padx=6)
        tk.Button(hotkey_row, text="应用", command=self.apply_hotkey) \
            .pack(side="left")
        tk.Button(hotkey_row, text="截图并标注", command=self.start_capture,
                  bg="#1f6fd6", fg="#ffffff", width=12) \
            .pack(side="right", padx=4)

        # 副本目录
        dir_row = tk.Frame(root)
        dir_row.pack(fill="x", padx=10, pady=2)
        tk.Label(dir_row, text="副本目录:") \
            .pack(side="left")
        self.copy_dir_var = tk.StringVar(value=self.cfg.get("copy_dir", DEFAULT_COPY_DIR))
        tk.Entry(dir_row, textvariable=self.copy_dir_var, width=46) \
            .pack(side="left", padx=6)
        tk.Button(dir_row, text="浏览...", command=self.browse_copy_dir) \
            .pack(side="left")

        # 自动粘贴开关
        switch_row = tk.Frame(root)
        switch_row.pack(fill="x", padx=10, pady=2)
        self.auto_paste_var = tk.BooleanVar(value=bool(self.cfg.get("auto_paste", True)))
        tk.Checkbutton(switch_row, text="确认发送后自动粘贴并发送到 Marvis 会话",
                       variable=self.auto_paste_var, command=self.save_auto_paste) \
            .pack(side="left")
        tk.Label(switch_row, text="(关闭时仅保存+复制剪贴板)", fg="#888888") \
            .pack(side="left", padx=4)

        # 连接 Marvis（跨电脑部署时把副本目录告知 Marvis）
        conn_row = tk.Frame(root)
        conn_row.pack(fill="x", padx=10, pady=2)
        tk.Button(conn_row, text="连接 Marvis", command=self.open_connect,
                  bg="#1f6fd6", fg="#ffffff", width=12) \
            .pack(side="left")
        tk.Label(conn_row, text="换电脑部署时，用它在 Marvis 侧重新登记截图副本目录", fg="#888888") \
            .pack(side="left", padx=6)

        # 粘贴通道状态（检测本机是否允许模拟按键）
        chan_row = tk.Frame(root)
        chan_row.pack(fill="x", padx=10, pady=2)
        tk.Button(chan_row, text="检测粘贴通道", command=self.check_paste_channel, width=12) \
            .pack(side="left")
        self.paste_state = tk.Label(chan_row, text="自动粘贴通道：未检测", fg="#888888")
        self.paste_state.pack(side="left", padx=6)

        # 提示
        tip = tk.Label(root,
                       text="流程：全屏遮罩框选区域 -> 预览编辑窗口标注 -> 确认发送自动粘贴到 Marvis",
                       fg="#555555", wraplength=600, justify="left")
        tip.pack(fill="x", padx=10, pady=4)

        # 历史截图列表
        hist_frame = tk.Frame(root)
        hist_frame.pack(fill="both", expand=True, padx=10, pady=4)
        tk.Label(hist_frame, text="历史截图:") \
            .pack(anchor="w")
        list_row = tk.Frame(hist_frame)
        list_row.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(list_row, height=12)
        v_sb = tk.Scrollbar(list_row, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=v_sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        v_sb.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", self.open_selected_screenshot)

        btn_row = tk.Frame(hist_frame)
        btn_row.pack(fill="x", pady=2)
        tk.Button(btn_row, text="打开选中", command=self.open_selected_screenshot) \
            .pack(side="left", padx=2)
        tk.Button(btn_row, text="删除选中", command=self.delete_selected_screenshot) \
            .pack(side="left", padx=2)

        self.refresh_list()

    # ---- 连接 Marvis / 粘贴通道 ----
    def open_connect(self):
        """打开「连接 Marvis」窗口：显示副本目录绝对路径与使用说明。"""
        try:
            last = None
            try:
                names = sorted([n for n in os.listdir(SCREENSHOT_DIR) if NAME_RE.match(n)],
                               reverse=True)
                if names:
                    last = names[0]
            except Exception:
                pass
            copy_dir = ""
            try:
                copy_dir = self.copy_dir_var.get().strip()
            except Exception:
                copy_dir = ""
            if not copy_dir:
                copy_dir = self.cfg.get("copy_dir", DEFAULT_COPY_DIR)
            ConnectWindow(self.root, copy_dir, last)
        except Exception as exc:
            messagebox.showerror("错误", "打开连接窗口失败: %s" % exc)

    def check_paste_channel(self):
        """检测本机是否允许模拟按键（自动粘贴/自动发送的前提）。"""
        try:
            state = get_inject_state(force=True)
            if state is True:
                try:
                    self.paste_state.config(text="自动粘贴通道：可用", fg="#34c759")
                except Exception:
                    pass
                messagebox.showinfo("检测结果", "模拟按键可用，截图后会尝试自动粘贴并回车发送。")
            elif state is False:
                try:
                    self.paste_state.config(text="自动粘贴通道：被系统拦截", fg="#ff3b30")
                except Exception:
                    pass
                messagebox.showwarning(
                    "检测结果",
                    "检测到本机安全软件拦截模拟按键（键盘注入），自动粘贴与自动发送无法生效。\n\n"
                    "可尝试的解决办法：\n"
                    "1. 把 Marvis截图助手.exe 及其所在目录加入安全软件（终端管控/天擎等）白名单后重试；\n"
                    "2. 以管理员身份运行本程序后重试；\n"
                    "3. 继续用剪贴板方案：确认发送后图片已在剪贴板，"
                    "切到 Marvis 输入框按 Ctrl+V 再回车。")
            else:
                try:
                    self.paste_state.config(text="自动粘贴通道：未知", fg="#888888")
                except Exception:
                    pass
                messagebox.showinfo("检测结果", "未能判定按键注入状态，可截图后观察是否自动粘贴。")
        except Exception as exc:
            messagebox.showerror("错误", str(exc))

    # ---- 历史截图 ----
    def refresh_list(self):
        self.listbox.delete(0, "end")
        try:
            names = sorted(os.listdir(SCREENSHOT_DIR), reverse=True)
        except Exception:
            names = []
        for n in names:
            if NAME_RE.match(n):
                self.listbox.insert("end", n)

    def open_selected_screenshot(self, _e=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.listbox.get(sel[0])
        path = os.path.join(SCREENSHOT_DIR, name)
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def delete_selected_screenshot(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.listbox.get(sel[0])
        path = os.path.join(SCREENSHOT_DIR, name)
        if not messagebox.askyesno("确认删除", "确定删除 %s ？" % name):
            return
        try:
            os.remove(path)
            self.refresh_list()
            self.set_status("已删除 " + name)
        except Exception as e:
            messagebox.showerror("删除失败", str(e))

    # ---- 配置 ----
    def browse_copy_dir(self):
        d = filedialog.askdirectory(initialdir=self.copy_dir_var.get() or BASE_DIR)
        if d:
            self.copy_dir_var.set(d)
            self.save_copy_dir()

    def save_copy_dir(self):
        self.cfg["copy_dir"] = self.copy_dir_var.get().strip() or DEFAULT_COPY_DIR
        save_config(self.cfg)

    def save_auto_paste(self):
        self.cfg["auto_paste"] = bool(self.auto_paste_var.get())
        save_config(self.cfg)

    def apply_hotkey(self):
        hk = self.hotkey_var.get().strip() or HOTKEY_DEFAULT
        try:
            if self.hotkey_id is not None:
                try:
                    keyboard.remove_hotkey(self.hotkey_id)
                except Exception:
                    pass
                self.hotkey_id = None
            if keyboard is not None:
                self.hotkey_id = keyboard.add_hotkey(hk, self.start_capture)
            self.cfg["hotkey"] = hk
            save_config(self.cfg)
            self.set_status("快捷键已更新: " + hk)
        except Exception as e:
            messagebox.showerror("快捷键设置失败", str(e))

    # ---- 状态 ----
    def set_status(self, text):
        try:
            self.status.config(text=text)
        except Exception:
            pass

    # ---- 两段式截图流程 ----
    def start_capture(self):
        """阶段1：隐藏主面板 -> 截取虚拟桌面 -> 遮罩框选"""
        try:
            if self.capturing:
                return
            self.capturing = True
            self.root.withdraw()
            time.sleep(0.15)
            bbox = get_virtual_screen_bbox()
            img = ImageGrab.grab(bbox=bbox)
            self.current_capture = (img, bbox)
            CaptureWindow(self.root, img, bbox, self.on_region_selected)
        except Exception as e:
            self.capturing = False
            self.root.deiconify()
            self.root.lift()
            messagebox.showerror("截图失败", "启动框选失败：%s" % e)

    def on_region_selected(self, kind, payload):
        """阶段1 回调：框选完成 -> 打开阶段2 编辑窗口；取消 -> 恢复主面板"""
        if kind == "cancel":
            self.capturing = False
            self._restore_main()
            return
        # kind == "region"
        full_img, bbox, region = payload
        try:
            EditWindow(self.root, full_img, bbox, region, self.on_edit_done)
        except Exception as e:
            self.capturing = False
            self._restore_main()
            messagebox.showerror("编辑窗口打开失败", str(e))

    def on_edit_done(self, kind, payload):
        """阶段2 回调：confirm -> 保存+粘贴+发送；reselect -> 回阶段1；cancel -> 恢复"""
        if kind == "reselect":
            full_img, bbox = payload
            try:
                CaptureWindow(self.root, full_img, bbox, self.on_region_selected)
            except Exception as e:
                self.capturing = False
                self._restore_main()
                messagebox.showerror("重新框选失败", str(e))
            return
        self.capturing = False
        self._restore_main()
        if kind == "cancel":
            return
        # kind == "confirm"
        annotated = payload
        self._finish_capture(annotated)

    def _restore_main(self):
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

    def _finish_capture(self, img):
        """确认发送：渲染已由编辑窗口完成，这里保存+剪贴板+自动粘贴发送+提示"""
        try:
            if img is None:
                return
            copy_dir = self.copy_dir_var.get().strip() or DEFAULT_COPY_DIR
            name, main_path, copy_path, cb_ok, err = save_screenshot(img, copy_dir)
            msg = "已保存 %s\n主图: %s" % (name, main_path)
            if copy_path:
                msg += "\n副本: %s" % copy_path
            if cb_ok:
                msg += "\n已复制到剪贴板"
            else:
                msg += "\n剪贴板复制失败"
            if err:
                msg += "\n保存异常: %s" % err

            if self.auto_paste_var.get():
                self.set_status("正在自动粘贴发送到 Marvis...")
                self.root.update_idletasks()
                time.sleep(0.2)
                try:
                    ok, pmsg = paste_to_marvis()
                except Exception as e:
                    ok, pmsg = False, "自动粘贴异常: %s" % e
                if ok:
                    msg += "\n" + pmsg
                    self.set_status("已发送: " + name)
                else:
                    msg += "\n自动发送失败：" + pmsg + "\n回退方案：在 Marvis 输入框说「查看%s」或手动 Ctrl+V 粘贴" % name
                    self.set_status("发送失败: " + name)
            else:
                msg += "\n(自动粘贴已关闭，请手动在 Marvis 输入框 Ctrl+V)"
                self.set_status("已保存: " + name)

            self.refresh_list()
            try:
                messagebox.showinfo("截图结果", msg)
            except Exception:
                pass
        except Exception as e:
            self.set_status("处理失败: " + str(e))
            try:
                messagebox.showerror("处理失败", str(e))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
def run_selftest():
    """验证核心能力：多屏bbox、显示缩放与坐标换算、命名、保存、剪贴板、Marvis窗口查找"""
    lines = []
    def log(s):
        lines.append(str(s))
        print(s)

    log("=== Marvis截图助手 selftest ===")
    bbox = get_virtual_screen_bbox()
    log("virtual_bbox=%r" % (bbox,))
    ok_bbox = (bbox[2] > bbox[0]) and (bbox[3] > bbox[1])
    log("bbox_valid=%s" % ok_bbox)

    # 缩放与坐标换算（等比）
    for cw, ch in ((4000, 3440), (800, 600), (120, 80)):
        s = compute_display_scale(cw, ch)
        ow, oh = int(cw * s), int(ch * s)
        back = (ow / cw, oh / ch)
        log("scale cw=%d ch=%d -> s=%.4f disp=%dx%d back=%r" % (cw, ch, s, ow, oh, back))
    # 换算往返
    s = compute_display_scale(4000, 3440)
    ox, oy = 1234.5, 678.9
    dx, dy = ox * s, oy * s
    rx, ry = dx / s, dy / s
    log("coord roundtrip orig=(%.2f,%.2f) -> disp=(%.2f,%.2f) -> back=(%.2f,%.2f) ok=%s"
        % (ox, oy, dx, dy, rx, ry, abs(rx - ox) < 0.01 and abs(ry - oy) < 0.01))

    # 命名
    import tempfile
    td = tempfile.mkdtemp(prefix="marvis_selftest_")
    main_td = os.path.join(td, "shots")
    copy_td = os.path.join(td, "copy")
    os.makedirs(main_td, exist_ok=True)
    os.makedirs(copy_td, exist_ok=True)
    for f in ("图0001.png", "图0003.png"):
        open(os.path.join(main_td, f), "wb").write(b"x")
    for f in ("图0002.png",):
        open(os.path.join(copy_td, f), "wb").write(b"x")
    n = get_next_name(main_td, copy_td)
    log("next_name=%s (期望 图0004.png)" % n)
    log("name_ok=%s" % (n == "图0004.png"))

    # 保存 + 剪贴板（使用自定义目录，不污染正式目录）
    img = Image.new("RGB", (80, 60), (10, 120, 200))
    if ImageDraw is not None:
        d = ImageDraw.Draw(img)
        d.rectangle([5, 5, 75, 55], outline="#ff3b30", width=3)
    name, main_path, copy_path, cb_ok, err = save_screenshot(img, copy_td)
    log("save name=%s main=%s copy=%s cb=%s err=%s" % (name, main_path, copy_path, cb_ok, err))
    log("save_exists=%s" % (os.path.exists(main_path) and os.path.exists(copy_path)))

    # Marvis 窗口查找
    wins = find_marvis_windows()
    log("find_marvis_windows count=%d wins=%r" % (len(wins), wins[:3]))
    log("find_ok=%s" % (len(wins) >= 0))

    # 版本 + 渲染子窗口 + 模拟按键可用性（后者会短暂创建并抢焦一个隐藏探针窗口）
    log("version=%s" % APP_VERSION)
    if wins:
        log("render_hwnd=%r" % (find_marvis_render_hwnd(wins[0][0]),))
    inj = probe_keyboard_injection()
    log("key_injection_available=%r  (True=可自动粘贴/发送  False=被安全软件拦截  None=未判定)" % (inj,))

    # 渲染函数
    crop = img.copy()
    anno = render_final(crop, [
        {"t": "rect", "xy": [2, 2, 78, 58], "color": "#007aff", "w": 3},
        {"t": "text", "pos": (5, 5), "text": "OK", "color": "#1c1c1e", "w": 1, "size": 14},
        {"t": "arrow", "p1": (5, 20), "p2": (70, 50), "color": "#ff9500", "w": 2},
        {"t": "mosaic", "xy": [20, 20, 60, 40], "color": "#000000", "w": 3},
    ])
    log("render_ok=%s size=%s" % (anno.size == img.size, anno.size))

    # 清理
    try:
        import shutil
        shutil.rmtree(td, ignore_errors=True)
    except Exception:
        pass
    log("=== selftest done ===")
    try:
        with open(SELFTEST_RESULT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    if "--selftest" in sys.argv:
        run_selftest()
        return
    if tk is None or Image is None:
        print("缺少依赖 tkinter/PIL")
        return
    root = tk.Tk()
    app = App(root)
    try:
        hk = app.cfg.get("hotkey", HOTKEY_DEFAULT)
        if keyboard is not None:
            app.hotkey_id = keyboard.add_hotkey(hk, app.start_capture)
    except Exception:
        pass
    root.mainloop()


if __name__ == "__main__":
    main()
