from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtGui import QImage

from .base import CaptureError, ScreenBackend


class GrimWaylandBackend(ScreenBackend):
    """Wayland 截图后端，调用 grim（wlroots 系合成器自带支持）。"""

    name = "wayland-grim"

    def __init__(self) -> None:
        if shutil.which("grim") is None:
            raise CaptureError("未找到 grim，请先安装：sudo apt install grim")

    def capture_fullscreen(self) -> QImage:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = Path(tmp.name)
        try:
            proc = subprocess.run(
                ["grim", str(path)],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode != 0:
                raise CaptureError(f"grim 截图失败：{proc.stderr.strip() or '未知错误'}")
            image = QImage(str(path))
            if image.isNull():
                raise CaptureError("grim 输出的图像无法解析")
            return image.convertToFormat(QImage.Format.Format_ARGB32)
        finally:
            path.unlink(missing_ok=True)
