"""标注图元：矩形、箭头、文字、马赛克，以及把它们画到 QPainter 上。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF


@dataclass
class Annotation:
    color: QColor = field(default_factory=lambda: QColor("#ff3b30"))
    width: int = 3

    def pen(self) -> QPen:
        pen = QPen(self.color, self.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def paint(self, painter: QPainter, base: QImage) -> None:
        raise NotImplementedError


@dataclass
class RectAnnotation(Annotation):
    rect: QRectF = field(default_factory=QRectF)

    def paint(self, painter: QPainter, base: QImage) -> None:
        painter.setPen(self.pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect.normalized())


@dataclass
class ArrowAnnotation(Annotation):
    start: QPointF = field(default_factory=QPointF)
    end: QPointF = field(default_factory=QPointF)

    def paint(self, painter: QPainter, base: QImage) -> None:
        painter.setPen(self.pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(self.start, self.end)

        dx, dy = self.end.x() - self.start.x(), self.end.y() - self.start.y()
        length = math.hypot(dx, dy)
        if length < 1e-3:
            return
        ux, uy = dx / length, dy / length
        head = max(10.0, self.width * 4.0)
        px, py = -uy, ux  # 垂直方向
        left = QPointF(self.end.x() - ux * head + px * head * 0.5,
                       self.end.y() - uy * head + py * head * 0.5)
        right = QPointF(self.end.x() - ux * head - px * head * 0.5,
                        self.end.y() - uy * head - py * head * 0.5)
        painter.setBrush(self.color)
        painter.drawPolygon(QPolygonF([self.end, left, right]))


@dataclass
class TextAnnotation(Annotation):
    pos: QPointF = field(default_factory=QPointF)
    text: str = ""
    font_size: int = 18

    def paint(self, painter: QPainter, base: QImage) -> None:
        if not self.text:
            return
        painter.setPen(self.pen())
        font = QFont()
        font.setPixelSize(self.font_size)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.pos, self.text)


@dataclass
class MosaicAnnotation(Annotation):
    rect: QRectF = field(default_factory=QRectF)
    block: int = 12  # 马赛克块大小（图像像素）
    # 像素化结果缓存：拖完一次后每帧重绘不必再做缩放
    _cache_key: tuple | None = field(
        default=None, init=False, repr=False, compare=False)
    _pixelated: QImage | None = field(
        default=None, init=False, repr=False, compare=False)

    def paint(self, painter: QPainter, base: QImage) -> None:
        rect = self.rect.normalized().toAlignedRect().intersected(base.rect())
        if rect.isEmpty():
            return
        key = (rect.x(), rect.y(), rect.width(), rect.height(),
               base.cacheKey())
        if self._pixelated is None or self._cache_key != key:
            region = base.copy(rect)
            small = region.scaled(
                max(1, rect.width() // self.block),
                max(1, rect.height() // self.block),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._pixelated = small.scaled(
                rect.width(), rect.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._cache_key = key
        painter.drawImage(rect.topLeft(), self._pixelated)
