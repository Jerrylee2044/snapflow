"""主控制器：串起 截图 -> 框选标注 的流程（单层覆盖层，无独立编辑窗口）。"""

from __future__ import annotations

import logging
import sys
import time

from PyQt6.QtCore import QEventLoop, QObject, Qt, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from .backends import candidate_backends
from .backends.base import CaptureCancelled, CaptureError, ScreenBackend
from .plugins import PluginRegistry
from .ui import CaptureOverlay

log = logging.getLogger(__name__)


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
        helper = self._focus_helper_for_portal()
        shot = self._grab()
        if helper is not None:
            helper.close()
        if shot is None:
            return
        log.info("抓屏完成：%.0f ms", (time.monotonic() - started) * 1000)
        shot = self.plugins.on_captured(shot)
        self._open_overlay(shot)

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

    def _open_overlay(self, shot: QImage) -> None:
        overlay = CaptureOverlay(shot, self.plugins)
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
