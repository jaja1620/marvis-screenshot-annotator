# Marvis 截图助手 v2.1

面向 Marvis 用户的常驻桌面截图标注工具（Windows / Python + tkinter + Pillow）。

## v2.1 更新要点

上一版（v2.0）在受企业终端管控的电脑上，自动粘贴只能让对话框里出现一个字母 `v`——因为 `SendInput` / `keybd_event` 发出的按键被安全软件丢弃，而 Chromium 渲染窗口用 `GetKeyState(VK_CONTROL)` 判断修饰键，单纯投递 `WM_KEYDOWN` 消息不会改变该键状态，于是 Ctrl 丢失，只剩下字符 `v`。

v2.1 改为**消息级 Ctrl+V（伪造修饰键状态）**：

1. `AttachThreadInput` 附加到 Marvis 的 UI 线程；
2. `GetKeyboardState` 取出键状态表，把 `VK_CONTROL`(0x11) / `VK_LCONTROL`(0xA2) 置为按下(0x80) 后 `SetKeyboardState` 写回；
3. 向 `Chrome_RenderWidgetHostHWND` 渲染子窗口 `PostMessage` 完整四连击：`WM_KEYDOWN Ctrl` → `WM_KEYDOWN V` → `WM_KEYUP V` → `WM_KEYUP Ctrl`（含扫描码 0x1D / 0x2F）；
4. 恢复原键状态并解除线程附加。

该通道不经过系统模拟按键注入，**不受安全软件拦截影响**，实测图片与文本均可正常粘贴进 Marvis 输入框。自动发送同样改为消息级回车（`PostMessage VK_RETURN`）。

其他改动：

- 粘贴是否生效改用**输入区图像差分**（变化像素占比 > 0.4% 判定），取样范围扩到窗口底部 320px 以覆盖附件缩略图行，避免"其实没粘上"却报成功，也避免误判导致重复粘贴（4 次轮询确认）；
- 通道优先级：消息级 Ctrl+V → 系统键盘注入 → 旧 WM_PASTE 消息，逐级降级；
- 「检测粘贴通道」按钮改为先检测消息级通道，并区分"可用（消息级 Ctrl+V）/ 可用（系统键盘注入）/ 待 Marvis 启动后检测 / 不可用"四种状态；
- 版本号 2.1，自检新增 `forge_channel_available` 项。

## 功能特性

- 常驻 GUI 主面板，启动后一直运行直到手动关闭
- 全局快捷键截图（默认 `Ctrl+Shift+M`，可在面板修改）
- 微信风格框选 + 标注工具栏：矩形、椭圆、箭头、画笔、文字、马赛克，支持撤销、颜色与粗细调整
- **重新选择截图区域**：确认前可清除选区与标注、无限次重新框选
- **多屏幕支持**：截图与遮罩覆盖整个虚拟桌面（含负坐标），跨屏坐标精确映射
- 自动命名：`图0001`、`图0002` 递增，主图与副本双保存，副本目录可在 GUI 自定义
- **连接 Marvis**：截图确认后自动激活 Marvis 窗口、粘贴图片到输入框并回车发送（开关可关闭）
- 历史截图查询：GUI 列表浏览副本、预览大图、复制图片名供会话引用
- 单文件 exe 部署，免 Python 环境，可直接拷贝到其他电脑

## 目录结构

- `screenshot_annotator_v2.1.py` — 主程序源码
- `Marvis截图助手_v2.1.exe` — PyInstaller 单文件打包（29.0 MB）
- `启动截图标注_v2.1.bat` — 源码启动脚本（自动安装依赖）
- 截图主图保存至 `screenshots\`，副本保存至 `MARVIS沟通截图\`（均不入库）

## 使用

双击 `Marvis截图助手_v2.1.exe`（或 `启动截图标注_v2.1.bat`）启动，按 `Ctrl+Shift+M` 框选并标注，确认后会自动粘贴到 Marvis 输入框并回车发送。

若面板显示"自动粘贴通道：不可用"，先打开 Marvis 会话窗口，再点一次「检测粘贴通道」。
