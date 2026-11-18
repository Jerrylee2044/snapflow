"""命令行入口。

用法：
    python -m snapflow capture    # 立即截一次（进程内完成，适合测试）
    python -m snapflow trigger    # 通知常驻 daemon 截图（桌面快捷键绑定这个）
    python -m snapflow daemon     # 常驻：本地 socket + X11 全局热键
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snapflow", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("capture", help="立即截取一次屏幕区域（进程内完成）")
    sub.add_parser(
        "trigger",
        help="通知常驻 daemon 截图；daemon 不在线时退化为直接截图")

    daemon = sub.add_parser("daemon", help="常驻：等待快捷键触发")
    daemon.add_argument("--hotkey", default="Ctrl+Shift+A",
                        help="X11 全局热键，如 'Ctrl+Shift+A' 或 'Print'")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # 最快路径：daemon 在线时只发一条 socket 消息，不导入 Qt（~30ms）
    if args.command == "trigger":
        from .ipc import send_trigger
        if send_trigger():
            return 0
        logging.info("daemon 未运行，退化为直接截图")

    # 以下路径需要 Qt；延迟导入以保住 trigger 的冷启动速度
    from .app import SnapflowApp, create_qapp
    from .backends.base import CaptureError

    app = create_qapp()
    if args.command == "daemon":
        return _run_daemon(app, args.hotkey)

    try:
        snap = SnapflowApp(one_shot=True)
    except CaptureError as exc:
        print(f"snapflow: {exc}", file=sys.stderr)
        return 2
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(30, snap.capture)
    _install_sigint(app)
    return app.exec()


def _run_daemon(app, hotkey_spec: str) -> int:
    from PyQt6.QtCore import QTimer

    from .app import SnapflowApp
    from .backends.base import CaptureError
    from .trigger_server import TriggerServer

    try:
        snap = SnapflowApp(one_shot=False)
    except CaptureError as exc:
        print(f"snapflow: {exc}", file=sys.stderr)
        return 2

    try:
        server = TriggerServer()
    except RuntimeError as exc:
        print(f"snapflow: {exc}", file=sys.stderr)
        print("snapflow: 可能已有 daemon 在运行", file=sys.stderr)
        return 2
    server.triggered.connect(snap.capture)

    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        print("snapflow daemon 已常驻（Wayland）：等待 snapflow trigger。")
        print("请把桌面快捷键绑定到：snapflow trigger")
    else:
        from .hotkey import X11Hotkey
        try:
            hotkey = X11Hotkey(hotkey_spec)
        except RuntimeError as exc:
            print(f"snapflow: 热键注册失败：{exc}", file=sys.stderr)
            return 2
        hotkey.triggered.connect(snap.capture)
        app._hotkey = hotkey  # 持有引用
        print(f"snapflow daemon 已常驻，按 {hotkey_spec} 唤起截图。")

    _install_sigint(app)
    timer = QTimer()  # 保持 Python 侧可响应 SIGINT
    timer.timeout.connect(lambda: None)
    timer.start(500)
    app._sigint_timer = timer  # 持有引用，避免被回收
    return app.exec()


def _install_sigint(app) -> None:
    signal.signal(signal.SIGINT, lambda *_: app.quit())


if __name__ == "__main__":
    raise SystemExit(main())
