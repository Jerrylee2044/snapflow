"""本地触发器客户端：纯 socket 实现，不导入 Qt，保证快捷键响应最快。

服务端见 ``trigger_server.py``（QLocalServer 在 Unix 上就是普通
Unix domain socket，纯 socket 客户端可以直接连接）。
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

log = logging.getLogger(__name__)

MESSAGE = b"capture"


def socket_path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return str(Path(base) / f"snapflow-{os.getuid()}.sock")


def send_trigger(timeout: float = 0.3) -> bool:
    """通知 daemon 截图；daemon 不在线时返回 False（调用方走一次性流程）。"""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path())
            sock.sendall(MESSAGE)
            try:
                sock.recv(8)
            except OSError:
                pass
            return True
    except OSError:
        return False
