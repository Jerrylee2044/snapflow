from __future__ import annotations

from PyQt6.QtGui import QImage

from .base import CaptureError, ScreenBackend


class MssX11Backend(ScreenBackend):
    """X11 截图后端，基于 mss。"""

    name = "x11-mss"

    def capture_fullscreen(self) -> QImage:
        try:
            import mss
        except ImportError as exc:  # pragma: no cover
            raise CaptureError("未安装 mss：pip install mss") from exc

        try:
            with mss.mss() as sct:
                # monitors[0] 是所有显示器合并后的虚拟屏幕
                raw = sct.grab(sct.monitors[0])
        except Exception as exc:  # noqa: BLE001 - mss 抛出的异常类型不固定
            raise CaptureError(f"mss 截图失败：{exc}") from exc

        # mss 返回 BGRA；构造零拷贝视图后转为 ARGB32 副本
        view = QImage(raw.bgra, raw.width, raw.height, raw.width * 4,
                      QImage.Format.Format_ARGB32)
        image = view.copy()
        self._reject_xwayland_black(image)
        return image

    @staticmethod
    def _reject_xwayland_black(image: QImage) -> None:
        """Wayland 会话下 mss 经 XWayland 只能抓到纯黑图，视为失败。"""
        import os
        if not os.environ.get("WAYLAND_DISPLAY"):
            return
        w, h = image.width(), image.height()
        for i in range(8):
            for j in range(8):
                if image.pixel((2 * i + 1) * w // 16,
                               (2 * j + 1) * h // 16) != 0xFF000000:
                    return
        raise CaptureError("mss 经 XWayland 只能抓到黑图")
