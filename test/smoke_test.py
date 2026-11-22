"""snapflow 冒烟测试：offscreen 平台下验证非交互路径。"""

import os
import sys
import tempfile
import textwrap
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from snapflow.annotations import (
    ArrowAnnotation, MosaicAnnotation, RectAnnotation, TextAnnotation,
)
from snapflow.backends import candidate_backends
from snapflow.backends.base import CaptureError
from snapflow.plugins import PluginRegistry
from snapflow.ui import CaptureOverlay

app = QApplication([])

failures = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        failures.append(name)


# 1. 候选链：GNOME Wayland 下应为 portal → mss（跳过 grim）
backends = candidate_backends()
names = [b.name for b in backends]
print("backends =", names)
gnome = "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
check("GNOME 下 portal 优先", not gnome or names[0] == "xdg-portal")
check("GNOME 下跳过 grim", not gnome or "wayland-grim" not in names)
shot = None
for backend in backends:
    if backend.name == "xdg-portal":
        continue  # 真实 Portal 调用可能弹授权框，留到本机交互验证
    try:
        shot = backend.capture_fullscreen()
        print("captured with", backend.name)
        break
    except CaptureError as exc:
        print(f"{backend.name} 失败（预期内降级）: {exc}")
if shot is None:
    # Wayland 下 mss 的黑图守卫会拒绝出图，UI 测试改用合成图
    print("真实后端不可用，UI 测试改用合成图")
    shot = QImage(64, 64, QImage.Format.Format_ARGB32)
    shot.fill(QColor("#336699"))
check("后端候选链非空", len(backends) > 0)

# 1b. Portal 后端 mock：模拟 D-Bus 往返返回合法 file:// URI
from snapflow.backends.portal import PortalBackend

mock_png = QImage(32, 24, QImage.Format.Format_ARGB32)
mock_png.fill(QColor("#12ab34"))
with tempfile.TemporaryDirectory() as d:
    # 文件名带空格，验证百分号解码
    png_path = Path(d, "portal shot.png")
    mock_png.save(str(png_path))

    backend = PortalBackend()
    captured_uri = f"file://{str(png_path).replace(' ', '%20')}"

    async def fake_dbus(self, interactive, timeout):
        assert interactive is False and timeout > 0
        return captured_uri

    backend._dbus_screenshot = fake_dbus.__get__(backend)
    got = backend.capture_fullscreen()
    check("portal mock 出图", not got.isNull()
          and got.width() == 32 and got.height() == 24)
    check("portal mock 像素正确",
          QColor(got.pixel(5, 5)) == QColor("#12ab34"))

check("uri 百分号解码",
      PortalBackend._uri_to_path("file:///tmp/a%20b.png") == Path("/tmp/a b.png"))

# 1c. 多屏：按目标屏幕裁剪整块桌面截图
from PyQt6.QtCore import QRect
from snapflow.app import crop_to_screen

wide = QImage(1000, 500, QImage.Format.Format_ARGB32)
wide.fill(QColor("red"))
pw = QPainter(wide)
pw.fillRect(QRect(500, 0, 500, 500), QColor("blue"))
pw.end()
virtual_geo = QRect(0, 0, 800, 400)
left_geo = QRect(0, 0, 400, 400)
right_geo = QRect(400, 0, 400, 400)
right_img = crop_to_screen(wide, right_geo, virtual_geo)
check("多屏裁剪尺寸", right_img.width() == 500 and right_img.height() == 500)
check("多屏裁剪取到右屏像素",
      QColor(right_img.pixel(10, 10)) == QColor("blue"))
check("单屏时不裁剪", crop_to_screen(wide, virtual_geo, virtual_geo)
      .size() == wide.size())

# 2. 覆盖层：构造 + 框选 + 标注（替代旧 selector/editor 测试）
base = QImage(400, 300, QImage.Format.Format_ARGB32)
base.fill(QColor("#336699"))
p = QPainter(base)  # 高反差棋盘，马赛克必须抹掉这种细节
for x in range(200, 280, 2):
    for y in range(100, 180, 2):
        p.fillRect(x, y, 1, 1, QColor("white") if (x + y) % 4 else QColor("black"))
p.end()

overlay = CaptureOverlay(base, PluginRegistry())
check("overlay 构造", overlay._scale > 0)
overlay.show()

# 拖拽框选 -> 应进入编辑模式并显示工具条
QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
QTest.mouseMove(overlay, QPoint(300, 240))
QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(300, 240))
check("框选后进入标注模式", overlay._editing)
check("工具条显示", overlay._toolbar.isVisible())

# 默认矩形工具：选区内拖一个矩形标注
QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(60, 60))
QTest.mouseMove(overlay, QPoint(140, 120))
QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(120, 120))
check("矩形标注入列",
      len(overlay.annotations) == 1
      and isinstance(overlay.annotations[0], RectAnnotation))

# 马赛克工具
overlay._set_tool("mosaic")
QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(200, 100))
QTest.mouseMove(overlay, QPoint(280, 180))
QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(280, 180))
check("马赛克标注入列",
      len(overlay.annotations) == 2
      and isinstance(overlay.annotations[1], MosaicAnnotation))

# 文字标注（走内联 QLineEdit 的提交路径）
overlay._set_tool("text")
overlay._start_text_edit(QPointF(100, 100))
overlay._text_edit.setText("hello")
overlay._commit_text_edit()
check("文字标注入列",
      len(overlay.annotations) == 3
      and isinstance(overlay.annotations[2], TextAnnotation))

# 渲染：尺寸等于选区（图像像素坐标），马赛克抹掉了棋盘细节
out = overlay.render()
sel_img = overlay._selection_image_rect()
check("render 尺寸等于选区",
      out.width() == sel_img.width() and out.height() == sel_img.height())
s = overlay._scale
mosaic_cx = round((240 * s) - sel_img.x())
mosaic_cy = round((140 * s) - sel_img.y())
center = QColor(out.pixel(mosaic_cx, mosaic_cy))
check("马赛克生效", center.name() not in ("#ffffff", "#000000"))

overlay.undo()
check("撤销", len(overlay.annotations) == 2)

# 选区调整：拖右下角手柄放大，标注保留
before = QRectF(overlay._selection)
corner = QPoint(round(before.right()), round(before.bottom()))
QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=corner)
QTest.mouseMove(overlay, QPoint(corner.x() + 40, corner.y() + 30))
QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton,
                   pos=QPoint(corner.x() + 40, corner.y() + 30))
check("拖角调整选区",
      overlay._selection.width() > before.width() + 30
      and overlay._selection.height() > before.height() + 20)
check("调整后标注保留", len(overlay.annotations) == 2)

# 选区外拖拽 = 重新框选（清空旧标注）
QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
QTest.mouseMove(overlay, QPoint(90, 70))
QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(90, 70))
check("框外拖拽重新框选",
      overlay._editing and len(overlay.annotations) == 0
      and overlay._selection.width() > 10)

# Esc 取消路径
cancelled = []
overlay.cancelled.connect(lambda: cancelled.append(1))
QTest.keyClick(overlay, Qt.Key.Key_Escape)
check("Esc 取消", len(cancelled) == 1)

# 3. 插件钩子：加载临时插件目录
with tempfile.TemporaryDirectory() as d:
    Path(d, "double.py").write_text(textwrap.dedent("""\
        from PyQt6.QtGui import QImage
        class P:
            def on_edited(self, image):
                image.fill(0xFFFF0000)
                return image
        PLUGIN = P()
    """))
    reg = PluginRegistry(Path(d))
    check("插件加载", len(reg.plugins) == 1)
    tinted = reg.on_edited(base.copy())
    check("on_edited 钩子生效", QColor(tinted.pixel(0, 0)).red() > 200)

# 4. 热键模块在 Wayland 下应拒绝注册
os.environ["XDG_SESSION_TYPE"] = "wayland"
from snapflow.hotkey import X11Hotkey
try:
    X11Hotkey("Ctrl+Shift+A")
    check("Wayland 热键拒绝", False)
except RuntimeError:
    check("Wayland 热键拒绝", True)

print()
if failures:
    print("FAILED:", failures)
    sys.exit(1)
print("ALL SMOKE TESTS PASSED")
