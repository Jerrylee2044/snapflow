"""插件接口（第一版预留，供 OCR、自动打码等后续能力挂接）。

插件是一个 Python 模块，放在 ``~/.config/snapflow/plugins/`` 下，
模块中定义一个名为 ``PLUGIN`` 的对象，实现以下任意钩子：

- ``on_captured(image: QImage) -> QImage | None``
    原始全屏截图完成后调用，可返回替换图像（例如先做一次预处理）。
- ``on_edited(image: QImage) -> QImage | None``
    编辑完成、即将复制/保存前调用，可返回替换图像（OCR、自动打码的挂点）。
- ``before_save(image: QImage, path: Path) -> None``
    文件即将写入磁盘前调用（只读通知，可用于备份、上传等）。

所有钩子都在 GUI 线程内被同步调用；耗时操作请自行转后台线程。
"""

from .base import Plugin, PluginRegistry

__all__ = ["Plugin", "PluginRegistry"]
