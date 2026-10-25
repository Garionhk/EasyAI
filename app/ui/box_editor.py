"""Draw layout boxes: where each thing goes, and what it is.

Flux 3 Klein and MOSS-Image can be told the layout of a picture - "a red cat
here, the word SALE across the top" - as boxes on a canvas. ComfyUI has its own
box canvas; this is EasyAI's, so nobody has to open ComfyUI to use it.

Drag on the canvas to draw a box. Click a box to pick it, drag it to move it,
drag its corner to resize it. Each box gets a kind and a description on the
right. Boxes are drawn from the back forwards, so the background goes first.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGraphicsItem,
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from app.boxes import KINDS, Box, numbers
from app.i18n import t
from app.ui import theme

#: The longer side of the canvas, in scene units. Boxes are stored as
#: fractions, so this only sets how finely the mouse can place them.
_SCENE_SIDE = 1000.0
#: How close to a box's corner a press counts as grabbing the corner.
_HANDLE = 14.0
#: Smaller than this and a drag was a click, not a box.
_MIN_SIDE = 12.0

_KIND_LABELS = {"object": "Object", "text": "Text", "character": "Character"}
_KIND_COLOURS = {"object": "#4ea1ff", "text": "#ffb347", "character": "#7bd88f"}


class _BoxItem(QGraphicsRectItem):
    """One box on the canvas. Moves with the mouse; resizes from its corner."""

    def __init__(self, rect: QRectF, kind: str, bounds: QRectF, editor: "BoxEditor"):
        super().__init__(QRectF(0, 0, rect.width(), rect.height()))
        self.setPos(rect.topLeft())
        self.kind = kind
        self.number = ""
        self._bounds = bounds
        self._editor = editor
        self._resizing = False
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

    # -- geometry ----------------------------------------------------------
    def scene_rect(self) -> QRectF:
        return QRectF(self.pos(), self.rect().size())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            # Keep the whole box on the canvas.
            size = self.rect().size()
            x = min(max(value.x(), self._bounds.left()), self._bounds.right() - size.width())
            y = min(max(value.y(), self._bounds.top()), self._bounds.bottom() - size.height())
            return QPointF(x, y)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._editor.geometry_changed()
        return super().itemChange(change, value)

    def _near_corner(self, point: QPointF) -> bool:
        corner = self.rect().bottomRight()
        return abs(point.x() - corner.x()) <= _HANDLE and abs(point.y() - corner.y()) <= _HANDLE

    def hoverMoveEvent(self, event):
        self.setCursor(Qt.SizeFDiagCursor if self._near_corner(event.pos()) else Qt.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._near_corner(event.pos()):
            self._resizing = True
            self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            limit_w = self._bounds.right() - self.pos().x()
            limit_h = self._bounds.bottom() - self.pos().y()
            w = min(max(event.pos().x(), _MIN_SIDE), limit_w)
            h = min(max(event.pos().y(), _MIN_SIDE), limit_h)
            self.prepareGeometryChange()
            self.setRect(QRectF(0, 0, w, h))
            self._editor.geometry_changed()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resizing = False
        super().mouseReleaseEvent(event)

    # -- drawing -----------------------------------------------------------
    def paint(self, painter: QPainter, option, widget=None):
        colour = QColor(_KIND_COLOURS.get(self.kind, "#4ea1ff"))
        fill = QColor(colour)
        fill.setAlpha(60 if self.isSelected() else 35)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(colour, 3 if self.isSelected() else 2,
                            Qt.SolidLine if self.isSelected() else Qt.DashLine))
        rect = self.rect()
        painter.drawRect(rect)

        # The number the prompt cites it by, in the corner.
        if self.number:
            font = QFont(painter.font())
            font.setPointSizeF(13)
            font.setBold(True)
            painter.setFont(font)
            tag = QRectF(rect.left(), rect.top(), 30 + 9 * len(self.number), 24)
            painter.fillRect(tag, colour)
            painter.setPen(QColor("#10131a"))
            painter.drawText(tag, Qt.AlignCenter, self.number)

        corner = rect.bottomRight()
        painter.fillRect(QRectF(corner.x() - 10, corner.y() - 10, 10, 10), colour)


class _Canvas(QGraphicsView):
    """The view boxes are drawn in. A drag on empty canvas makes a new box."""

    drawn = Signal(QRectF)

    def __init__(self, scene: QGraphicsScene, bounds: QRectF):
        super().__init__(scene)
        self._bounds = bounds
        self._start: QPointF | None = None
        self._ghost: QGraphicsRectItem | None = None
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumSize(520, 420)
        # The canvas is always fitted to the view, so there is nothing to scroll.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor(theme.BG))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self._bounds, Qt.KeepAspectRatio)

    def showEvent(self, event):
        super().showEvent(event)
        self.fitInView(self._bounds, Qt.KeepAspectRatio)

    def _clamp(self, point: QPointF) -> QPointF:
        return QPointF(min(max(point.x(), self._bounds.left()), self._bounds.right()),
                       min(max(point.y(), self._bounds.top()), self._bounds.bottom()))

    def mousePressEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        hit = self.itemAt(event.position().toPoint())
        if event.button() == Qt.LeftButton and not isinstance(hit, _BoxItem) \
                and self._bounds.contains(point):
            self.scene().clearSelection()
            self._start = self._clamp(point)
            self._ghost = self.scene().addRect(
                QRectF(self._start, self._start),
                QPen(QColor("#ffffff"), 2, Qt.DashLine))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start is not None and self._ghost is not None:
            end = self._clamp(self.mapToScene(event.position().toPoint()))
            self._ghost.setRect(QRectF(self._start, end).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._start is not None and self._ghost is not None:
            rect = self._ghost.rect()
            self.scene().removeItem(self._ghost)
            self._ghost, self._start = None, None
            if rect.width() >= _MIN_SIDE and rect.height() >= _MIN_SIDE:
                self.drawn.emit(rect)
            return
        super().mouseReleaseEvent(event)


class BoxEditor(QDialog):
    """The box-drawing window. ``boxes()`` holds the result after OK."""

    def __init__(self, boxes: list[Box], aspect: tuple[int, int] = (1, 1),
                 background: QPixmap | None = None, allow_character: bool = False,
                 cites_objects: bool = False, note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("Layout boxes"))
        self.resize(1080, 680)
        self._kinds = [k for k in KINDS if allow_character or k != "character"]
        self._items: list[_BoxItem] = []
        self._descs: dict[_BoxItem, str] = {}
        self._texts: dict[_BoxItem, str] = {}
        #: Colours a MOSS layout gave a box; not edited here, only kept.
        self._palettes: dict[_BoxItem, tuple[str, ...]] = {}
        self._loading = False

        # The canvas keeps the picture's shape, its longer side _SCENE_SIDE.
        if background is not None and not background.isNull():
            aspect = (background.width(), background.height())
        aw, ah = max(1, aspect[0]), max(1, aspect[1])
        scale = _SCENE_SIDE / max(aw, ah)
        self._bounds = QRectF(0, 0, aw * scale, ah * scale)

        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(self._bounds.adjusted(-20, -20, 20, 20))
        if background is not None and not background.isNull():
            picture = QGraphicsPixmapItem(background.scaled(
                int(self._bounds.width()), int(self._bounds.height()),
                Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            picture.setZValue(-10)
            self.scene.addItem(picture)
        else:
            page = self.scene.addRect(self._bounds, QPen(QColor("#6b7280"), 2),
                                      QBrush(QColor("#f4f1ea")))
            page.setZValue(-10)
        self.scene.selectionChanged.connect(self._on_selection)

        self.canvas = _Canvas(self.scene, self._bounds)
        self.canvas.drawn.connect(self._on_drawn)

        # -- the side panel ------------------------------------------------
        side = QWidget()
        panel = QVBoxLayout(side)
        panel.setContentsMargins(0, 0, 0, 0)

        hint = t("Drag on the picture to draw a box. Draw the background first "
                 "and the things in front last.")
        if cites_objects:
            hint += "\n\n" + t("In your prompt, call a box by its number: "
                               "<obj_1>, <obj_2>… The first picture is <ref_image_0>.")
        self.hint = QLabel(hint)
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        panel.addWidget(self.hint)
        if note:
            self.note = QLabel("⚠  " + note)
            self.note.setWordWrap(True)
            self.note.setStyleSheet(f"color:{theme.WARN};")
            panel.addWidget(self.note)

        self.listing = QListWidget()
        self.listing.currentRowChanged.connect(self._on_row)
        panel.addWidget(self.listing, 1)

        form = QFormLayout()
        self.kind_combo = QComboBox()
        for kind in self._kinds:
            self.kind_combo.addItem(t(_KIND_LABELS[kind]), kind)
        self.kind_combo.currentIndexChanged.connect(self._on_edit)
        form.addRow(t("Kind"), self.kind_combo)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText(t("What is in this box - e.g. a red cat sitting"))
        self.desc_edit.setFixedHeight(84)
        self.desc_edit.textChanged.connect(self._on_edit)
        form.addRow(t("Description"), self.desc_edit)
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText(t("The exact words to show"))
        self.text_edit.textChanged.connect(self._on_edit)
        self.text_label = QLabel(t("Text"))
        form.addRow(self.text_label, self.text_edit)
        panel.addLayout(form)

        row = QHBoxLayout()
        self.up_btn = QPushButton(t("Move back"))
        self.up_btn.setToolTip(t("Draw this box earlier, behind the ones after it"))
        self.up_btn.clicked.connect(lambda: self._move(-1))
        row.addWidget(self.up_btn)
        self.down_btn = QPushButton(t("Move forward"))
        self.down_btn.setToolTip(t("Draw this box later, in front of the ones before it"))
        self.down_btn.clicked.connect(lambda: self._move(1))
        row.addWidget(self.down_btn)
        panel.addLayout(row)
        row = QHBoxLayout()
        self.delete_btn = QPushButton(t("Delete box"))
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete)
        row.addWidget(self.delete_btn)
        self.clear_btn = QPushButton(t("Clear all"))
        self.clear_btn.clicked.connect(self._clear)
        row.addWidget(self.clear_btn)
        panel.addLayout(row)

        body = QHBoxLayout()
        body.addWidget(self.canvas, 3)
        body.addWidget(side, 2)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addLayout(body, 1)
        outer.addWidget(buttons)

        for box in boxes:
            box = box.clamped()
            rect = QRectF(box.x * self._bounds.width(), box.y * self._bounds.height(),
                          box.w * self._bounds.width(), box.h * self._bounds.height())
            self._add(rect, box.kind if box.kind in self._kinds else "object",
                      box.desc, box.text, select=False, palette=box.palette)
        self._refresh()

    # -- boxes -------------------------------------------------------------
    def _add(self, rect: QRectF, kind: str, desc: str = "", text: str = "",
             palette: tuple[str, ...] = (),
             select: bool = True) -> _BoxItem:
        item = _BoxItem(rect, kind, self._bounds, self)
        item.setZValue(len(self._items))
        self.scene.addItem(item)
        self._items.append(item)
        self._descs[item] = desc
        self._texts[item] = text
        self._palettes[item] = tuple(palette)
        if select:
            self.scene.clearSelection()
            item.setSelected(True)
        return item

    def _on_drawn(self, rect: QRectF) -> None:
        self._add(rect, "object")
        self._refresh()
        self.desc_edit.setFocus()

    def _selected(self) -> _BoxItem | None:
        for item in self._items:
            if item.isSelected():
                return item
        return None

    def _move(self, step: int) -> None:
        item = self._selected()
        if item is None:
            return
        index = self._items.index(item)
        target = index + step
        if not 0 <= target < len(self._items):
            return
        self._items[index], self._items[target] = self._items[target], self._items[index]
        self._refresh()

    def _delete(self) -> None:
        item = self._selected()
        if item is None:
            return
        self._items.remove(item)
        self._descs.pop(item, None)
        self._texts.pop(item, None)
        self._palettes.pop(item, None)
        self.scene.removeItem(item)
        self._refresh()

    def _clear(self) -> None:
        for item in list(self._items):
            self.scene.removeItem(item)
        self._items.clear()
        self._descs.clear()
        self._texts.clear()
        self._palettes.clear()
        self._refresh()

    def geometry_changed(self) -> None:
        """A box moved or changed size; nothing in the list depends on it."""

    # -- the list and the form ---------------------------------------------
    def _numbers(self) -> list[str]:
        return [str(n) for n in numbers(self._items)]

    def _refresh(self) -> None:
        """Renumber, restack and relist the boxes after any change."""
        self._loading = True
        selected = self._selected()
        self.listing.clear()
        for index, (item, number) in enumerate(zip(self._items, self._numbers())):
            item.number = number
            item.setZValue(index)
            item.update()
            desc = self._descs.get(item, "").strip() or t("(no description yet)")
            if item.kind == "text" and self._texts.get(item):
                desc = f"“{self._texts[item]}”  {desc}"
            self.listing.addItem(f"{number}  ·  {t(_KIND_LABELS[item.kind])}  —  {desc}")
        if selected in self._items:
            self.listing.setCurrentRow(self._items.index(selected))
        self._loading = False
        self._load_form(selected)

    def _on_row(self, row: int) -> None:
        if self._loading or not 0 <= row < len(self._items):
            return
        self.scene.blockSignals(True)
        self.scene.clearSelection()
        self._items[row].setSelected(True)
        self.scene.blockSignals(False)
        self._load_form(self._items[row])

    def _on_selection(self) -> None:
        item = self._selected()
        if item in self._items:
            self._loading = True
            self.listing.setCurrentRow(self._items.index(item))
            self._loading = False
        self._load_form(item)

    def _load_form(self, item: _BoxItem | None) -> None:
        self._loading = True
        has = item is not None
        for widget in (self.kind_combo, self.desc_edit, self.text_edit,
                       self.up_btn, self.down_btn, self.delete_btn):
            widget.setEnabled(has)
        if has:
            self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(item.kind)))
            if self.desc_edit.toPlainText() != self._descs.get(item, ""):
                self.desc_edit.setPlainText(self._descs.get(item, ""))
            self.text_edit.setText(self._texts.get(item, ""))
            self.text_edit.setEnabled(item.kind == "text")
        else:
            self.desc_edit.setPlainText("")
            self.text_edit.setText("")
        self.clear_btn.setEnabled(bool(self._items))
        self._loading = False

    def _on_edit(self) -> None:
        if self._loading:
            return
        item = self._selected()
        if item is None:
            return
        item.kind = self.kind_combo.currentData() or "object"
        self._descs[item] = self.desc_edit.toPlainText()
        self._texts[item] = self.text_edit.text()
        self.text_edit.setEnabled(item.kind == "text")
        # Relist without disturbing the text being typed.
        row = self._items.index(item)
        numbers = self._numbers()
        for index, other in enumerate(self._items):
            other.number = numbers[index]
            other.update()
        desc = self._descs[item].strip() or t("(no description yet)")
        if item.kind == "text" and self._texts[item]:
            desc = f"“{self._texts[item]}”  {desc}"
        entry = self.listing.item(row)
        if entry is not None:
            entry.setText(f"{numbers[row]}  ·  {t(_KIND_LABELS[item.kind])}  —  {desc}")

    # -- the result --------------------------------------------------------
    def boxes(self) -> list[Box]:
        w, h = self._bounds.width(), self._bounds.height()
        found = []
        for item in self._items:
            rect = item.scene_rect()
            found.append(Box(rect.x() / w, rect.y() / h, rect.width() / w,
                             rect.height() / h, item.kind,
                             self._descs.get(item, "").strip(),
                             self._texts.get(item, "").strip(),
                             self._palettes.get(item, ())).clamped())
        return found
