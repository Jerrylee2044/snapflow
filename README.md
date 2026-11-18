# snapflow

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/Jerrylee2044/snapflow)](https://github.com/Jerrylee2044/snapflow/releases)

Ubuntu 桌面截图工具：**X11 优先（mss），Wayland 经 XDG Portal / grim 兜底**。

微信式单层交互——拖拽框选后工具条直接出现在选区旁，矩形、箭头、文字、马赛克
标注都在同一个覆盖层内完成，没有独立的编辑窗口。回车即复制，无需再多点一步。

```
Alt+A（可自定义） → 拖拽框选 → 标注 → Enter 复制 / Ctrl+Enter 保存
```

## 特性

- **单层交互**：框选与标注一体，选区旁内联工具条（矩形 / 箭头 / 文字 / 马赛克，
  6 色预选，撤销），不打断视线、没有中间窗口
- **Enter 复制到剪贴板**：wl-copy（Wayland）/ xclip（X11）/ Qt 三级兜底，
  主程序退出后剪贴板内容仍然可用
- **Ctrl+Enter 直接保存**：无弹窗，按时间戳落到 `~/Pictures/截图/`
- **Wayland 可用**：GNOME/KDE 经 XDG Portal（先静默、失败自动回退交互模式），
  wlroots 系合成器（sway / Hyprland）用 grim，X11 用 mss，运行期失败自动降级
- **全局快捷键**：X11 内置热键守护（零额外依赖的 XGrabKey 实现）；
  Wayland 绑定桌面自定义快捷键（README 附 gsettings 一键配置）
- **插件接口**：`on_captured` / `on_edited` / `before_save` 三个钩子，
  为 OCR、自动打码等能力预留，单个插件出错自动隔离

## 安装

### deb 包（推荐）

从 [Releases](https://github.com/Jerrylee2044/snapflow/releases) 下载
`snapflow_x.y.z-1_all.deb`：

```bash
sudo apt install ./snapflow_0.1.1-1_all.deb   # 自动处理依赖
```

安装后 `snapflow` 命令可用，应用菜单中也能找到 Snapflow 图标。

### 从源码运行

```bash
sudo apt install python3-pyqt6 python3-dbus-next python3-pip grim wl-clipboard xclip
pip install --user mss        # mss 不在 apt 源中；遇 PEP 668 报错加 --break-system-packages

git clone https://github.com/Jerrylee2044/snapflow.git && cd snapflow
python3 main.py capture       # 立即截一次
```

## 使用

```bash
snapflow trigger              # 通知常驻 daemon 截图（桌面快捷键绑定这个，响应最快）
snapflow daemon               # 常驻：本地 socket + X11 全局热键
snapflow daemon --hotkey Print  # X11 换成 PrintScreen
snapflow capture              # 立即截一次（进程内完成，适合测试/无 daemon 环境）
python3 -m snapflow capture   # 源码方式运行
```

安装 deb 后，登录时会通过 `/etc/xdg/autostart/snapflow.desktop` 自动拉起
`snapflow daemon`。快捷键只发一条本地 socket 消息（~1ms），截图立即开始，
省掉每次冷启动 Python/Qt 的 300–500ms。

### 操作与快捷键

| 操作 | 说明 |
| --- | --- |
| 拖拽 | 框选截图区域，松手后选区旁出现工具条 |
| 工具条 | 矩形 / 箭头 / 文字 / 马赛克，6 色预选，撤销、保存、完成 |
| 文字工具 | 点击选区后直接输入，回车确认 |
| **Enter** | 复制到剪贴板并退出 |
| **Ctrl+Enter** | 直接保存到 `~/Pictures/截图/` 并退出（无弹窗） |
| Ctrl+S | 同 Ctrl+Enter |
| Ctrl+Z | 撤销上一笔标注 |
| 工具条"完成" / "保存" | 等同 Enter / Ctrl+Enter |
| Esc | 取消截图 |

### 绑定全局快捷键

**GNOME（Wayland 推荐）**，把 `Alt+A` 绑定到 `snapflow trigger`：

```bash
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/']"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/ \
  name 'Snapflow 截图'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/ \
  command 'snapflow trigger'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/ \
  binding '<Alt>a'
```

也可以走 GUI：设置 → 键盘 → 查看及自定义快捷键 → 自定义快捷键。
KDE：系统设置 → 快捷键 → 自定义快捷键。

**X11** 用内置守护即可：`snapflow daemon --hotkey "Alt+A"`。

## Wayland 支持

后端按 **portal → grim → mss** 顺序组成候选链，运行期失败自动降级：

| 后端 | 适用场景 | 行为 |
| --- | --- | --- |
| `xdg-portal` | GNOME / KDE Wayland | 先 `interactive: false` 静默截全屏，失败回退系统截图 UI |
| `grim` | sway / Hyprland 等 wlroots 合成器 | 静默、无弹窗；GNOME 桌面自动跳过 |
| `mss` | X11 原生 | Wayland 下经 XWayland 只能抓到黑图，会被黑图守卫拒绝 |

无论走哪个后端，拿到全屏图后一律进入 snapflow 自己的框选层。

### 关于 GNOME 的截图快门闪光

在 GNOME Wayland 下按快捷键后，屏幕会闪一下才出现选框——这是 GNOME 的
**截图快门动画**，不是 snapflow 造成的，也无法通过 API 关闭：
`xdg-desktop-portal-gnome` 调用 shell 截图时把 flash 参数硬编码为 `TRUE`
（[screenshotdialog.c](https://github.com/GNOME/xdg-desktop-portal-gnome/blob/main/src/screenshotdialog.c)），
Portal 协议也没有对应的选项。

snapflow 能做的是把闪光前后的等待压到最短：常驻 daemon 让触发只需一条
本地消息，授权缓存后跳过焦点窗口等待，实测按 `Alt+A` 到可框选约 0.5–0.6 秒
（其中绝大部分是 GNOME Portal 自身的截图耗时）。
如果你需要真正零闪烁、零延迟的截图，X11 会话下的 mss 后端是首选。

### GNOME 首次使用的授权弹窗

GNOME 会把快捷键启动的 snapflow 识别为独立 app_id `snapflow`。首次使用时
可能弹出"允许截图？"授权框，**这是桌面环境的正常安全行为，不是 snapflow 的缺陷**，
点允许一次即可，之后静默截图。

快捷键启动的进程没有前台窗口，GNOME 会拒绝展示授权框（
`Only the focused app is allowed to show a system access dialog`）。
snapflow 会先显示一个 1×1 透明窗口取得焦点，保证授权框能弹出。
也可以直接预授予权限：

```bash
# 查看授权（screenshot 表）
gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore \
  --object-path /org/freedesktop/impl/portal/PermissionStore \
  --method org.freedesktop.impl.portal.PermissionStore.Lookup screenshot screenshot
```

GNOME 48+ 的 Portal `interactive: true` 存在已知问题（xdg-desktop-portal-gnome
可能直接失败），"先静默后交互"的顺序正是为此设计。两种模式都失败时会给出
明确提示，而不是弹出黑屏。

## 插件接口

在 `~/.config/snapflow/plugins/` 放一个 `.py` 文件，定义名为 `PLUGIN` 的对象，
实现以下任意钩子（全部可选）：

```python
class MyPlugin:
    def on_captured(self, image):        # 全屏截图后，可返回替换图像
        return image

    def on_edited(self, image):          # 编辑完成后（复制/保存前）
        return image                     # OCR / 自动打码挂这里

    def before_save(self, image, path):  # 写盘前通知
        pass

PLUGIN = MyPlugin()
```

所有钩子在 GUI 线程同步调用，耗时操作请自行转后台；单个插件异常会被隔离。
示例见 `examples/plugins/mirror_save.py`。

## 架构

```
snapflow/
├── backends/          # 截图后端：portal.py（dbus-next）、wayland_grim.py、x11_mss.py
│   └── __init__.py    # 候选链与降级策略
├── ui/overlay.py      # 全屏覆盖层：框选 + 内联工具条 + 标注渲染
├── annotations.py     # 矩形 / 箭头 / 文字 / 马赛克的图元与绘制
├── app.py             # 流程控制器：抓屏 → 覆盖层 → 复制/保存
├── clipboard.py       # wl-copy / xclip / Qt 三级兜底
├── hotkey.py          # X11 全局热键（ctypes 直调 XGrabKey）
├── ipc.py             # 常驻 daemon 的本地 socket 触发器（快捷键 → daemon）
├── plugins/           # 插件协议与注册表
└── __main__.py        # CLI：capture / daemon
```

设计取舍：

- 截图与标注同层完成，避免窗口切换导致的焦点与光标状态问题
- 标注统一存图像像素坐标，HiDPI 下按缩放系数换算，输出始终是原分辨率
- Portal 调用前先 AddMatch 订阅 `Response` 信号，再发请求，消除竞态
- 后端失败即降级，但用户主动取消（Portal code=1）不降级，避免取消失败后
  又弹出黑屏

## 构建 deb

```bash
./scripts/build-deb.sh        # 输出 dist/snapflow_<version>-1_all.deb
```

脚本自动 vendor mss（apt 源中没有该包）、生成图标与桌面文件，
重新构建时记得同步 `snapflow/__init__.py` 中的版本号。

## 开发

```bash
QT_QPA_PLATFORM=offscreen python3 test/smoke_test.py
```

冒烟测试覆盖：候选链顺序、Portal mock（请求路径 / Response 信号 /
file:// URI 解码）、覆盖层的框选与三类标注、马赛克像素验证、插件钩子、
Wayland 热键拒绝。

## 已知限制

- 文字标注暂不支持选中后二次编辑，Ctrl+Z 可回退
- Wayland 框选层只覆盖主屏（Qt/Wayland 不允许自由定位窗口）；X11 多屏正常
- 无 OCR、无自动打码（插件接口已预留）
- Wayland 下应用无法自行注册全局热键，需桌面环境绑定

## 许可

[MIT](LICENSE)。打包内置的 [mss](https://github.com/BoboTiG/python-mss) 同为 MIT。
