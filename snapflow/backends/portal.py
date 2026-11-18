"""XDG Portal Screenshot 后端（GNOME/KDE Wayland 下唯一真实出图路径）。

协议要点：
- 调用 ``org.freedesktop.portal.Screenshot.Screenshot`` 后立即返回一个
  Request 对象路径，结果通过 ``org.freedesktop.portal.Request.Response``
  信号异步返回（code=0 成功，results["uri"] 为 file:// URI）。
- 为消除竞态，在发起调用前先 AddMatch 订阅 Response 信号。
- 选项策略：先 ``interactive=False`` 静默截全屏；失败后回退
  ``interactive=True``，让桌面环境自带的截图 UI 接管。
  （GNOME 48+ 的 interactive 模式有已知问题，静默模式反而更稳。）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt6.QtGui import QImage

from .base import CaptureCancelled, CaptureError, ScreenBackend

log = logging.getLogger(__name__)

_BUS_NAME = "org.freedesktop.portal.Desktop"
_DESKTOP_PATH = "/org/freedesktop/portal/desktop"
_SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
_REQUEST_IFACE = "org.freedesktop.portal.Request"

INTERACTIVE_FAILED_HINT = (
    "当前桌面环境不支持 Portal 交互式截图，请尝试在 X11 会话下使用，"
    "或手动绑定桌面自带截图工具的快捷键后粘贴到 SnapFlow。"
)

RESPONSE_OK = 0
RESPONSE_CANCELLED = 1


class PortalBackend(ScreenBackend):
    name = "xdg-portal"

    def __init__(self, silent_timeout: float = 120.0,
                 interactive_timeout: float = 300.0) -> None:
        try:
            import dbus_next  # noqa: F401
        except ImportError as exc:
            raise CaptureError(
                "未安装 dbus-next：sudo apt install python3-dbus-next"
            ) from exc
        self._silent_timeout = silent_timeout
        self._interactive_timeout = interactive_timeout

    def capture_fullscreen(self) -> QImage:
        # 此处阻塞的是触发截图的 GUI 线程，但此时尚未打开任何窗口，可接受
        return asyncio.run(self._capture_async())

    async def _capture_async(self) -> QImage:
        try:
            uri = await self._dbus_screenshot(
                interactive=False, timeout=self._silent_timeout)
        except CaptureCancelled:
            raise
        except Exception as exc:
            log.info("Portal 静默截图失败（%s），回退到交互模式", exc)
            try:
                uri = await self._dbus_screenshot(
                    interactive=True, timeout=self._interactive_timeout)
            except CaptureCancelled:
                raise
            except Exception as fallback_exc:
                raise CaptureError(INTERACTIVE_FAILED_HINT) from fallback_exc
        return self._load(uri)

    # ---- D-Bus 往返（mock 测试的替换点） ----

    async def _dbus_screenshot(self, interactive: bool,
                               timeout: float) -> str:
        """发起 Screenshot 请求并等待 Response 信号，返回 file:// URI。"""
        from dbus_next import BusType, Message, MessageType, Variant
        from dbus_next.aio import MessageBus

        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        token = f"snapflow_{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def on_message(msg) -> None:
            if (msg.message_type is MessageType.SIGNAL
                    and msg.interface == _REQUEST_IFACE
                    and msg.member == "Response"
                    and msg.path and msg.path.endswith("/" + token)
                    and not future.done()):
                future.set_result(msg.body)
            return None

        bus.add_message_handler(on_message)
        try:
            # 先订阅 Response 信号，再发起请求，避免竞态丢失响应
            await bus.call(Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[f"type='signal',interface='{_REQUEST_IFACE}',"
                      f"member='Response'"],
            ))
            reply = await bus.call(Message(
                destination=_BUS_NAME,
                path=_DESKTOP_PATH,
                interface=_SCREENSHOT_IFACE,
                member="Screenshot",
                signature="sa{sv}",
                body=["", {  # parent_window 第一版先传空串
                    "handle_token": Variant("s", token),
                    "interactive": Variant("b", interactive),
                    "modal": Variant("b", True),
                }],
            ))
            if reply.message_type is MessageType.ERROR:
                detail = reply.body[0] if reply.body else reply.error_name
                raise CaptureError(f"Portal 调用被拒绝：{detail}")
            code, results = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise CaptureError("等待 Portal 响应超时") from exc
        finally:
            bus.remove_message_handler(on_message)
            bus.disconnect()

        if code == RESPONSE_CANCELLED:
            raise CaptureCancelled("用户取消了截图")
        if code != RESPONSE_OK:
            raise CaptureError(f"Portal 返回错误码 {code}")
        uri = results.get("uri")
        if uri is None:
            raise CaptureError("Portal 响应缺少 uri 字段")
        return getattr(uri, "value", uri)

    # ---- URI 解析与图像加载 ----

    @staticmethod
    def _uri_to_path(uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise CaptureError(f"Portal 返回了非 file URI：{uri}")
        return Path(unquote(parsed.path))

    def _load(self, uri: str) -> QImage:
        path = self._uri_to_path(uri)
        # 可能是 /run/user/$UID/doc/ 下的文档门户 FUSE 路径，读字节最稳妥；
        # 文件由 Portal 管理，无需删除
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CaptureError(f"无法读取截图文件 {path}：{exc}") from exc
        image = QImage.fromData(data)
        if image.isNull():
            raise CaptureError(f"Portal 截图无法解析：{path}")
        return image.convertToFormat(QImage.Format.Format_ARGB32)
