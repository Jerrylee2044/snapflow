"""用户配置：~/.config/snapflow/config.json（缺失时使用默认值）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "snapflow" / "config.json"

DEFAULTS = {
    # 截图瞬间临时关闭 GNOME 系统动画：跳过快门闪光和窗口打开动画
    "suppress_shutter_flash": True,
}


def load() -> dict:
    if not CONFIG_PATH.is_file():
        return dict(DEFAULTS)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("配置必须是 JSON 对象")
        return {**DEFAULTS, **data}
    except Exception:  # noqa: BLE001 - 配置损坏时用默认值，不影响启动
        log.warning("配置文件解析失败，使用默认配置", exc_info=True)
        return dict(DEFAULTS)
