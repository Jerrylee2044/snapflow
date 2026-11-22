"""主控制器：串起 截图 -> 框选标注 的流程（单层覆盖层，无独立编辑窗口）。"""

from __future__ import annotations

import logging
import sys
import time
import atexit

from PyQt6.QtCore import QEventLoop, QObject, QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QCursor, QGuiApplication, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from .backends import candidate_backends
from .backends.base import CaptureCancelled, CaptureError, ScreenBackend
from . import config
from .plugins import PluginRegistry
from .ui import CaptureOverlay

log = logging.getLogger(__name__)


def _noop() -> None:
    return None


def screen_for_physical_point(screens, point) -> str | None:
    """把 X11/XWayland 的物理像素坐标映射到 Qt 屏幕名。

    ``screens`` 为 ``[(name, x, y, w, h, dpr), ...]``（Qt 逻辑坐标）。
    物理布局按水平排列逐屏累加推算（常见多屏布局），混合 DPI 也能对应上。
    """
    ordered = sorted(screens, key=lambda s: (s[1], s[2]))
    phys_x = 0.0
    for name, x, y, w, h, dpr in ordered:
        rect = QRect(round(phys_x), round(y * dpr),
                     round(w * dpr), round(h * dpr))
        if rect.contains(point):
            return name
        phys_x += w * dpr
    return None


def xdotool_pointer_screen() -> str | None:
    """经 XWayland 读取全局鼠标物理坐标，返回所在屏幕名。"""
    import shutil
    import subprocess

    if shutil.which("xdotool") is None:
        return None
    try:
        proc = subprocess.run(
            ["xdotool", "getmouselocation", "--shell"],
            capture_output=True, text=True, timeout=1)
        if proc.returncode != 0:
            return None
        values = dict(
            line.split("=", 1) for line in proc.stdout.splitlines()
            if "=" in line)
        point = QPoint(int(values["X"]), int(values["Y"]))
    except Exception:  # noqa: BLE001 - 环境不支持就退回其它方式
        return None
    screens = [
        (s.name(), s.geometry().x(), s.geometry().y(),
         s.geometry().width(), s.geometry().height(), s.devicePixelRatio())
        for s in QGuiApplication.screens()
    ]
    return screen_for_physical_point(screens, point)


def crop_to_screen(image: QImage, screen_geometry: QRect,
                   virtual_geometry: QRect) -> QImage:
    """把整块虚拟桌面的截图裁成指定屏幕的区域。

    截图后端返回的是全部显示器合并后的图像，尺寸与逻辑坐标未必 1:1
    （GNOME 下实测为统一缩放），因此按图像/虚拟桌面的比例换算。
    """
    if virtual_geometry.width() <= 0 or virtual_geometry.height() <= 0:
        return image
    sx = image.width() / virtual_geometry.width()
    sy = image.height() / virtual_geometry.height()
    rect = QRect(
        round((screen_geometry.x() - virtual_geometry.x()) * sx),
        round((screen_geometry.y() - virtual_geometry.y()) * sy),
        round(screen_geometry.width() * sx),
        round(screen_geometry.height() * sy),
    ).intersected(image.rect())
    if rect.isEmpty():
        return image
    if rect == image.rect():
        return image
    return image.copy(rect)


class SnapflowApp(QObject):
    """一次截图流程：冻结全屏 -> 覆盖层框选标注 -> 复制/保存。

    ``one_shot=True`` 时流程结束（或取消）即退出进程，供 CLI/快捷键绑定使用；
    否则常驻，等待下一次触发。
    """

    def __init__(self, one_shot: bool = False) -> None:
        super().__init__()
        self.one_shot = one_shot
        self._busy = False
        self._overlay: CaptureOverlay | None = None
        self._portal_permission: bool | None = None
        self._pending_restore = None
        self._config = config.load()
        atexit.register(self._restore_pending_animations)

        self._backends: list[ScreenBackend] = candidate_backends()
        log.info("截图后端候选：%s",
                 ", ".join(b.name for b in self._backends))
        self.plugins = PluginRegistry()

    # ---- 流程 ----

    def capture(self) -> None:
        if self._busy:
            log.debug("已有截图流程进行中，忽略本次触发")
            return
        self._busy = True
        started = time.monotonic()
        screen = self._target_screen()
        log.info("目标屏幕：%s %s", screen.name(), screen.geometry())
        restore_animations = self._suppress_animations()
        helper = self._focus_helper_for_portal()
        shot = self._grab()
        if helper is not None:
            helper.close()
        if shot is None:
            restore_animations()
            return
        virtual = QGuiApplication.primaryScreen().virtualGeometry()
        shot = crop_to_screen(shot, screen.geometry(), virtual)
        log.info("抓屏完成：%.0f ms（%s）",
                 (time.monotonic() - started) * 1000, screen.name())
        shot = self.plugins.on_captured(shot)
        self._open_overlay(shot, screen)
        # 等覆盖层完成映射后再恢复动画，避免窗口打开动画
        QTimer.singleShot(150, restore_animations)

    @staticmethod
    def _target_screen():
        """鼠标所在屏幕；取不到时退回主屏。"""
        name = xdotool_pointer_screen()
        if name is not None:
            for screen in QGuiApplication.screens():
                if screen.name() == name:
                    return screen
        pos = QCursor.pos()
        if pos != QPoint(0, 0):
            screen = QGuiApplication.screenAt(pos)
            if screen is not None:
                return screen
        return QGuiApplication.primaryScreen()

    # ---- 快门闪光抑制 ----

    def _suppress_animations(self):
        """截图瞬间临时关闭 GNOME 系统动画，返回恢复函数。

        GNOME 的快门闪光（Flashspot）与窗口打开动画都基于 St 的 ease，
        enable-animations=false 时 St 会把动画时长归零，闪光与缩放动画
        随之跳过。截图完成后恢复原值；即使进程异常也有 atexit 兜底。
        """
        if not self._config.get("suppress_shutter_flash", True):
            return _noop
        import os
        if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
            return _noop
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            settings = Gio.Settings.new("org.gnome.desktop.interface")
            previous = settings.get_boolean("enable-animations")
            if not previous:
                return _noop
            settings.set_boolean("enable-animations", False)
            Gio.Settings.sync()
        except Exception:  # noqa: BLE001 - 非 GNOME 或 gi 缺失时静默跳过
            log.debug("无法临时关闭系统动画", exc_info=True)
            return _noop

        restored = False

        def restore() -> None:
            nonlocal restored
            if restored:
                return
            restored = True
            self._pending_restore = None
            try:
                settings.set_boolean("enable-animations", previous)
                Gio.Settings.sync()
            except Exception:  # noqa: BLE001
                log.warning("恢复系统动画设置失败", exc_info=True)

        self._pending_restore = restore
        return restore

    def _restore_pending_animations(self) -> None:
        if self._pending_restore is not None:
            self._pending_restore()

    def _focus_helper_for_portal(self) -> QWidget | None:
        """Portal 要求调用方持有焦点窗口，否则 GNOME 无法弹授权框。

        由快捷键启动时没有前台窗口，先显示一个 1x1 透明窗口取得焦点，
        截图完成后关闭。授权已缓存时不需要该窗口，直接跳过以省掉等待。
        """
        import os
        if not os.environ.get("WAYLAND_DISPLAY"):
            return None
        if not self._backends or self._backends[0].name != "xdg-portal":
            return None
        if self._portal_permission_cached():
            return None
        helper = QWidget(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        helper.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        helper.resize(1, 1)
        helper.move(0, 0)
        helper.show()
        helper.raise_()
        helper.activateWindow()
        handle = helper.windowHandle()
        if handle is not None and not helper.isActiveWindow():
            loop = QEventLoop()
            handle.activeChanged.connect(loop.quit)
            QTimer.singleShot(800, loop.quit)
            loop.exec()  # 最多等 800ms，超时也继续
        return helper

    def _grab(self) -> QImage | None:
        """按候选链抓屏，运行期失败自动降级到下一个后端。"""
        errors = []
        while self._backends:
            backend = self._backends[0]
            try:
                return backend.capture_fullscreen()
            except CaptureCancelled:
                log.info("用户取消了截图")
                self._busy = False
                self._maybe_quit()
                return None
            except CaptureError as exc:
                log.warning("后端 %s 失败：%s", backend.name, exc)
                errors.append(f"{backend.name}: {exc}")
                self._backends.pop(0)
                if self._backends:
                    log.info("降级到后端 %s", self._backends[0].name)
        self._busy = False
        QMessageBox.critical(None, "snapflow",
                             "截图失败：\n" + "\n".join(errors))
        self._maybe_quit()
        return None

    def _open_overlay(self, shot: QImage, screen) -> None:
        overlay = CaptureOverlay(shot, self.plugins, screen=screen)
        overlay.finished.connect(self._reset)
        overlay.cancelled.connect(self._reset)
        overlay.destroyed.connect(self._clear_overlay)
        self._overlay = overlay
        overlay.showFullScreen()
        overlay.raise_()
        overlay.activateWindow()
        overlay.setFocus()

    # ---- 状态 ----

    def _reset(self) -> None:
        self._busy = False
        self._maybe_quit()

    def _clear_overlay(self) -> None:
        self._overlay = None

    def _maybe_quit(self) -> None:
        if self.one_shot:
            QApplication.quit()

    # ---- Portal 权限缓存 ----

    def _portal_permission_cached(self) -> bool:
        """查询权限存储：snapflow 是否已被授予截图权限（结果进程内缓存）。"""
        if self._portal_permission is not None:
            return self._portal_permission
        try:
            import asyncio

            async def lookup() -> bool:
                from dbus_next import BusType, Message, MessageType
                from dbus_next.aio import MessageBus

                bus = await MessageBus(bus_type=BusType.SESSION).connect()
                try:
                    reply = await bus.call(Message(
                        destination="org.freedesktop.impl.portal.PermissionStore",
                        path="/org/freedesktop/impl/portal/PermissionStore",
                        interface="org.freedesktop.impl.portal.PermissionStore",
                        member="Lookup", signature="ss",
                        body=["screenshot", "screenshot"]))
                finally:
                    bus.disconnect()
                if reply.message_type is not MessageType.METHOD_RETURN:
                    return False
                permissions = reply.body[0] if reply.body else {}
                return "yes" in permissions.get("snapflow", [])

            self._portal_permission = asyncio.run(lookup())
        except Exception:  # noqa: BLE001 - 查询失败就按"未授权"处理
            log.debug("查询 Portal 权限失败", exc_info=True)
            self._portal_permission = False
        log.info("Portal 截图授权已缓存：%s", self._portal_permission)
        return self._portal_permission


def create_qapp() -> QApplication:
    app = QApplication(sys.argv)
    app.setApplicationName("snapflow")
    app.setQuitOnLastWindowClosed(False)
    return app
