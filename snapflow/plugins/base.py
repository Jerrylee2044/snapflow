from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from PyQt6.QtGui import QImage

log = logging.getLogger(__name__)

DEFAULT_PLUGIN_DIR = Path.home() / ".config" / "snapflow" / "plugins"


@runtime_checkable
class Plugin(Protocol):
    """插件协议：实现任意一个钩子即可，全部可选。"""

    def on_captured(self, image: QImage) -> QImage | None: ...
    def on_edited(self, image: QImage) -> QImage | None: ...
    def before_save(self, image: QImage, path: Path) -> None: ...


class PluginRegistry:
    """从目录加载插件并按钩子分发。单个插件出错不影响其余插件与主流程。"""

    def __init__(self, plugin_dir: Path | None = None) -> None:
        self.plugin_dir = plugin_dir or DEFAULT_PLUGIN_DIR
        self._plugins: list[object] = []
        self.load()

    def load(self) -> None:
        self._plugins.clear()
        if not self.plugin_dir.is_dir():
            return
        for file in sorted(self.plugin_dir.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(
                    f"snapflow_plugin_{file.stem}", file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                plugin = getattr(module, "PLUGIN", None)
                if plugin is not None:
                    self._plugins.append(plugin)
                    log.info("已加载插件 %s", file.name)
            except Exception:  # noqa: BLE001 - 插件隔离
                log.exception("插件 %s 加载失败，已跳过", file.name)

    @property
    def plugins(self) -> tuple[object, ...]:
        return tuple(self._plugins)

    def _apply_image_hook(self, hook: str, image: QImage) -> QImage:
        for plugin in self._plugins:
            func = getattr(plugin, hook, None)
            if func is None:
                continue
            try:
                result = func(image)
                if isinstance(result, QImage) and not result.isNull():
                    image = result
            except Exception:  # noqa: BLE001 - 插件隔离
                log.exception("插件 %r 的 %s 钩子执行失败", plugin, hook)
        return image

    def on_captured(self, image: QImage) -> QImage:
        return self._apply_image_hook("on_captured", image)

    def on_edited(self, image: QImage) -> QImage:
        return self._apply_image_hook("on_edited", image)

    def before_save(self, image: QImage, path: Path) -> None:
        for plugin in self._plugins:
            func = getattr(plugin, "before_save", None)
            if func is None:
                continue
            try:
                func(image, path)
            except Exception:  # noqa: BLE001 - 插件隔离
                log.exception("插件 %r 的 before_save 钩子执行失败", plugin)
