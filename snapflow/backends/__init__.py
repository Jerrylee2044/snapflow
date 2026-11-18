from __future__ import annotations

import logging
import os
import shutil

from .base import CaptureError, ScreenBackend

log = logging.getLogger(__name__)


def candidate_backends() -> list[ScreenBackend]:
    """按优先级返回可用后端列表。

    Wayland：portal → grim → mss。GNOME/KDE 的合成器不支持 grim 依赖的
    wlr-screencopy 协议，GNOME 桌面下直接跳过 grim 以免无意义的报错和延迟。
    调用方应按序尝试：某个后端运行期失败时降级到下一个。
    """
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or session == "wayland"
    x11 = bool(os.environ.get("DISPLAY"))
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    gnome = "gnome" in desktop
    backends: list[ScreenBackend] = []

    def add_portal() -> None:
        from .portal import PortalBackend
        try:
            backends.append(PortalBackend())
        except CaptureError as exc:
            log.info("Portal 后端不可用：%s", exc)

    def add_grim() -> None:
        if shutil.which("grim"):
            from .wayland_grim import GrimWaylandBackend
            try:
                backends.append(GrimWaylandBackend())
            except CaptureError:
                pass

    def add_mss() -> None:
        from .x11_mss import MssX11Backend
        backends.append(MssX11Backend())

    if wayland:
        add_portal()
        if not gnome:
            add_grim()
        if x11:
            add_mss()       # 兜底：经 XWayland（GNOME Wayland 下只出黑图）
    elif x11:
        add_mss()

    if not backends:
        raise CaptureError("无法确定截图后端：既无 DISPLAY 也无 WAYLAND_DISPLAY")
    return backends


def detect_backend() -> ScreenBackend:
    """返回首选后端（兼容旧接口）。"""
    return candidate_backends()[0]
