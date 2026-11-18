"""示例插件：保存前把副本写一份到 /tmp/snapflow-last.png。

把本文件复制到 ~/.config/snapflow/plugins/ 即可生效。
"""

from pathlib import Path


class MirrorSavePlugin:
    def on_edited(self, image):
        # OCR / 自动打码应在此钩子中修改并返回图像
        return image

    def before_save(self, image, path: Path) -> None:
        image.save("/tmp/snapflow-last.png")


PLUGIN = MirrorSavePlugin()
