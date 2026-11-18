from __future__ import annotations

from abc import ABC, abstractmethod

from PyQt6.QtGui import QImage


class CaptureError(RuntimeError):
    pass


class CaptureCancelled(CaptureError):
    """用户在系统截图 UI 中主动取消。调用方不应再降级到其他后端。"""


class ScreenBackend(ABC):
    """截图后端：负责抓取整个虚拟屏幕（多屏合并）。"""

    name: str = "abstract"

    @abstractmethod
    def capture_fullscreen(self) -> QImage:
        """返回覆盖所有屏幕的 QImage（Format_ARGB32 / Format_RGB32）。"""
        raise NotImplementedError
