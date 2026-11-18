from __future__ import annotations

import logging
import os
import shutil
import subprocess

from PyQt6.QtGui import QGuiApplication, QImage

log = logging.getLogger(__name__)


def copy_image(image: QImage) -> bool:
    """把图像复制到系统剪贴板。

    Wayland 下优先用 wl-copy（进程驻留持有数据，主程序退出后仍可粘贴）；
    X11 下优先用 xclip；都不可用则退回 Qt 剪贴板。
    """
    on_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))

    if on_wayland and shutil.which("wl-copy"):
        if _pipe_copy(["wl-copy", "-t", "image/png"], image):
            return True
    if not on_wayland and shutil.which("xclip"):
        if _pipe_copy(["xclip", "-selection", "clipboard",
                       "-t", "image/png", "-i"], image):
            return True

    # 兜底：Qt 剪贴板（程序退出后内容可能丢失，取决于桌面是否有剪贴板管理器）
    QGuiApplication.clipboard().setImage(image)
    log.info("已使用 Qt 剪贴板兜底复制")
    return True


def _pipe_copy(cmd: list[str], image: QImage) -> bool:
    from PyQt6.QtCore import QBuffer, QIODevice

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buf, "PNG"):
        return False
    try:
        proc = subprocess.Popen(  # noqa: S603 - 固定命令行
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.communicate(bytes(buf.data()), timeout=5)
        # wl-copy/xclip 会 fork 到后台持有数据，立即返回属正常
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        log.exception("调用 %s 复制失败", cmd[0])
        return False
