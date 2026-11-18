"""命令行入口。

用法：
    python -m snapflow capture            # 立即截一次（适合绑定桌面快捷键）
    python -m snapflow daemon [--hotkey Ctrl+Shift+A]
                                          # 常驻并监听全局热键（仅 X11）
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from .app import SnapflowApp, create_qapp
from .backends.base import CaptureError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snapflow", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("capture", help="立即截取一次屏幕区域")

    daemon = sub.add_parser("daemon", help="常驻并监听全局热键（仅 X11）")
    daemon.add_argument("--hotkey", default="Ctrl+Shift+A",
                        help="全局热键，如 'Ctrl+Shift+A' 或 'Print'")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = create_qapp()

    try:
        snap = SnapflowApp(one_shot=(args.command != "daemon"))
    except CaptureError as exc:
        print(f"snapflow: {exc}", file=sys.stderr)
        return 2

    if args.command == "daemon":
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            print(
                "Wayland 会话不支持应用级全局热键。\n"
                "请在桌面环境设置中把快捷键绑定到命令：snapflow capture\n"
                "  GNOME：设置 -> 键盘 -> 自定义快捷键\n"
                "  KDE  ：系统设置 -> 快捷键 -> 自定义快捷键",
                file=sys.stderr)
            return 2
        from .hotkey import X11Hotkey
        try:
            hotkey = X11Hotkey(args.hotkey)
        except RuntimeError as exc:
            print(f"snapflow: 热键注册失败：{exc}", file=sys.stderr)
            return 2
        hotkey.triggered.connect(snap.capture)
        print(f"snapflow 已常驻，按 {args.hotkey} 唤起截图（Ctrl+C 退出）")
    else:
        # 让窗口系统先就绪再抓屏
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, snap.capture)

    # 允许 Ctrl+C 退出 Qt 事件循环
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer_ping = _keep_sigint_alive(app)

    return app.exec()


def _keep_sigint_alive(app):
    from PyQt6.QtCore import QTimer
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(500)
    return timer


if __name__ == "__main__":
    raise SystemExit(main())
