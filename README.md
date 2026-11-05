---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 798659b1f59514cb753b383c1f423b09_75fc7ca6d8c611f1a0ca525400de85a5
    ReservedCode1: 03c5Wv2mS1hrMwMnp70QItKgCFjPKjUpTpZW2DancVnmH3MbfpIImbNUy+AQYNwcZ0Rk+Z1JMvEN+41mWnhDFqguqS5U4eKXDfXO2FM/o1tpJARvtIIbz5HB+Bb9tMNhAZoOHVorJyoQ5B0nyPmxxWI0bzo+eVsZYXjcWQNkHbGyHRNoOX4cTnQPhk4=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 798659b1f59514cb753b383c1f423b09_75fc7ca6d8c611f1a0ca525400de85a5
    ReservedCode2: 03c5Wv2mS1hrMwMnp70QItKgCFjPKjUpTpZW2DancVnmH3MbfpIImbNUy+AQYNwcZ0Rk+Z1JMvEN+41mWnhDFqguqS5U4eKXDfXO2FM/o1tpJARvtIIbz5HB+Bb9tMNhAZoOHVorJyoQ5B0nyPmxxWI0bzo+eVsZYXjcWQNkHbGyHRNoOX4cTnQPhk4=
---

# Marvis 截图助手 v2.0

面向 Marvis 用户的常驻桌面截图标注工具（Windows / Python + tkinter + Pillow）。

## 版本历史

| 版本 | 内容 | 备注 |
| --- | --- | --- |
| 1.5 | 两段式截图（框选 → 预览编辑窗口标注）+ 标注工具栏 + 递增命名 + 剪贴板 CF_DIB + 自动粘贴（键盘注入） | 已备份至 `releases\v1.5\`，git tag `v1.5`，作为回滚基准 |
| 2.0 | 恢复「连接 Marvis」按钮；新增粘贴通道自动检测与消息级备用通道；版本号与标题统一为 v2.0 | 当前版本，git tag `v2.0` |

## 功能特性

- 常驻 GUI 主面板，启动后一直运行直到手动关闭
- 全局快捷键截图（默认 `Ctrl+Shift+M`，可在面板修改）
- 两段式截图：全屏遮罩仅用于框选（框选即关闭），随后弹出「截图预览与标注」独立窗口
- 标注工具栏：矩形、椭圆、箭头、画笔、文字、马赛克，撤销、7 种预设色、取色器、1/3/6 粗细
- **重选**：回到框选阶段重新框选（基于已截取的全屏图像，不重新截图）
- **多屏幕支持**：截图与遮罩覆盖整个虚拟桌面（含负坐标），缩放与坐标换算精确
- 自动命名：`图0001`、`图0002` 递增，主图与副本双保存，副本目录可在 GUI 自定义
- **连接 Marvis**（v2.0 恢复）：显示副本目录绝对路径 + 复制路径 + 使用说明，换电脑部署时用它让 Marvis 重新登记截图目录
- **粘贴通道检测**（v2.0 新增）：一键检测本机是否允许模拟按键，明确显示"可用 / 被系统拦截"
- 历史截图查询：GUI 列表浏览、双击预览、删除选中
- 单文件 exe 部署，免 Python 环境，可直接拷贝到其他电脑

## 自动粘贴的已知限制（重要）

自动粘贴到 Marvis 会话框依赖**系统级模拟按键（Ctrl+V / 回车）**。本机装有奇安信天擎、金山安全终端、终端入网认证类管控组件，实测会拦截键盘注入：

- 探针验证：自建探针窗口通过 `SendInput` 发送按键后**收不到** `WM_KEYDOWN`，判定拦截
- 记事本验证：`SendInput`/`keybd_event` 打字后 Edit 控件读回为空，而直接投递 `WM_CHAR` 可正常写入
- 鼠标注入未被拦截（`mouse_event` 拖动窗口有效），Marvis 窗口可正常激活并获得焦点
- 对 Chromium 渲染窗口投递消息级 `WM_PASTE` / `WM_KEYDOWN(Ctrl+V)` 无效（Electron 不处理该消息）
- Marvis 的 UI Automation 树未暴露可写的输入框控件，无法通过 UIA 写入

因此在本机，**自动粘贴与自动发送不可用**，截图后的稳定流程是：

1. 确认发送 → 图片已保存并复制到剪贴板
2. 切到 Marvis 输入框，手动 `Ctrl+V`，回车发送

若要恢复自动粘贴，可尝试（任一）：把本程序目录加入安全软件白名单 / 以管理员身份运行本程序；改完后在主面板点「检测粘贴通道」复验。

## 目录结构

- `screenshot_annotator.py` — 主程序源码
- `Marvis截图助手.exe` — PyInstaller 单文件打包（29 MB）
- `启动截图标注.bat` — 源码启动脚本（自动安装依赖）
- `releases\v1.5\` — 1.5 基准版本备份（源码 + exe + 文档）
- 截图主图保存至 `screenshots\`，副本保存至 `MARVIS沟通截图\`（均不入库）

## 使用

双击 `Marvis截图助手.exe`（或 `启动截图标注.bat`）启动，按 `Ctrl+Shift+M` 截图框选标注，确认后图片进入剪贴板，到 Marvis 输入框 `Ctrl+V` 并回车发送。

自检：`Marvis截图助手.exe --selftest` 或 `python screenshot_annotator.py --selftest`，结果写入 `selftest_result.txt`。
*（内容由AI生成，仅供参考）*
