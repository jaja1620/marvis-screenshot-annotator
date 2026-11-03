# -*- coding: utf-8 -*-
"""
Marvis 截图助手 (常驻 GUI 版)
- 常驻主面板：启动后立即显示，不自动进入截图；关闭窗口或点"退出"即退出程序
- 全局快捷键触发截图（默认 Ctrl+Shift+M，可修改；keyboard 注册失败降级 pynput）
- 截图流程：隐藏主窗口 -> 全屏截图 -> 鼠标框选标注(矩形/椭圆/箭头/画笔/文字/马赛克/撤销/颜色/粗细)
- 确认后：自动生成简要名称(图0001/图0002...递增) -> 主图存 screenshots\图NNNN.png
  -> 副本存 当前副本目录(默认 MARVIS沟通截图，可在 GUI 自定义并持久化到 config.json)
  -> 复制到剪贴板 -> 恢复主窗口并提示
- 主面板：最近截图、连接 Marvis、副本目录设置、历史截图列表(副本文件夹)、点击预览大图、复制名称
- 连接 Marvis：显示副本目录绝对路径 + 使用说明（对 Marvis 说"我的截图副本目录是：<路径>"）
依赖: pillow, pywin32, keyboard (pynput 可选, 由启动脚本自动安装)
"""

import io
import json
import math
import os
import re
import sys
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog

from PIL import Image, ImageDraw, ImageFont, ImageGrab, ImageTk

try:
    import win32clipboard
except ImportError:
    win32clipboard = None

try:
    import keyboard
except ImportError:
    keyboard = None

try:
    from pynput import keyboard as pynput_keyboard
except ImportError:
    pynput_keyboard = None

APP_TITLE = "Marvis 截图助手"
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(BASE_DIR, "screenshots")
DEFAULT_COPY_DIR = os.path.join(BASE_DIR, "MARVIS沟通截图")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_HOTKEY = "ctrl+shift+m"

TOOL_RECT = "rect"
TOOL_ELLIPSE = "ellipse"
TOOL_ARROW = "arrow"
TOOL_PEN = "pen"
TOOL_TEXT = "text"
TOOL_MOSAIC = "mosaic"

TOOL_NAMES = {
    TOOL_RECT: "矩形",
    TOOL_ELLIPSE: "椭圆",
    TOOL_ARROW: "箭头",
    TOOL_PEN: "画笔",
    TOOL_TEXT: "文字",
    TOOL_MOSAIC: "马赛克",
}

DEFAULT_COLORS = ["#FF0000", "#FF7F00", "#FFFF00", "#00FF00", "#00BFFF", "#0000FF", "#000000"]
WIDTHS = [1, 3, 6]
TEXT_FONT_SIZE = 20
NAME_RE = re.compile(r"^图(\d{4})\.png$")


def load_font(size):
    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def copy_image_to_clipboard(img):
    """把 PIL 图像复制到 Windows 剪贴板(CF_DIB)，返回是否成功"""
    if win32clipboard is None:
        return False
    try:
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, "BMP")
        dib = buffer.getvalue()[14:]  # 去掉 14 字节 BMP 文件头，剩余为 DIB 数据
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


# ---------------- 配置读写 ----------------
def load_config():
    """读取 config.json，返回副本目录；缺失或损坏时返回默认目录"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict) and isinstance(cfg.get("copy_dir"), str) and cfg["copy_dir"].strip():
            return cfg["copy_dir"].strip()
    except Exception:
        pass
    return DEFAULT_COPY_DIR


def save_config(copy_dir):
    """持久化副本目录到 config.json，返回是否成功"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"copy_dir": copy_dir}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ---------------- 命名与保存 ----------------
def get_next_name(copy_dir):
    """扫描主图目录与当前副本目录推断下一序号: 图0001、图0002...(防主图覆盖)"""
    used = set()
    for d in (SAVE_DIR, copy_dir):
        if os.path.isdir(d):
            try:
                for f in os.listdir(d):
                    m = NAME_RE.match(f)
                    if m:
                        used.add(int(m.group(1)))
            except OSError:
                pass
    n = 1
    while n in used:
        n += 1
    return "图%04d" % n


def save_screenshot(img, copy_dir):
    """保存标注后图片：主图 + 副本(自定义目录) + 剪贴板。返回 (名称, 主图路径, 副本路径, 剪贴板是否成功)"""
    os.makedirs(SAVE_DIR, exist_ok=True)
    os.makedirs(copy_dir, exist_ok=True)
    name = get_next_name(copy_dir)
    main_path = os.path.join(SAVE_DIR, name + ".png")
    copy_path = os.path.join(copy_dir, name + ".png")
    img.save(main_path, "PNG")
    img.save(copy_path, "PNG")
    copied = copy_image_to_clipboard(img)
    return name, main_path, copy_path, copied


# ---------------- 截图标注窗口 ----------------
class CaptureWindow(tk.Toplevel):
    """全屏截图 + 框选 + 标注。确认/取消时通过 on_result 回调 (PIL 图 or None)"""

    def __init__(self, parent, img, on_result):
        super().__init__(parent)
        self.on_result = on_result
        self.img = img.convert("RGB")
        self.screen_w = self.winfo_screenwidth()
        self.screen_h = self.winfo_screenheight()
        self.title("截图标注")
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.focus_force()

        self.photo = ImageTk.PhotoImage(self.img)
        self.canvas = tk.Canvas(self, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.bg_id = self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

        self.state = "select"  # select=框选区域, draw=标注模式
        self.start = None
        self.cur = None
        self.region = None
        self.elements = []
        self.temp_id = None
        self.toolbar = None

        self.tool = TOOL_RECT
        self.color = "#FF0000"
        self.width = 3
        self.pen_points = []

        self.bind("<Button-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.confirm())

    # ---------- 鼠标事件 ----------
    def on_press(self, e):
        if self.state == "select":
            self.start = (e.x, e.y)
            self.cur = (e.x, e.y)
            return
        if self.tool == TOOL_TEXT:
            self.add_text(e.x, e.y)
            return
        if self.tool == TOOL_PEN:
            self.pen_points = [(e.x, e.y)]
            return
        self.start = (e.x, e.y)
        self.cur = (e.x, e.y)

    def on_drag(self, e):
        if self.state == "select" and self.start:
            self.cur = (e.x, e.y)
            self._draw_rubber_band()
            return
        if self.state != "draw":
            return
        if self.tool == TOOL_PEN:
            self.pen_points.append((e.x, e.y))
            self._draw_pen_preview()
            return
        if self.tool == TOOL_TEXT:
            return
        self.cur = (e.x, e.y)
        self._draw_shape_preview()

    def on_release(self, e):
        if self.state == "select" and self.start:
            x1, y1 = self.start
            x2, y2 = e.x, e.y
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            if x2 - x1 < 3 or y2 - y1 < 3:
                self.start = None
                self.cur = None
                return
            self.region = (x1, y1, x2, y2)
            self.state = "draw"
            self.start = None
            self.cur = None
            self._remove_temp()
            self._show_toolbar()
            return
        if self.state != "draw":
            return
        if self.tool == TOOL_PEN:
            if len(self.pen_points) >= 2:
                self.elements.append({
                    "type": TOOL_PEN, "points": self.pen_points[:],
                    "color": self.color, "width": self.width,
                })
            self.pen_points = []
            self.redraw_overlay()
            return
        if self.tool in (TOOL_RECT, TOOL_ELLIPSE, TOOL_ARROW, TOOL_MOSAIC):
            if self.start and self.cur:
                x1, y1 = self.start
                x2, y2 = self.cur
                x1, x2 = sorted((x1, x2))
                y1, y2 = sorted((y1, y2))
                if x2 - x1 < 3 or y2 - y1 < 3:
                    self.start = None
                    self.cur = None
                    return
                if self.tool == TOOL_MOSAIC:
                    self.elements.append({"type": TOOL_MOSAIC, "box": (x1, y1, x2, y2)})
                elif self.tool == TOOL_ARROW:
                    self.elements.append({
                        "type": TOOL_ARROW, "start": self.start, "end": self.cur,
                        "color": self.color, "width": self.width,
                    })
                else:
                    self.elements.append({
                        "type": self.tool, "box": (x1, y1, x2, y2),
                        "color": self.color, "width": self.width,
                    })
            self.start = None
            self.cur = None
            self.redraw_overlay()

    # ---------- 预览绘制 ----------
    def _draw_rubber_band(self):
        self._remove_temp()
        x1, y1 = self.start
        x2, y2 = self.cur
        self.temp_id = self.canvas.create_rectangle(
            x1, y1, x2, y2, outline="#1a8cff", width=2, dash=(4, 2)
        )

    def _draw_shape_preview(self):
        self._remove_temp()
        x1, y1 = self.start
        x2, y2 = self.cur
        if self.tool == TOOL_RECT:
            self.temp_id = self.canvas.create_rectangle(x1, y1, x2, y2, outline=self.color, width=self.width)
        elif self.tool == TOOL_ELLIPSE:
            self.temp_id = self.canvas.create_oval(x1, y1, x2, y2, outline=self.color, width=self.width)
        elif self.tool == TOOL_ARROW:
            self.temp_id = self._canvas_arrow(x1, y1, x2, y2, self.color, self.width)
        elif self.tool == TOOL_MOSAIC:
            self.temp_id = self.canvas.create_rectangle(x1, y1, x2, y2, outline="#808080", width=1, dash=(2, 2))

    def _draw_pen_preview(self):
        if len(self.pen_points) >= 2:
            self._remove_temp()
            self.temp_id = self.canvas.create_line(
                *self.pen_points, fill=self.color, width=self.width,
                capstyle="round", joinstyle="round", smooth=True,
            )

    def _canvas_arrow(self, x1, y1, x2, y2, color, width):
        line_id = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width, capstyle="round")
        angle = math.atan2(y2 - y1, x2 - x1)
        head_len = max(10, width * 4)
        p1 = (x2 - head_len * math.cos(angle - 0.5), y2 - head_len * math.sin(angle - 0.5))
        p2 = (x2 - head_len * math.cos(angle + 0.5), y2 - head_len * math.sin(angle + 0.5))
        head_id = self.canvas.create_polygon(x2, y2, p1[0], p1[1], p2[0], p2[1], fill=color, outline=color)
        return (line_id, head_id)

    def _remove_temp(self):
        if self.temp_id is None:
            return
        if isinstance(self.temp_id, tuple):
            for item in self.temp_id:
                self.canvas.delete(item)
        else:
            self.canvas.delete(self.temp_id)
        self.temp_id = None

    def redraw_overlay(self):
        for item in self.canvas.find_all():
            if item != self.bg_id:
                self.canvas.delete(item)
        for el in self.elements:
            t = el["type"]
            if t == TOOL_RECT:
                self.canvas.create_rectangle(*el["box"], outline=el["color"], width=el["width"])
            elif t == TOOL_ELLIPSE:
                self.canvas.create_oval(*el["box"], outline=el["color"], width=el["width"])
            elif t == TOOL_ARROW:
                self._canvas_arrow(el["start"][0], el["start"][1], el["end"][0], el["end"][1], el["color"], el["width"])
            elif t == TOOL_PEN:
                if len(el["points"]) >= 2:
                    self.canvas.create_line(
                        *el["points"], fill=el["color"], width=el["width"],
                        capstyle="round", joinstyle="round", smooth=True,
                    )
            elif t == TOOL_TEXT:
                x, y = el["pos"]
                self.canvas.create_text(
                    x, y, text=el["text"], fill=el["color"],
                    font=("Microsoft YaHei UI", el["size"]), anchor="nw",
                )
            elif t == TOOL_MOSAIC:
                x1, y1, x2, y2 = el["box"]
                self.canvas.create_rectangle(x1, y1, x2, y2, outline="#666666", width=1)
                step = 12
                for gx in range(x1, x2, step):
                    self.canvas.create_line(gx, y1, gx, y2, fill="#9a9a9a")
                for gy in range(y1, y2, step):
                    self.canvas.create_line(x1, gy, x2, gy, fill="#9a9a9a")

    # ---------- 工具栏 ----------
    def _show_toolbar(self):
        if self.toolbar is not None:
            return
        x1, y1, x2, y2 = self.region
        bar = tk.Frame(self, bg="#2b2b2b", padx=6, pady=4)
        for tool in (TOOL_RECT, TOOL_ELLIPSE, TOOL_ARROW, TOOL_PEN, TOOL_TEXT, TOOL_MOSAIC):
            btn = tk.Button(
                bar, text=TOOL_NAMES[tool], command=lambda t=tool: self.set_tool(t),
                relief="flat", width=5, bg="#4a4a4a", fg="white", activebackground="#5a5a5a",
            )
            btn.pack(side="left", padx=1)
            setattr(self, "_btn_" + tool, btn)

        tk.Frame(bar, width=6, bg="#2b2b2b").pack(side="left")
        for c in DEFAULT_COLORS:
            tk.Button(
                bar, bg=c, width=2, relief="flat", activebackground="#ffffff",
                command=lambda cc=c: self.set_color(cc),
            ).pack(side="left", padx=1)
        tk.Button(
            bar, text="更多", command=self.pick_color,
            relief="flat", bg="#4a4a4a", fg="white",
        ).pack(side="left", padx=1)

        tk.Frame(bar, width=6, bg="#2b2b2b").pack(side="left")
        for w in WIDTHS:
            tk.Button(
                bar, text=str(w), command=lambda ww=w: self.set_width(ww),
                relief="flat", bg="#4a4a4a", fg="white",
            ).pack(side="left", padx=1)

        tk.Frame(bar, width=6, bg="#2b2b2b").pack(side="left")
        tk.Button(bar, text="撤销", command=self.undo, relief="flat", bg="#4a4a4a", fg="white").pack(side="left", padx=2)
        tk.Button(bar, text="确认", command=self.confirm, relief="flat", bg="#1a8cff", fg="white", width=6).pack(side="left", padx=4)
        tk.Button(bar, text="取消", command=self.cancel, relief="flat", bg="#c9302c", fg="white", width=6).pack(side="left", padx=2)

        self.update_idletasks()
        th = bar.winfo_reqheight()
        ty = y2 + 10 if y2 + 10 + th <= self.screen_h else max(0, y1 - th - 10)
        tx = max(4, min(x1, self.screen_w - bar.winfo_reqwidth() - 4))
        bar.place(x=tx, y=ty)
        self.toolbar = bar
        self.update_tool_buttons()

    def set_tool(self, tool):
        self.tool = tool
        self.update_tool_buttons()

    def update_tool_buttons(self):
        for tool in TOOL_NAMES:
            btn = getattr(self, "_btn_" + tool, None)
            if btn is not None:
                btn.config(bg="#4a4a4a" if tool != self.tool else "#1a8cff")

    def set_color(self, color):
        self.color = color

    def pick_color(self):
        _, rgb = colorchooser.askcolor(color=self.color, parent=self)
        if rgb:
            self.color = "#%02x%02x%02x" % rgb

    def set_width(self, width):
        self.width = width

    def add_text(self, x, y):
        try:
            text = simpledialog.askstring("文字标注", "请输入文字:", parent=self)
            if text:
                self.elements.append({
                    "type": TOOL_TEXT, "pos": (x, y), "text": text,
                    "color": self.color, "size": TEXT_FONT_SIZE,
                })
                self.redraw_overlay()
        except Exception as exc:
            messagebox.showerror("错误", "文字标注失败: %s" % exc, parent=self)

    def undo(self):
        try:
            if self.elements:
                self.elements.pop()
                self.redraw_overlay()
        except Exception as exc:
            messagebox.showerror("错误", "撤销失败: %s" % exc, parent=self)

    # ---------- 确认 / 取消 ----------
    def confirm(self):
        if self.region is None:
            return
        try:
            annotated = self.render_final()
        except Exception as exc:
            messagebox.showerror("错误", "渲染标注失败: %s" % exc, parent=self)
            return
        self.on_result(annotated)
        self.destroy()

    def cancel(self):
        self.on_result(None)
        self.destroy()

    # ---------- 最终渲染 ----------
    def render_final(self):
        draw_img = self.img.copy()
        d = ImageDraw.Draw(draw_img)
        for el in self.elements:
            t = el["type"]
            if t in (TOOL_RECT, TOOL_ELLIPSE):
                box = el["box"]
                if t == TOOL_RECT:
                    d.rectangle(box, outline=el["color"], width=el["width"])
                else:
                    d.ellipse(box, outline=el["color"], width=el["width"])
            elif t == TOOL_ARROW:
                self._draw_pil_arrow(d, el["start"], el["end"], el["color"], el["width"])
            elif t == TOOL_PEN:
                pts = el["points"]
                if len(pts) >= 2:
                    d.line(pts, fill=el["color"], width=el["width"], joint="curve")
            elif t == TOOL_TEXT:
                font = load_font(el["size"])
                d.text(el["pos"], el["text"], fill=el["color"], font=font)
            elif t == TOOL_MOSAIC:
                draw_img = self._pixelate(draw_img, el["box"])
        return draw_img

    def _draw_pil_arrow(self, d, start, end, color, width):
        x1, y1 = start
        x2, y2 = end
        d.line([x1, y1, x2, y2], fill=color, width=width)
        angle = math.atan2(y2 - y1, x2 - x1)
        head_len = max(10, width * 4)
        p1 = (x2 - head_len * math.cos(angle - 0.5), y2 - head_len * math.sin(angle - 0.5))
        p2 = (x2 - head_len * math.cos(angle + 0.5), y2 - head_len * math.sin(angle + 0.5))
        d.polygon([x2, y2, p1[0], p1[1], p2[0], p2[1]], fill=color, outline=color)

    def _pixelate(self, img, box):
        x1, y1, x2, y2 = box
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        region = img.crop((x1, y1, x2, y2))
        step = max(4, min(24, w // 40))
        small = region.resize((max(1, w // step), max(1, h // step)), Image.NEAREST)
        big = small.resize((w, h), Image.NEAREST)
        img.paste(big, (x1, y1))
        return img


# ---------------- 预览窗口 ----------------
class PreviewWindow:
    def __init__(self, parent, path, name):
        self.win = tk.Toplevel(parent)
        self.win.title(name)
        self.win.geometry("+80+80")
        img = Image.open(path)
        img.thumbnail((900, 620))
        self.photo = ImageTk.PhotoImage(img)
        tk.Label(self.win, image=self.photo).pack(padx=8, pady=8)
        bar = tk.Frame(self.win)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(bar, text="名称: ").pack(side="left")
        tk.Label(bar, text=name, fg="#1a8cff", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        tk.Button(bar, text="复制名称", command=lambda: self.copy_name(name)).pack(side="right")

    def copy_name(self, name):
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(name)
            messagebox.showinfo("已复制", "图片名称已复制: " + name, parent=self.win)
        except Exception as exc:
            messagebox.showerror("错误", "复制失败: %s" % exc, parent=self.win)


# ---------------- 连接 Marvis 窗口 ----------------
class ConnectWindow:
    """显示副本目录绝对路径 + 使用说明，用于和 Marvis 建立连接"""

    def __init__(self, parent, copy_dir, last_name):
        self.win = tk.Toplevel(parent)
        self.win.title("连接 Marvis")
        self.win.geometry("580x400")
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
            "3. 每次截图确认后，也可对 Marvis 说「查看图XXXX」\n"
            "   或在输入框按 Ctrl+V 粘贴。"
        )
        tk.Label(box, text=tips, justify="left", anchor="w", fg="#333333").pack(anchor="w", pady=(2, 10))

        tk.Label(box, text="最近一张截图：" + (last_name if last_name else "（暂无）"),
                 fg="#1a8cff").pack(anchor="w")
        tk.Button(box, text="关闭", command=self.win.destroy).pack(anchor="e", pady=(8, 0))


# ---------------- 常驻主面板 ----------------
class MainApp:
    def __init__(self, copy_dir):
        self.copy_dir = copy_dir
        try:
            os.makedirs(self.copy_dir, exist_ok=True)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("620x620")
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.capturing = False
        self.last_name = None
        self._hk_ids = []
        self._pynput_hk = None
        self._hk_driver = None

        self._build_ui()
        self.refresh_list()
        self._register_hotkey(DEFAULT_HOTKEY, silent=True)

    def _build_ui(self):
        top = tk.Frame(self.root, padx=16, pady=14)
        top.pack(fill="both", expand=True)
        tk.Label(top, text=APP_TITLE, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")

        # 全局快捷键区
        hk = tk.LabelFrame(top, text="全局快捷键（触发全屏截图）", padx=10, pady=8)
        hk.pack(fill="x", pady=(10, 6))
        row = tk.Frame(hk)
        row.pack(fill="x")
        tk.Label(row, text="当前:").pack(side="left")
        self.lbl_hotkey = tk.Label(row, text=DEFAULT_HOTKEY, fg="#1a8cff", width=16)
        self.lbl_hotkey.pack(side="left", padx=4)
        tk.Label(row, text="修改为:").pack(side="left", padx=(14, 0))
        self.entry_hotkey = tk.Entry(row, width=16)
        self.entry_hotkey.insert(0, DEFAULT_HOTKEY)
        self.entry_hotkey.pack(side="left", padx=4)
        tk.Button(hk, text="应用", command=self.apply_hotkey).pack(side="right", padx=(6, 0))
        tk.Button(hk, text="立即截图", command=lambda: self.start_capture("button"),
                  bg="#1a8cff", fg="white", width=10).pack(side="right")
        self.lbl_hotkey_state = tk.Label(hk, text="", fg="#888888", anchor="w")
        self.lbl_hotkey_state.pack(fill="x", pady=(6, 0))

        # 最近截图 + 连接 Marvis
        row2 = tk.Frame(top)
        row2.pack(fill="x", pady=(4, 6))
        self.lbl_recent = tk.Label(row2, text="最近截图：无", font=("Microsoft YaHei UI", 12, "bold"))
        self.lbl_recent.pack(side="left")
        tk.Button(row2, text="连接 Marvis", command=self.open_connect,
                  bg="#1a8cff", fg="white", width=12).pack(side="right")

        # 副本目录设置
        cfg = tk.LabelFrame(top, text="副本目录（截图副本保存位置，可随时修改并持久化）", padx=10, pady=8)
        cfg.pack(fill="x", pady=(0, 6))
        tk.Label(cfg, text="当前生效：").pack(anchor="w")
        self.lbl_copy_dir_cur = tk.Label(cfg, text=self.copy_dir, fg="#1a8cff",
                                         font=("Microsoft YaHei UI", 10), anchor="w", wraplength=560)
        self.lbl_copy_dir_cur.pack(fill="x", pady=(2, 4))
        r3 = tk.Frame(cfg)
        r3.pack(fill="x")
        self.entry_copy_dir = tk.Entry(r3, width=52)
        self.entry_copy_dir.insert(0, self.copy_dir)
        self.entry_copy_dir.pack(side="left", fill="x", expand=True)
        tk.Button(r3, text="浏览", command=self.browse_copy_dir).pack(side="left", padx=(6, 0))
        tk.Button(r3, text="保存", command=self.save_copy_dir, bg="#1a8cff", fg="white").pack(side="left", padx=(4, 0))

        # 历史截图列表
        lst = tk.LabelFrame(top, text="历史截图（副本目录中的图片）", padx=10, pady=8)
        lst.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(lst, height=8, exportselection=False)
        self.listbox.pack(fill="both", expand=True, side="left")
        sb = tk.Scrollbar(lst, orient="vertical", command=self.listbox.yview)
        sb.pack(side="left", fill="y")
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind("<Double-Button-1>", lambda e: self.preview_selected())
        btns = tk.Frame(lst)
        btns.pack(side="left", padx=(10, 0), anchor="n")
        tk.Button(btns, text="预览选中", width=10, command=self.preview_selected).pack(pady=2)
        tk.Button(btns, text="复制名称", width=10, command=self.copy_selected_name).pack(pady=2)
        tk.Button(btns, text="刷新列表", width=10, command=self.refresh_list).pack(pady=2)

        # 底部
        foot = tk.Frame(top)
        foot.pack(fill="x", pady=(10, 0))
        tk.Label(foot, text="截图确认后可对 Marvis 说「查看图0001」或 Ctrl+V 粘贴",
                 fg="#888888").pack(side="left")
        tk.Button(foot, text="退出", command=self.quit_app, bg="#c9302c", fg="white", width=10).pack(side="right")

    # ---------- 连接 Marvis ----------
    def open_connect(self):
        try:
            ConnectWindow(self.root, self.copy_dir, self.last_name)
        except Exception as exc:
            messagebox.showerror("错误", "打开连接窗口失败: %s" % exc, parent=self.root)

    # ---------- 副本目录 ----------
    def browse_copy_dir(self):
        try:
            d = filedialog.askdirectory(title="选择副本目录",
                                        initialdir=self.copy_dir if os.path.isdir(self.copy_dir) else BASE_DIR,
                                        parent=self.root)
            if d:
                self.entry_copy_dir.delete(0, "end")
                self.entry_copy_dir.insert(0, d)
        except Exception as exc:
            messagebox.showerror("错误", "选择目录失败: %s" % exc, parent=self.root)

    def save_copy_dir(self):
        try:
            d = self.entry_copy_dir.get().strip()
            if not d:
                messagebox.showwarning("提示", "请输入或选择目录路径", parent=self.root)
                return
            os.makedirs(d, exist_ok=True)
            if save_config(d):
                self.copy_dir = d
                self.lbl_copy_dir_cur.config(text=d)
                self.refresh_list()
                messagebox.showinfo("成功", "副本目录已更新并保存到 config.json\n%s" % d, parent=self.root)
            else:
                messagebox.showerror("错误", "config.json 写入失败，目录未生效", parent=self.root)
        except Exception as exc:
            messagebox.showerror("错误", "保存副本目录失败: %s" % exc, parent=self.root)

    # ---------- 热键 ----------
    def _hotkey_cb(self):
        try:
            self.root.after(0, lambda: self.start_capture("hotkey"))
        except Exception:
            pass

    def _register_hotkey(self, spec, silent=False):
        self._unregister_hotkey()
        spec = (spec or "").strip().lower()
        if not spec:
            self._set_hk_state("快捷键不能为空", err=True)
            return False
        errs = []
        if keyboard is not None:
            try:
                hid = keyboard.add_hotkey(spec, self._hotkey_cb)
                self._hk_ids.append(hid)
                self._hk_driver = "keyboard"
                self.lbl_hotkey.config(text=spec)
                self._set_hk_state("已注册 (keyboard)：按 %s 触发截图" % spec)
                return True
            except Exception as e:
                errs.append("keyboard: %s" % e)
        if pynput_keyboard is not None:
            try:
                parts = []
                for p in spec.split("+"):
                    p = p.strip()
                    if p in ("ctrl", "control"):
                        parts.append("<ctrl>")
                    elif p == "alt":
                        parts.append("<alt>")
                    elif p == "shift":
                        parts.append("<shift>")
                    elif p in ("win", "cmd", "super"):
                        parts.append("<cmd>")
                    else:
                        parts.append(p)
                pynput_spec = "+".join(parts)
                hk = pynput_keyboard.GlobalHotKeys({pynput_spec: self._hotkey_cb})
                hk.start()
                self._pynput_hk = hk
                self._hk_driver = "pynput"
                self.lbl_hotkey.config(text=spec)
                self._set_hk_state("已注册 (pynput 降级)：按 %s 触发截图" % spec)
                return True
            except Exception as e:
                errs.append("pynput: %s" % e)
        self._set_hk_state("热键注册失败: %s（可用「立即截图」按钮）" % ("; ".join(errs) if errs else "缺少 keyboard/pynput 库"), err=True)
        return False

    def _unregister_hotkey(self):
        for hid in self._hk_ids:
            try:
                if keyboard is not None:
                    keyboard.remove_hotkey(hid)
            except Exception:
                pass
        self._hk_ids = []
        if self._pynput_hk is not None:
            try:
                self._pynput_hk.stop()
            except Exception:
                pass
            self._pynput_hk = None
        self._hk_driver = None

    def _set_hk_state(self, text, err=False):
        try:
            self.lbl_hotkey_state.config(text=text, fg="#c9302c" if err else "#1a8cff")
        except Exception:
            pass

    def apply_hotkey(self):
        try:
            spec = self.entry_hotkey.get().strip().lower()
            if not spec:
                messagebox.showwarning("提示", "请输入快捷键，例如 ctrl+shift+m", parent=self.root)
                return
            ok = self._register_hotkey(spec)
            if ok:
                messagebox.showinfo("成功", "快捷键已更新为: %s" % spec, parent=self.root)
        except Exception as exc:
            messagebox.showerror("错误", "应用快捷键失败: %s" % exc, parent=self.root)

    # ---------- 截图流程 ----------
    def start_capture(self, source="button"):
        if self.capturing:
            return
        try:
            self.capturing = True
            self.root.withdraw()
            self.root.after(150, self._do_capture)
        except Exception as exc:
            self.capturing = False
            try:
                self.root.deiconify()
            except Exception:
                pass
            messagebox.showerror("错误", "启动截图失败: %s" % exc, parent=self.root)

    def _do_capture(self):
        try:
            img = ImageGrab.grab()
            CaptureWindow(self.root, img, self.on_capture_done)
        except Exception as exc:
            self.capturing = False
            try:
                self.root.deiconify()
            except Exception:
                pass
            messagebox.showerror("错误", "截图失败: %s" % exc, parent=self.root)

    def on_capture_done(self, img):
        self.capturing = False
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        if img is None:
            return
        try:
            name, main_path, copy_path, copied = save_screenshot(img, self.copy_dir)
        except Exception as exc:
            messagebox.showerror("保存失败", "截图保存出错: %s" % exc, parent=self.root)
            return
        self.last_name = name
        self.lbl_recent.config(text="最近截图：" + name)
        self.refresh_list()
        tips = "截图已保存并复制到剪贴板！\n\n名称：%s\n\n保存位置：\n%s\n副本：\n%s\n\n现在可以：\n· 直接对 Marvis 说「查看%s」\n· 或在 Marvis 输入框按 Ctrl+V 粘贴" % (name, main_path, copy_path, name)
        if not copied:
            tips += "\n\n[提示] 剪贴板复制失败(缺少 pywin32)，请用启动脚本运行。"
        messagebox.showinfo("完成", tips, parent=self.root)

    # ---------- 列表与预览 ----------
    def refresh_list(self):
        try:
            self.listbox.delete(0, "end")
            items = []
            if os.path.isdir(self.copy_dir):
                for f in os.listdir(self.copy_dir):
                    if NAME_RE.match(f):
                        items.append(f)
            items.sort(key=lambda f: int(NAME_RE.match(f).group(1)), reverse=True)
            for f in items:
                self.listbox.insert("end", f)
            if self.last_name:
                self.lbl_recent.config(text="最近截图：" + self.last_name)
        except Exception as exc:
            messagebox.showerror("错误", "刷新列表失败: %s" % exc, parent=self.root)

    def _selected_file(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择一张截图", parent=self.root)
            return None, None
        fname = self.listbox.get(sel[0])
        path = os.path.join(self.copy_dir, fname)
        return fname, path

    def preview_selected(self):
        try:
            fname, path = self._selected_file()
            if fname is None:
                return
            if not os.path.exists(path):
                messagebox.showerror("错误", "文件不存在: %s" % path, parent=self.root)
                return
            PreviewWindow(self.root, path, fname)
        except Exception as exc:
            messagebox.showerror("错误", "预览失败: %s" % exc, parent=self.root)

    def copy_selected_name(self):
        try:
            fname, _ = self._selected_file()
            if fname is None:
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(fname)
            messagebox.showinfo("已复制", "图片名称已复制: " + fname, parent=self.root)
        except Exception as exc:
            messagebox.showerror("错误", "复制失败: %s" % exc, parent=self.root)

    # ---------- 退出 ----------
    def quit_app(self):
        try:
            self._unregister_hotkey()
        except Exception:
            pass
        self.root.destroy()

    def mainloop(self):
        self.root.mainloop()


# ---------------- 自测 ----------------
def run_selftest():
    """模拟截图确认流程：使用自定义副本目录验证主图+副本保存与递增命名"""
    os.makedirs(SAVE_DIR, exist_ok=True)
    test_copy_dir = os.path.join(BASE_DIR, "selftest_copy")
    img = Image.new("RGB", (320, 200), (235, 235, 235))
    d = ImageDraw.Draw(img)
    d.rectangle((15, 15, 305, 185), outline="red", width=4)
    d.line((15, 15, 305, 185), fill="blue", width=3)
    d.text((40, 80), "selftest", fill="black")
    name, main_p, copy_p, copied = save_screenshot(img, test_copy_dir)
    print("SELFTEST NAME:", name)
    print("SELFTEST MAIN:", main_p)
    print("SELFTEST COPY:", copy_p)
    print("SELFTEST COPY_DIR_OK:", os.path.isdir(test_copy_dir))
    print("SELFTEST CLIPBOARD:", copied)
    print("SELFTEST CONFIG_DEFAULT:", load_config())
    print("SELFTEST DONE")


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)
    if "--selftest" in sys.argv:
        run_selftest()
        return
    copy_dir = load_config()
    try:
        os.makedirs(copy_dir, exist_ok=True)
    except Exception:
        pass
    app = MainApp(copy_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
