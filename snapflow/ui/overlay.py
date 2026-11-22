"""微信式截图覆盖层：框选与标注在同一全屏窗口内完成。

流程：拖拽框选 -> 选区旁弹出工具条（矩形/箭头/文字/马赛克/颜色/撤销）
-> Enter 复制到剪贴板；Ctrl+Enter 直接保存到 ~/Pictures/截图/；Esc 取消。

渲染在构造时一次性把截图平滑缩放到设备分辨率并缓成两张位图（压暗背景 /
亮图），每帧只做位图拷贝，拖动时再局部重绘，避免逐帧整屏缩放与混合。
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

from PyQt6.QtCore import (
    QEvent, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)

from ..annotations import (
    Annotation, ArrowAnnotation, MosaicAnnotation, RectAnnotation,
    TextAnnotation,
)
from ..clipboard import copy_image
from ..plugins import PluginRegistry

log = logging.getLogger(__name__)

TOOL_RECT = "rect"
TOOL_ARROW = "arrow"
TOOL_TEXT = "text"
TOOL_MOSAIC = "mosaic"

TOOLS = (
    (TOOL_RECT, "矩形"),
    (TOOL_ARROW, "箭头"),
    (TOOL_TEXT, "文字"),
    (TOOL_MOSAIC, "马赛克"),
)

TOOL_SHORTCUTS = {
    Qt.Key.Key_R: TOOL_RECT,
    Qt.Key.Key_A: TOOL_ARROW,
    Qt.Key.Key_T: TOOL_TEXT,
    Qt.Key.Key_M: TOOL_MOSAIC,
}

TOOL_HINTS = {
    TOOL_RECT: "矩形 (R)",
    TOOL_ARROW: "箭头 (A)",
    TOOL_TEXT: "文字 (T)",
    TOOL_MOSAIC: "马赛克 (M)",
}

BG_DIM_ALPHA = 120      # 未选中区域压暗程度
DAMAGE_MARGIN = 48.0    # 局部重绘时为边框/手柄/尺寸标签留的余量

PRESET_COLORS = ("#ff3b30", "#ffcc00", "#34c759", "#0a84ff",
                 "#ffffff", "#000000")

SAVE_DIR = Path.home() / "Pictures" / "截图"


class CaptureOverlay(QWidget):
    """全屏冻结层 + 框选 + 内联标注，一个窗口完成整个截图流程。"""

    finished = pyqtSignal()
    cancelled = pyqtSignal()
    claimed = pyqtSignal()   # 用户在本覆盖层开始操作（用于关闭其它屏幕的覆盖层）

    def __init__(self, screenshot: QImage, plugins: PluginRegistry,
                 screen=None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # 每帧都会画满整个窗口，告诉 Qt 不必先擦背景，省一次填充
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._image = screenshot
        self._plugins = plugins
        screen = screen or QGuiApplication.primaryScreen()
        # 覆盖层只盖鼠标所在的那一块屏（Wayland 全屏窗口也只能占一块屏）
        self.setScreen(screen)
        geometry = screen.geometry()
        self.setGeometry(geometry)
        # 图像像素 / 逻辑坐标 的换算系数（HiDPI 下通常 >1）
        self._scale = (screenshot.width() / geometry.width()
                       if geometry.width() else 1.0)

        # 预缩放缓存：压暗整屏 + 亮图，绘制时 1:1 拷贝，不再逐帧缩放
        self._dim_cache: QPixmap | None = None
        self._bright_cache: QPixmap | None = None
        self._cache_key: tuple | None = None

        # ---- 框选状态 ----
        self._origin = QPointF()
        self._dragging = False
        self._selection = QRectF()     # 控件（逻辑）坐标
        self._editing = False          # True = 选区已定，进入标注模式

        # ---- 标注状态（均为图像像素坐标） ----
        self.annotations: list[Annotation] = []
        self._draft: Annotation | None = None
        self._press_img: QPointF | None = None
        self.tool = TOOL_RECT
        self.color = QColor(PRESET_COLORS[0])
        self.pen_width = 3
        self.font_size = 18

        self._text_edit: QLineEdit | None = None
        self._text_img_pos: QPointF | None = None
        self._claimed = False

        # ---- 选区调整状态（逻辑坐标） ----
        self._resize_handle: str | None = None
        self._resize_rect0 = QRectF()
        self.HANDLE_SIZE = 8.0
        self.MIN_SELECTION = 24.0

        self._toolbar = self._build_toolbar()
        self._toolbar.hide()

    # ================= 工具条 =================

    def _build_toolbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setStyleSheet(
            "QFrame { background: rgba(30,30,30,230); border-radius: 6px; }"
            "QPushButton { color: white; background: transparent;"
            "  border: none; padding: 4px 8px; font-size: 13px; }"
            "QPushButton:hover { background: rgba(255,255,255,40); }"
            "QPushButton[active=\"true\"] { background: #0a84ff;"
            "  border-radius: 4px; }")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        self._tool_buttons: dict[str, QPushButton] = {}
        for tool, label in TOOLS:
            btn = QPushButton(label)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(TOOL_HINTS[tool])
            btn.clicked.connect(
                lambda _=False, t=tool: self._set_tool(t))
            layout.addWidget(btn)
            self._tool_buttons[tool] = btn
        self._tool_buttons[self.tool].setProperty("active", "true")

        layout.addSpacing(8)
        self._color_buttons: list[QPushButton] = []
        for hexcolor in PRESET_COLORS:
            swatch = QPushButton()
            swatch.setFixedSize(18, 18)
            swatch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setStyleSheet(
                f"background: {hexcolor}; border: 1px solid #888;"
                "border-radius: 9px; padding: 0;")
            swatch.clicked.connect(
                lambda _=False, c=hexcolor: self._set_color(c))
            layout.addWidget(swatch)
            self._color_buttons.append(swatch)

        layout.addSpacing(8)
        undo_btn = QPushButton("撤销")
        undo_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        undo_btn.setToolTip("撤销上一笔标注 (Ctrl+Z)")
        undo_btn.clicked.connect(self.undo)
        layout.addWidget(undo_btn)

        save_btn = QPushButton("保存")
        save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setToolTip("保存到 ~/Pictures/截图 (Ctrl+S / Ctrl+Enter)")
        save_btn.clicked.connect(self.save)
        layout.addWidget(save_btn)

        ok_btn = QPushButton("完成")
        ok_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setToolTip("复制到剪贴板 (Enter / 双击选区)")
        ok_btn.clicked.connect(self.copy_and_close)
        layout.addWidget(ok_btn)

        hint = QLabel("Enter 复制 · Ctrl+S 保存")
        hint.setStyleSheet("color: #aaa; font-size: 11px; padding-left: 6px;")
        layout.addWidget(hint)

        bar.adjustSize()
        return bar

    def _place_toolbar(self) -> None:
        bar = self._toolbar
        rect = self._selection
        x = rect.right() - bar.width()
        y = rect.bottom() + 8
        if y + bar.height() > self.height() - 4:     # 下方没空间就放到选区上方
            y = rect.top() - bar.height() - 8
        if y < 4:                                    # 上方也没有就放进选区内
            y = rect.bottom() - bar.height() - 8
        x = min(max(x, 4.0), self.width() - bar.width() - 4.0)
        y = min(max(y, 4.0), self.height() - bar.height() - 4.0)
        bar.move(round(x), round(y))
        bar.show()

    def _set_tool(self, tool: str) -> None:
        self._commit_text_edit()
        self.tool = tool
        for name, btn in self._tool_buttons.items():
            btn.setProperty("active", "true" if name == tool else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.setFocus()

    def _set_color(self, hexcolor: str) -> None:
        self.color = QColor(hexcolor)
        self.setFocus()

    def _claim(self) -> None:
        """本屏成为本次截图的交互目标，通知应用关闭其它屏幕的覆盖层。"""
        if self._claimed:
            return
        self._claimed = True
        self.raise_()
        self.activateWindow()
        self.claimed.emit()

    # ================= 背景缓存 =================

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._ensure_caches()
        if getattr(self, "_editing", False) and getattr(self, "_toolbar", None):
            self._place_toolbar()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 全屏窗口在 show 之后才拿到最终几何，这里补建一次缓存
        self._ensure_caches()

    def _ensure_caches(self) -> None:
        if self._image.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        self._scale = self._image.width() / self.width()
        key = (self.width(), self.height(), self.devicePixelRatioF())
        if self._cache_key == key and self._dim_cache is not None:
            return
        self._cache_key = key
        self._build_background_caches()

    def _build_background_caches(self) -> None:
        """预缩放 + 预合成：压暗整屏与亮图各一张，绘制时只做拷贝。

        逐帧对原图做平滑缩放 + 全屏 alpha 混合是最大热点（1080p 下约 3 ms），
        改为构造时各做一次（约 5 ms），此后每帧降到约 1 ms。
        """
        if self._image.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        dpr = self.devicePixelRatioF()
        size = QSize(max(1, round(self.width() * dpr)),
                     max(1, round(self.height() * dpr)))
        scaled = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        scaled.setDevicePixelRatio(dpr)
        scaled.fill(Qt.GlobalColor.transparent)
        painter = QPainter(scaled)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(QRectF(0, 0, self.width(), self.height()), self._image)
        painter.end()

        bright = QPixmap.fromImage(scaled)
        bright.setDevicePixelRatio(dpr)
        dim = QPixmap.fromImage(scaled)
        dim.setDevicePixelRatio(dpr)
        painter = QPainter(dim)
        painter.fillRect(QRectF(0, 0, self.width(), self.height()),
                         QColor(0, 0, 0, BG_DIM_ALPHA))
        painter.end()
        self._bright_cache, self._dim_cache = bright, dim

    # ================= 坐标换算 =================

    def _to_image(self, pos: QPointF) -> QPointF:
        return QPointF(pos.x() * self._scale, pos.y() * self._scale)

    def _clamp_point(self, pos: QPointF) -> QPointF:
        """把指针限制在本屏内，避免拖出屏幕时选区越界或出现负坐标。"""
        return QPointF(min(max(pos.x(), 0.0), float(self.width())),
                       min(max(pos.y(), 0.0), float(self.height())))

    def _clamp_to_selection(self, pos: QPointF) -> QPointF:
        r = self._selection
        return QPointF(min(max(pos.x(), r.left()), r.right()),
                       min(max(pos.y(), r.top()), r.bottom()))

    def _repaint_region(self, *rects: QRectF) -> None:
        """只重绘旧/新状态覆盖的区域，拖动大选区时省掉整屏重绘。

        区域过大（接近整屏）时退回整屏重绘，避免多个小矩形拼接触发的额外开销。
        """
        region = QRectF()
        for rect in rects:
            if rect.isEmpty():
                continue
            region = region.united(rect.adjusted(
                -DAMAGE_MARGIN, -DAMAGE_MARGIN, DAMAGE_MARGIN, DAMAGE_MARGIN))
        if (region.isEmpty() or region.width() >= self.width()
                or region.height() >= self.height()):
            self.update()
        else:
            self.update(region.toAlignedRect())

    def _draft_bounds(self) -> QRectF:
        """当前草稿标注在逻辑坐标下的包围盒（含画笔余量），用于局部重绘。"""
        draft = self._draft
        if draft is None:
            return QRectF()
        if isinstance(draft, ArrowAnnotation):
            rect = QRectF(draft.start, draft.end).normalized()
        else:
            rect = QRectF(draft.rect).normalized()
        pad = max(float(self.pen_width), 12.0)
        rect = rect.adjusted(-pad, -pad, pad, pad)
        s = self._scale
        return QRectF(rect.x() / s, rect.y() / s,
                      rect.width() / s, rect.height() / s)

    # ================= 鼠标交互 =================

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._claim()
        pos = self._clamp_point(event.position())
        if not self._editing:
            self._origin = pos
            self._dragging = True
            self._selection = QRectF(pos, pos)
            self.update()
            return
        # 调整选区：先命中边框/角上的手柄
        handle = self._hit_handle(pos)
        if handle is not None:
            self._resize_handle = handle
            self._resize_rect0 = QRectF(self._selection)
            return
        # 选区外按拖拽 = 重新框选
        if not self._selection.contains(pos):
            self._start_new_selection(pos)
            return
        # 标注模式
        img_pos = self._to_image(self._clamp_to_selection(pos))
        if self.tool == TOOL_TEXT:
            self._start_text_edit(img_pos)
            return
        self._press_img = img_pos
        self._draft = self._make_draft(img_pos, img_pos)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = self._clamp_point(event.position())
        if self._resize_handle is not None:
            old = QRectF(self._selection)
            self._apply_resize(pos)
            self._repaint_region(old, self._selection)
            self._place_toolbar()
        elif self._dragging:
            old = QRectF(self._selection)
            self._selection = QRectF(self._origin, pos).normalized()
            self._repaint_region(old, self._selection)
        elif self._draft is not None and self._press_img is not None:
            old = self._draft_bounds()
            img_pos = self._to_image(self._clamp_to_selection(pos))
            self._draft = self._make_draft(self._press_img, img_pos)
            self._repaint_region(old, self._draft_bounds())
        else:
            self._update_hover_cursor(pos)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._editing and self._selection.contains(event.position()):
            self._claim()
            self.copy_and_close()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = self._clamp_point(event.position())
        if self._resize_handle is not None:
            self._apply_resize(pos)
            self._resize_handle = None
            self._place_toolbar()
            self.update()
            return
        if self._dragging:
            self._dragging = False
            self._selection = QRectF(self._origin, pos).normalized()
            if (self._selection.width() >= 5
                    and self._selection.height() >= 5):
                self._editing = True
                self._place_toolbar()
                self._update_hover_cursor(pos)
            else:
                self._selection = QRectF()
            self.update()
            return
        if self._draft is not None and self._press_img is not None:
            img_pos = self._to_image(self._clamp_to_selection(pos))
            draft = self._make_draft(self._press_img, img_pos)
            if self._is_meaningful(draft):
                self.annotations.append(draft)
            self._draft = None
            self._press_img = None
            self.update()

    # ---- 选区调整 ----

    def _start_new_selection(self, pos: QPointF) -> None:
        """在选区外拖拽：放弃当前选区与标注，重新框选。"""
        self._commit_text_edit()
        self._resize_handle = None
        self._editing = False
        self._toolbar.hide()
        self._selection = QRectF(pos, pos)
        self._origin = pos
        self._dragging = True
        self.annotations.clear()
        self._draft = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def _hit_handle(self, pos: QPointF) -> str | None:
        if not self._editing or self._selection.isEmpty():
            return None
        r = self._selection
        h = self.HANDLE_SIZE
        x, y = pos.x(), pos.y()
        if not (r.left() - h <= x <= r.right() + h
                and r.top() - h <= y <= r.bottom() + h):
            return None
        near_l = abs(x - r.left()) <= h
        near_r = abs(x - r.right()) <= h
        near_t = abs(y - r.top()) <= h
        near_b = abs(y - r.bottom()) <= h
        if near_l and near_t:
            return "nw"
        if near_r and near_t:
            return "ne"
        if near_l and near_b:
            return "sw"
        if near_r and near_b:
            return "se"
        if near_l:
            return "w"
        if near_r:
            return "e"
        if near_t:
            return "n"
        if near_b:
            return "s"
        return None

    def _apply_resize(self, pos: QPointF) -> None:
        r = QRectF(self._resize_rect0)
        x = min(max(pos.x(), 0.0), float(self.width()))
        y = min(max(pos.y(), 0.0), float(self.height()))
        m = self.MIN_SELECTION
        handle = self._resize_handle or ""
        if "w" in handle:
            r.setLeft(min(x, r.right() - m))
        if "e" in handle:
            r.setRight(max(x, r.left() + m))
        if "n" in handle:
            r.setTop(min(y, r.bottom() - m))
        if "s" in handle:
            r.setBottom(max(y, r.top() + m))
        self._selection = r

    def _update_hover_cursor(self, pos: QPointF) -> None:
        handle = self._hit_handle(pos)
        cursors = {
            "nw": Qt.CursorShape.SizeFDiagCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor,
            "sw": Qt.CursorShape.SizeBDiagCursor,
            "n": Qt.CursorShape.SizeVerCursor,
            "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor,
            "w": Qt.CursorShape.SizeHorCursor,
        }
        if handle is not None:
            self.setCursor(cursors[handle])
        elif (self._editing and self.tool == TOOL_TEXT
                and self._selection.contains(pos)):
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    # ================= 标注构造 =================

    def _make_draft(self, a: QPointF, b: QPointF) -> Annotation:
        common = dict(color=QColor(self.color), width=self.pen_width)
        if self.tool == TOOL_ARROW:
            return ArrowAnnotation(start=a, end=b, **common)
        if self.tool == TOOL_MOSAIC:
            return MosaicAnnotation(rect=QRectF(a, b), **common)
        return RectAnnotation(rect=QRectF(a, b), **common)

    @staticmethod
    def _is_meaningful(a: Annotation) -> bool:
        if isinstance(a, (RectAnnotation, MosaicAnnotation)):
            r = a.rect.normalized()
            return r.width() >= 2 and r.height() >= 2
        if isinstance(a, ArrowAnnotation):
            return (a.end - a.start).manhattanLength() >= 4
        return True

    def undo(self) -> None:
        self._commit_text_edit()
        if self.annotations:
            self.annotations.pop()
            self.update()
        self.setFocus()

    # ================= 文字输入 =================

    def _start_text_edit(self, img_pos: QPointF) -> None:
        self._commit_text_edit()
        self._text_img_pos = img_pos
        anchor = QPointF(img_pos.x() / self._scale, img_pos.y() / self._scale)
        edit = QLineEdit(self)
        edit.setPlaceholderText("输入文字，回车确认")
        edit.setStyleSheet(
            f"color: {self.color.name()}; font-size: "
            f"{max(10, round(self.font_size / self._scale))}px;"
            "background: rgba(255,255,255,230);")
        width, height = 220, 32
        x = min(max(anchor.x(), 0.0), max(0.0, self.width() - width))
        y = min(max(anchor.y(), 0.0), max(0.0, self.height() - height))
        edit.move(round(x), round(y))
        edit.resize(width, height)
        edit.returnPressed.connect(self._commit_text_edit)
        edit.editingFinished.connect(self._commit_text_edit)
        edit.installEventFilter(self)   # Esc 丢弃、Ctrl+Enter 直接保存
        edit.show()
        edit.setFocus()
        self._text_edit = edit

    def _commit_text_edit(self) -> None:
        edit, img_pos = self._text_edit, self._text_img_pos
        if edit is None:
            return
        self._text_edit = None
        self._text_img_pos = None
        text = edit.text().strip()
        edit.hide()
        edit.deleteLater()
        if text and img_pos is not None:
            baseline = QPointF(img_pos.x(),
                               img_pos.y() + self.font_size * 0.8)
            self.annotations.append(TextAnnotation(
                color=QColor(self.color), width=self.pen_width,
                pos=baseline, text=text, font_size=self.font_size))
        self.setFocus()
        self.update()

    def _discard_text_edit(self) -> None:
        edit = self._text_edit
        if edit is None:
            return
        self._text_edit = None
        self._text_img_pos = None
        edit.hide()
        edit.deleteLater()
        self.setFocus()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._text_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self._discard_text_edit()
                return True
            if (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._commit_text_edit()
                self.save()
                return True
        return super().eventFilter(obj, event)

    # ================= 键盘 =================

    def _cancel(self) -> None:
        self._discard_text_edit()
        self.cancelled.emit()
        self.close()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        self._claim()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if ctrl:
                self.save()
            else:
                self.copy_and_close()
        elif ctrl and key == Qt.Key.Key_S:
            self.save()
        elif ctrl and key == Qt.Key.Key_C:
            self.copy_and_close()
        elif ctrl and key == Qt.Key.Key_Z:
            self.undo()
        elif key == Qt.Key.Key_Escape:
            self._cancel()
        elif self._editing and not mods and key in TOOL_SHORTCUTS:
            self._set_tool(TOOL_SHORTCUTS[key])
        else:
            super().keyPressEvent(event)

    # ================= 绘制 =================

    def _draw_hint(self, painter: QPainter, text: str) -> None:
        font = painter.font()
        font.setPixelSize(13)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = metrics.horizontalAdvance(text) + 28
        h = metrics.height() + 14
        x = (self.width() - w) / 2
        y = self.height() - h - 36
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(QRectF(x, y, w, h), 7, 7)
        painter.setPen(QColor(235, 235, 235))
        painter.drawText(QRectF(x, y, w, h),
                         Qt.AlignmentFlag.AlignCenter, text)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def paintEvent(self, event) -> None:
        self._ensure_caches()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        if self._dim_cache is not None:
            painter.drawPixmap(QPointF(0, 0), self._dim_cache)
        else:
            painter.drawImage(self.rect(), self._image)
            painter.fillRect(self.rect(), QColor(0, 0, 0, BG_DIM_ALPHA))

        rect = self._selection
        if rect.isEmpty():
            if not self._dragging:
                self._draw_hint(painter, "拖动选择区域 · 右键 / Esc 取消")
            painter.end()
            return

        # 选区内恢复亮图：从预缩放亮图按设备像素拷贝，不再逐帧缩放
        dpr = self.devicePixelRatioF()
        if self._bright_cache is not None:
            painter.drawPixmap(
                rect, self._bright_cache,
                QRectF(rect.x() * dpr, rect.y() * dpr,
                       rect.width() * dpr, rect.height() * dpr))
        else:
            s = self._scale
            painter.drawImage(rect, self._image,
                              QRectF(rect.x() * s, rect.y() * s,
                                     rect.width() * s, rect.height() * s))

        # 标注（存储为图像像素坐标，显示时除以 s）
        painter.save()
        painter.setClipRect(rect)
        painter.scale(1.0 / self._scale, 1.0 / self._scale)
        for ann in self.annotations:
            ann.paint(painter, self._image)
        if self._draft is not None:
            self._draft.paint(painter, self._image)
        painter.restore()

        painter.setPen(QPen(QColor("#0a84ff"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        if self._editing:
            painter.setPen(QPen(QColor("#0a84ff"), 1))
            painter.setBrush(QColor("white"))
            for point in self._handle_points():
                painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))

        w = round(rect.width() * self._scale)
        h = round(rect.height() * self._scale)
        painter.setPen(QColor("white"))
        painter.drawText(rect.topLeft() + QPointF(4, -6),
                         f"{w} × {h}")
        painter.end()

    def _handle_points(self) -> list[QPointF]:
        r = self._selection
        cx, cy = r.center().x(), r.center().y()
        return [
            QPointF(r.left(), r.top()), QPointF(cx, r.top()),
            QPointF(r.right(), r.top()), QPointF(r.right(), cy),
            QPointF(r.right(), r.bottom()), QPointF(cx, r.bottom()),
            QPointF(r.left(), r.bottom()), QPointF(r.left(), cy),
        ]

    # ================= 输出 =================

    def _selection_image_rect(self) -> QRect:
        s = self._scale
        rect = QRectF(self._selection.x() * s, self._selection.y() * s,
                      self._selection.width() * s,
                      self._selection.height() * s)
        return rect.toAlignedRect().intersected(self._image.rect())

    def render(self) -> QImage:
        """裁出选区并合成全部标注，输出图像像素分辨率的结果。"""
        sel = self._selection_image_rect()
        out = QImage(sel.size(), QImage.Format.Format_ARGB32)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.drawImage(QPointF(0, 0), self._image, QRectF(sel))
        painter.translate(-sel.topLeft().x(), -sel.topLeft().y())
        painter.setClipRect(QRectF(0, 0, sel.width(), sel.height()))
        for ann in self.annotations:
            ann.paint(painter, self._image)
        painter.end()
        return out

    def _final_image(self) -> QImage:
        self._commit_text_edit()
        return self._plugins.on_edited(self.render())

    def copy_and_close(self) -> None:
        if self._selection.isEmpty():
            return
        if copy_image(self._final_image()):
            self.finished.emit()
            self.close()

    def save(self) -> None:
        if self._selection.isEmpty():
            return
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        target = SAVE_DIR / f"snapflow_{stamp}.png"
        image = self._final_image()
        self._plugins.before_save(image, target)
        if image.save(str(target)):
            log.info("已保存 %s", target)
            self.finished.emit()
            self.close()
