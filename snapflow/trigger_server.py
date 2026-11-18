"""daemon 侧的本地触发器：监听 Unix socket，收到消息后发出 ``triggered``。"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .ipc import MESSAGE, socket_path

log = logging.getLogger(__name__)


class TriggerServer(QObject):
    triggered = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        path = socket_path()
        self._server = QLocalServer(self)
        # 清理上一个进程残留的 socket 文件（Unix socket 不会自动消失）
        QLocalServer.removeServer(path)
        if not self._server.listen(path):
            raise RuntimeError(
                f"无法监听 {path}：{self._server.errorString()}")
        self._server.newConnection.connect(self._on_connection)
        log.info("触发器已就绪：%s", path)

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._on_ready(c))
            conn.disconnected.connect(conn.deleteLater)
            if conn.bytesAvailable():
                self._on_ready(conn)

    def _on_ready(self, conn: QLocalSocket) -> None:
        data = bytes(conn.readAll())
        # 先回执再截图：截图会阻塞事件循环，回执必须抢在前面
        conn.write(b"ok\n")
        conn.flush()
        if MESSAGE in data:
            # 让当前 socket 事件处理完再进入截图流程
            QTimer.singleShot(0, self.triggered.emit)
        conn.disconnectFromServer()
