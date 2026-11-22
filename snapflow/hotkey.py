"""X11 全局快捷键：ctypes 直接调 libX11 的 XGrabKey，零第三方依赖。

Wayland 协议不允许客户端自行抓取全局按键，Wayland 下本模块不可用；
请在桌面环境设置里把快捷键绑定到 ``snapflow capture``（见 README）。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import select

from PyQt6.QtCore import QObject, QThread, pyqtSignal

log = logging.getLogger(__name__)

_MOD_MAP = {
    "ctrl": 1 << 2,    # ControlMask
    "control": 1 << 2,
    "shift": 1 << 0,   # ShiftMask
    "alt": 1 << 3,     # Mod1Mask
    "mod1": 1 << 3,
    "super": 1 << 6,   # Mod4Mask
    "meta": 1 << 6,
    "win": 1 << 6,
}


def _load_x11():
    path = ctypes.util.find_library("X11")
    if not path:
        raise RuntimeError("未找到 libX11")
    x11 = ctypes.CDLL(path)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XKeysymToKeycode.restype = ctypes.c_uint8
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XGrabKey.restype = ctypes.c_int
    x11.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
                             ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int]
    x11.XNextEvent.restype = ctypes.c_int
    x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    x11.XPending.restype = ctypes.c_int
    x11.XPending.argtypes = [ctypes.c_void_p]
    x11.XSync.restype = ctypes.c_int
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XConnectionNumber.restype = ctypes.c_int
    x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
    x11.XUngrabKey.restype = ctypes.c_int
    x11.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
                               ctypes.c_ulong]
    x11.XCloseDisplay.restype = ctypes.c_int
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    return x11


class _XEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int),
                ("pad", ctypes.c_ulong * 24)]  # 192 字节足够容纳任何 XEvent


KEY_PRESS = 2
LOCK_MASK = 1 << 1     # CapsLock
MOD2_MASK = 1 << 4     # NumLock
GRAB_MODE_ASYNC = 1
# 锁键会改变 modifier 组合，标准做法是连同 Lock/Mod2 的四种组合一起抓取
_GRAB_EXTRAS = (0, LOCK_MASK, MOD2_MASK, LOCK_MASK | MOD2_MASK)


class _HotkeyWorker(QThread):
    triggered = pyqtSignal()

    def __init__(self, display_ptr: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._display = ctypes.c_void_p(display_ptr)
        self._x11 = _load_x11()
        self._fd = self._x11.XConnectionNumber(self._display)

    def run(self) -> None:
        event = _XEvent()
        while not self.isInterruptionRequested():
            # 事件驱动：X 连接可读才处理，按键在毫秒内响应；
            # 超时只用于周期性检查退出请求，不做忙轮询。
            ready, _, _ = select.select([self._fd], [], [], 0.2)
            if not ready:
                continue
            while not self.isInterruptionRequested() and self._x11.XPending(self._display):
                self._x11.XNextEvent(self._display, ctypes.byref(event))
                if event.type == KEY_PRESS:
                    self.triggered.emit()


class X11Hotkey(QObject):
    """监听一个全局快捷键，触发时发出 ``triggered`` 信号（Qt 队列连接）。"""

    triggered = pyqtSignal()

    def __init__(self, hotkey: str = "Ctrl+Shift+A",
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("无 DISPLAY，X11 热键不可用")
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            raise RuntimeError("Wayland 会话不支持应用级全局热键，请绑定 CLI")

        self._x11 = _load_x11()
        self._display = self._x11.XOpenDisplay(None)
        if not self._display:
            raise RuntimeError("XOpenDisplay 失败")

        modifiers, key_name = self._parse(hotkey)
        keysym = self._x11.XStringToKeysym(key_name.encode())
        if not keysym:
            raise RuntimeError(f"无法识别按键名：{key_name}")
        keycode = self._x11.XKeysymToKeycode(self._display, keysym)
        root = self._x11.XDefaultRootWindow(self._display)
        for extra in _GRAB_EXTRAS:
            self._x11.XGrabKey(self._display, keycode, modifiers | extra,
                               root, True, GRAB_MODE_ASYNC, GRAB_MODE_ASYNC)
        # 请求是缓冲发送的，必须 Sync 后才能确保抓取在服务器端生效
        self._x11.XSync(self._display, False)
        self._keycode = keycode
        self._modifiers = modifiers
        log.info("已注册全局热键 %s（keycode=%d）", hotkey, keycode)

        self._worker = _HotkeyWorker(self._display, self)
        self._worker.triggered.connect(self.triggered)
        self._worker.start()

    @staticmethod
    def _parse(hotkey: str) -> tuple[int, str]:
        parts = [p.strip() for p in hotkey.split("+") if p.strip()]
        if not parts:
            raise RuntimeError("热键不能为空")
        modifiers = 0
        for part in parts[:-1]:
            mask = _MOD_MAP.get(part.lower())
            if mask is None:
                raise RuntimeError(f"无法识别修饰键：{part}")
            modifiers |= mask
        return modifiers, parts[-1]

    def stop(self) -> None:
        self._worker.requestInterruption()
        self._worker.wait(1000)
        if self._display:
            root = self._x11.XDefaultRootWindow(self._display)
            for extra in _GRAB_EXTRAS:
                self._x11.XUngrabKey(self._display, self._keycode,
                                     self._modifiers | extra, root)
            self._x11.XSync(self._display, False)
            self._x11.XCloseDisplay(self._display)
            self._display = None
