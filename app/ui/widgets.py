"""Reusable pieces of the generate screen.

* :class:`RatioPicker`   - aspect ratio chooser that shows the real pixel size.
* :class:`DropZone`      - click-or-drag file input for pictures, sound, video.
* :class:`WorkflowList`  - the left-hand list of workflow cards with readiness.
* :class:`PreviewPane`   - live preview during a run, final result after it.
* :class:`ResultGallery` - thumbnails / player for everything made this session.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QFontMetrics, QImage, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QHBoxLayout,
    QLabel, QLayout, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from app.i18n import t
from app.ratios import (
    MEGAPIXEL_MAX, MEGAPIXEL_MIN, MEGAPIXEL_STEP, RATIO_ORDER,
    clamp_megapixels, resolve, snap_to_output_grid, solve_for,
)
from app.ui import theme
from app.workflows.loader import Workflow

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
_AUDIO_EXT = {".flac", ".mp3", ".wav", ".ogg", ".m4a"}


def open_in_explorer(path: str | Path) -> None:
    """Open the file manager with this file selected.

    Explorer wants the switch and the path as a single argument -
    ``/select,C:\\pictures\\a.png``. Passing them as two arguments makes it
    ignore both and open Documents instead. If the file has gone, fall back to
    showing the folder rather than opening the wrong window.
    """
    path = Path(path)
    if not path.exists():
        open_folder(path.parent)
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError:
        open_folder(path.parent)


def open_folder(path: str | Path) -> None:
    """Open a folder itself, rather than selecting something inside it."""
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer "{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


def _square(pixmap: QPixmap, side: int) -> QPixmap:
    """Centre a picture on a transparent square of the given size."""
    scaled = pixmap.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QPixmap(side, side)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap((side - scaled.width()) // 2,
                       (side - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


class FlowLayout(QLayout):
    """Lays widgets left to right, wrapping onto a new line when out of room.

    Qt has no such layout. The shape chips need one: nine of them do not fit
    across the middle column, and a horizontal layout would either squash them
    or force a scrollbar.
    """

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect, apply: bool) -> int:
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


# --------------------------------------------------------------------------
class RatioPicker(QWidget):
    """Picks a shape, and shows what that means in pixels for this model.

    A row of chips rather than a dropdown: every shape a workflow can make is
    worth seeing at a glance, and the pixel size for the chosen one is spelled
    out underneath in the monospace face so it does not jitter as it changes.
    """

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine: str | None = None
        #: Restricts the list when the workflow chooses its size through a node
        #: that only knows certain ratios. None means offer everything.
        self._allowed: list[str] | None = None
        #: (megapixels, multiple) when a chooser node decides the pixels, so
        #: the label matches the file the user will actually get.
        self._profile: tuple[float, int] | None = None
        #: Chosen pixel budget, overriding whatever the engine or node says.
        self._megapixels: float | None = None
        self._ratio: str | None = None
        self._chips: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.heading = QLabel(t("SHAPE"))
        self.heading.setObjectName("Heading")
        layout.addWidget(self.heading)

        self._chip_holder = QWidget()
        self._chip_flow = FlowLayout(self._chip_holder, spacing=6)
        layout.addWidget(self._chip_holder)

        self.detail = QLabel("")
        self.detail.setObjectName("Mono")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.buttonClicked.connect(self._on_chip)

        self.set_engine(None)

    def set_engine(self, engine: str | None) -> None:
        """Re-label every option for the selected model's resolution rules."""
        self._engine = engine
        self._repopulate()

    def set_allowed(self, ratios: list[str] | None) -> None:
        """Limit the list to what this workflow can actually produce."""
        self._allowed = list(ratios) if ratios else None
        self._repopulate()

    def set_node_profile(self, profile: tuple[float, int] | None) -> None:
        """Use a chooser node's own megapixels and rounding for the labels."""
        self._profile = profile
        self._repopulate()

    def set_megapixels(self, megapixels: float | None) -> None:
        """Re-label for a different pixel budget."""
        self._megapixels = megapixels
        self._repopulate()

    def _size_for(self, ratio: str) -> tuple[int, int]:
        if self._profile:
            budget, multiple = self._profile
            size = solve_for(ratio, self._megapixels or budget, multiple)
        else:
            size = resolve(ratio, self._engine, megapixels=self._megapixels)
        return snap_to_output_grid(size, self._engine)

    def _repopulate(self) -> None:
        offered = [r for r in RATIO_ORDER
                   if self._allowed is None or r in self._allowed]
        if not offered:
            offered = list(RATIO_ORDER)

        if list(self._chips) != offered:
            for chip in self._chips.values():
                self._group.removeButton(chip)
                self._chip_flow.removeWidget(chip)
                # Unparent now: deleteLater runs after the next repaint, and
                # until then the old chips stay visible behind the new ones.
                chip.setParent(None)
                chip.deleteLater()
            self._chips.clear()

            for ratio in offered:
                chip = QPushButton(ratio)
                chip.setObjectName("Chip")
                chip.setCheckable(True)
                chip.setCursor(Qt.PointingHandCursor)
                self._chips[ratio] = chip
                self._group.addButton(chip)
                self._chip_flow.addWidget(chip)

        for ratio, chip in self._chips.items():
            w, h = self._size_for(ratio)
            chip.setToolTip(t("{w} × {h} pixels", w=w, h=h))

        wanted = self._ratio if self._ratio in offered else (offered[0] if offered else None)
        if wanted:
            self.set_ratio(wanted)

    def set_ratio(self, ratio: str) -> None:
        if ratio not in self._chips:
            return
        self._ratio = ratio
        for key, chip in self._chips.items():
            chip.setChecked(key == ratio)
        self._update_detail()

    def current_ratio(self) -> str | None:
        return self._ratio

    def current_size(self) -> tuple[int, int]:
        return self._size_for(self._ratio or "1:1")

    def set_enabled_with_reason(self, enabled: bool, reason: str = "") -> None:
        """Grey out when the workflow decides its own size."""
        self._chip_holder.setEnabled(enabled)
        self.setEnabled(enabled)
        self._chip_holder.setToolTip(reason)
        if enabled:
            self._update_detail()
        else:
            self.detail.setText(reason)

    def _on_chip(self, chip) -> None:
        for ratio, candidate in self._chips.items():
            if candidate is chip:
                self._ratio = ratio
                break
        self._update_detail()
        self.changed.emit(self._ratio or "")

    def _update_detail(self) -> None:
        if not self._chip_holder.isEnabled():
            return
        w, h = self.current_size()
        self.detail.setText(t("{w} × {h} px", w=w, h=h))

    def retranslate(self) -> None:
        self.heading.setText(t("SHAPE"))
        for ratio, chip in self._chips.items():
            w, h = self._size_for(ratio)
            chip.setToolTip(t("{w} × {h} pixels", w=w, h=h))
        if self._chip_holder.isEnabled():
            self._update_detail()


# --------------------------------------------------------------------------
class DurationPicker(QWidget):
    """How long the video should be, in seconds.

    Workflows store this differently - most compute a frame count from a
    seconds value, a few take frames directly - so the manifest says which, and
    this converts. The user only ever sees seconds.

    Longer clips scale VRAM roughly linearly, and running out of memory is the
    most common way a video job dies, so the warning is shown before the run
    rather than explained afterwards.
    """

    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._unit = "seconds"
        self._fps = 24
        self._warn_at = 8
        self._warning_active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.heading = QLabel(t("Length"))
        self.heading.setObjectName("Heading")
        layout.addWidget(self.heading)

        self.spin = QSpinBox()
        self.spin.setSuffix(t(" seconds"))
        self.spin.setRange(2, 15)
        self.spin.valueChanged.connect(self._on_change)
        layout.addWidget(self.spin)

        self.detail = QLabel("")
        self.detail.setObjectName("Hint")
        layout.addWidget(self.detail)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        layout.addWidget(self.warning)

    def configure(self, manifest, cfg) -> None:
        """Set the range and units from the workflow and the user's settings."""
        self._unit = getattr(manifest, "length_unit", "seconds") or "seconds"
        self._fps = int(getattr(manifest, "fps", None) or 24)
        low = int(cfg.get("video_length_min") or 2)
        high = int(cfg.get("video_length_max") or 15)
        self._warn_at = int(cfg.get("video_length_warn") or 8)

        self.spin.blockSignals(True)
        self.spin.setRange(low, high)
        self.spin.setValue(int(cfg.get("video_length_default") or 5))
        self.spin.blockSignals(False)
        self._refresh()

    def seconds(self) -> int:
        return self.spin.value()

    def is_warning(self) -> bool:
        """Whether the memory caution is showing.

        Separate from the label's isVisible(), which is False whenever the tab
        itself is not on screen.
        """
        return self._warning_active

    def value_for_workflow(self) -> int:
        """What to write into the workflow: seconds, or frames if it wants those."""
        if self._unit == "frames":
            return max(1, round(self.spin.value() * self._fps))
        return self.spin.value()

    def _on_change(self) -> None:
        self._refresh()
        self.changed.emit(self.spin.value())

    def _refresh(self) -> None:
        seconds = self.spin.value()
        frames = round(seconds * self._fps)
        self.detail.setText(t("about {frames} frames at {fps} per second",
                              frames=frames, fps=self._fps))

        over = seconds > self._warn_at
        self._warning_active = over
        self.warning.setVisible(over)
        if over:
            self.warning.setText(t(
                "⚠  {seconds} seconds is a long clip. Long videos use a lot of "
                "graphics memory and the job may stop with an out-of-memory "
                "error. If that happens, try {limit} seconds or fewer, or a "
                "smaller shape.", seconds=seconds, limit=self._warn_at))
            self.warning.setStyleSheet(f"color:{theme.WARN};font-size:12px;")

    def retranslate(self) -> None:
        self.heading.setText(t("Length"))
        self.spin.setSuffix(t(" seconds"))
        self._refresh()


class MegapixelPicker(QWidget):
    """How many pixels the picture should have in total.

    Separate from the shape: the ratio decides the proportions, this decides
    how big. Kept in megapixels rather than width and height because that is
    what stays meaningful across every shape, and it is the unit the
    size-chooser nodes already use.
    """

    changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._warning_active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # The label and the current value share a line, so the number reads as
        # part of the heading rather than as another control.
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel(t("DETAIL"))
        self.heading.setObjectName("Heading")
        head.addWidget(self.heading)
        head.addStretch(1)
        self.value_label = QLabel("1.0 MP")
        self.value_label.setObjectName("MonoAccent")
        head.addWidget(self.value_label)
        layout.addLayout(head)

        # A slider, because this is a judgement between "quick" and "detailed"
        # rather than a number anyone types exactly.
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.setToolTip(
            "How many pixels the picture has in total.\n\n"
            "1.0 is what these models are trained for and is the safe choice. "
            "Lower is quicker; higher gives more detail but takes longer and "
            "uses more graphics memory.")
        self.slider.valueChanged.connect(self._on_change)
        layout.addWidget(self.slider)

        # Kept so anything holding a reference to .spin still works.
        self.spin = QDoubleSpinBox()
        self.spin.setRange(MEGAPIXEL_MIN, MEGAPIXEL_MAX)
        self.spin.setSingleStep(MEGAPIXEL_STEP)
        self.spin.setDecimals(2)
        self.spin.setVisible(False)
        self.spin.valueChanged.connect(self._on_spin)

        self.detail = QLabel("")
        self.detail.setObjectName("Hint")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

    def configure(self, cfg, mode_key: str = "image",
                  current: float | None = None) -> None:
        """Set up for one workflow.

        ``current`` is the value the workflow itself carries. It wins over the
        remembered setting, so opening a workflow and pressing Create makes what
        its author intended - several of these are built at 0.4 or 0.5, and
        silently pushing them to 1.0 would change the result and could exhaust
        the card.
        """
        self._mode_key = mode_key
        self._warn_above = float(cfg.get(f"{mode_key}_megapixels_warn") or 1.5)
        # A workflow built bigger than that - MOSS-Image is made at 2 - is not
        # "bigger than it was built for" at its own size.
        if current is not None:
            self._warn_above = max(self._warn_above, float(current))
        remembered = cfg.get(f"{mode_key}_megapixels") or 1.0
        value = float(current if current is not None else remembered)

        # The offered range is 0.5-2.0, but a workflow built below that keeps
        # its own floor. Several of these video workflows sit at 0.4, and
        # rounding them up to the nearest allowed value would quietly change
        # the result the author tuned.
        low = min(MEGAPIXEL_MIN, value) if current is not None else MEGAPIXEL_MIN
        high = max(MEGAPIXEL_MAX, value)
        value = min(high, max(low, value))

        # The slider counts in hundredths of a megapixel; the step is a tenth.
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setRange(int(round(low * 100)), int(round(high * 100)))
        self.slider.setSingleStep(int(MEGAPIXEL_STEP * 100))
        self.slider.setPageStep(int(MEGAPIXEL_STEP * 100))
        self.slider.setValue(int(round(value * 100)))
        self.spin.setRange(round(low, 2), round(high, 2))
        self.spin.setValue(round(value, 2))
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)
        self._refresh()

    def megapixels(self) -> float:
        # Not clamp_megapixels: the control may legitimately sit below the
        # normal floor when the workflow itself was built that way.
        return round(self.slider.value() / 100, 2)

    def set_megapixels(self, value: float) -> None:
        self.slider.setValue(int(round(float(value) * 100)))

    def is_warning(self) -> bool:
        return self._warning_active

    def _on_change(self) -> None:
        self.spin.blockSignals(True)
        self.spin.setValue(self.megapixels())
        self.spin.blockSignals(False)
        self._refresh()
        self.changed.emit(self.megapixels())

    def _on_spin(self) -> None:
        self.slider.setValue(int(round(self.spin.value() * 100)))

    def _refresh(self) -> None:
        value = self.megapixels()
        self.value_label.setText(t("{n} MP", n=f"{value:.1f}"))
        over = value > getattr(self, "_warn_above", 1.5)
        self._warning_active = over
        if over:
            # Video costs pixels x frames, so the same number bites much
            # harder there and the warning has to say so.
            extra = (t(" Video is worst hit, because every frame costs this much.")
                     if getattr(self, "_mode_key", "image") == "video" else "")
            self.detail.setText(t(
                "⚠  Bigger than this workflow was built for. Slower, and it "
                "may run out of graphics memory.") + extra)
            self.detail.setStyleSheet(f"color:{theme.WARN};font-size:12px;")
        else:
            self.detail.setText(
                t("1.0 is the normal size") if abs(value - 1.0) < 0.05
                else (t("smaller and quicker") if value < 1.0
                      else t("larger, more detail")))
            self.detail.setStyleSheet("")

    def retranslate(self) -> None:
        self.heading.setText(t("DETAIL"))
        self._refresh()


class ColumnScroll(QScrollArea):
    """Scrolls up and down only, and is never narrower than what it holds.

    A plain scroll area reports almost no minimum width, so a splitter squeezes
    it and the right-hand edge of its contents - buttons, borders - is cut off
    with no way to scroll to it. Reporting the contents' own minimum width
    keeps the column as wide as it was before it scrolled.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        inner = self.widget()
        if inner is None:
            return hint
        width = (inner.minimumSizeHint().width()
                 + self.verticalScrollBar().sizeHint().width()
                 + 2 * self.frameWidth())
        return QSize(max(width, hint.width()), hint.height())


class Switch(QCheckBox):
    """A tick box drawn as a sliding switch.

    Still a QCheckBox underneath - isChecked, setChecked and toggled all behave
    normally - so nothing that uses one has to know it looks different.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QCheckBox{{color:{theme.MUTED};font-size:13px;spacing:10px;}}"
            f"QCheckBox:hover{{color:{theme.TEXT};}}"
            f"QCheckBox::indicator{{width:34px;height:19px;border-radius:10px;"
            f"border:1px solid {theme.LINE_2};background:{theme.SURFACE_2};}}"
            f"QCheckBox::indicator:checked{{background:{theme.ACCENT};"
            f"border-color:{theme.ACCENT};}}")


class DropZone(QFrame):
    """A click-or-drop target for one input file."""

    changed = Signal(str)   # empty string when cleared

    def __init__(self, label: str, kinds: set[str], compact: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setAcceptDrops(True)
        self._label = label
        self._kinds = kinds
        self._path: str = ""
        self._compact = compact

        # Compact zones let a workflow that wants ten reference pictures still
        # show several at once without pushing the Create button off screen.
        # Two lines of text plus padding; the earlier heights cropped the
        # "click to choose" caption once the theme grew its type.
        thumb_size = 42 if compact else 60
        self.setFixedHeight(72 if compact else 96)

        layout = QHBoxLayout(self)
        margin = 6 if compact else 10
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(8 if compact else 10)

        self.thumb = QLabel()
        self.thumb.setFixedSize(thumb_size, thumb_size)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(
            f"background:{theme.BG_INPUT};border-radius:6px;color:{theme.TEXT_DIM};")
        layout.addWidget(self.thumb)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title = QLabel(label)
        self.title.setObjectName("Heading")
        self.caption = QLabel(t("Click to choose, or drag one here"))
        self.caption.setObjectName("Hint")
        self.caption.setWordWrap(not compact)
        text_col.addWidget(self.title)
        text_col.addWidget(self.caption)
        layout.addLayout(text_col, 1)

        self.clear_btn = QPushButton(t("Remove"))
        self.clear_btn.setVisible(False)
        self.clear_btn.clicked.connect(lambda: self.set_path(""))
        layout.addWidget(self.clear_btn)

        self._reset_thumb()

    # -- state -------------------------------------------------------------
    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = path or ""
        if self._path:
            name = Path(self._path).name
            self.caption.setText(name if len(name) <= 42 else name[:20] + "…" + name[-18:])
            self.caption.setToolTip(self._path)
            self.clear_btn.setVisible(True)
            self._show_thumb(self._path)
        else:
            self.caption.setText(t("Click to choose, or drag one here"))
            self.caption.setToolTip("")
            self.clear_btn.setVisible(False)
            self._reset_thumb()
        self.changed.emit(self._path)

    def retranslate(self) -> None:
        self.clear_btn.setText(t("Remove"))
        if not self._path:
            self.caption.setText(t("Click to choose, or drag one here"))

    def _reset_thumb(self) -> None:
        icon = "🖼" if _IMAGE_EXT & self._kinds else ("🎵" if _AUDIO_EXT & self._kinds else "🎬")
        self.thumb.setPixmap(QPixmap())
        self.thumb.setText(icon)

    def _show_thumb(self, path: str) -> None:
        if Path(path).suffix.lower() in _IMAGE_EXT:
            pix = QPixmap(path)
            if not pix.isNull():
                side = self.thumb.width()
                self.thumb.setText("")
                self.thumb.setPixmap(pix.scaled(side, side, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation))
                return
        self._reset_thumb()

    # -- interaction -------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        patterns = " ".join(f"*{e}" for e in sorted(self._kinds))
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choose a {self._label.lower()}", "", f"Files ({patterns})")
        if path:
            self.set_path(path)

    def dragEnterEvent(self, event) -> None:
        if self._first_accepted(event):
            event.acceptProposedAction()
            self.setStyleSheet(f"QFrame#Panel {{ border: 2px solid {theme.ACCENT}; }}")

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet("")

    def dropEvent(self, event) -> None:
        self.setStyleSheet("")
        path = self._first_accepted(event)
        if path:
            self.set_path(path)
            event.acceptProposedAction()

    def _first_accepted(self, event) -> str:
        if not event.mimeData().hasUrls():
            return ""
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and Path(local).suffix.lower() in self._kinds:
                return local
        return ""


# --------------------------------------------------------------------------
class _WorkflowCard(QWidget):
    """Two lines: the name, and a status dot with a short machine-ish note.

    A widget rather than styled item text so the name can ellipsize on its own
    line - the earlier version let long names force a horizontal scrollbar
    across the whole list.
    """

    def __init__(self, workflow: Workflow, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(9)

        colour = {"✅": theme.OK, "⚠": theme.WARN}.get(workflow.status_icon(), theme.BAD)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{colour};font-size:11px;")
        dot.setFixedWidth(10)
        layout.addWidget(dot, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        name = QLabel(workflow.name)
        name.setStyleSheet(
            f"font-size:13px;font-weight:600;color:"
            f"{theme.TEXT if workflow.ready else theme.MUTED};")
        name.setMinimumWidth(1)          # let it shrink and ellipsize
        column.addWidget(name)

        status = QLabel(_card_status(workflow))
        status.setObjectName("Mono")
        status.setStyleSheet(
            f"font-family:'{theme.MONO_FONT}';font-size:10px;"
            f"color:{theme.DIM if workflow.ready else theme.WARN};"
            f"letter-spacing:0.5px;")
        status.setMinimumWidth(1)
        column.addWidget(status)
        layout.addLayout(column, 1)

        self._name = name
        self._status = status
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide(self._name)
        self._elide(self._status)

    def _elide(self, label: QLabel) -> None:
        text = label.property("full") or label.text()
        label.setProperty("full", text)
        metrics = QFontMetrics(label.font())
        label.setText(metrics.elidedText(text, Qt.ElideRight, max(40, label.width())))


def _card_status(workflow: Workflow) -> str:
    """The short line under a workflow's name."""
    if not workflow.loadable:
        return "NEEDS RE-EXPORT"
    if workflow.missing_nodes:
        return "MISSING ADD-ON"
    if workflow.missing_models:
        return "MISSING MODEL"
    if not workflow.checked:
        return "NOT CHECKED"
    if workflow.needs_attention:
        return "CHECK SET UP"
    kind = (workflow.manifest.engine or "").upper() if workflow.manifest else ""
    return f"READY · {kind}" if kind else "READY"


class WorkflowList(QListWidget):
    """The list of available workflows, with a readiness badge on each."""

    picked = Signal(object)        # Workflow
    rename_requested = Signal(object)
    setup_requested = Signal(object)
    reveal_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(56, 56))
        self.setSpacing(0)
        self.currentItemChanged.connect(self._on_pick)
        self.itemDoubleClicked.connect(
            lambda item: self.rename_requested.emit(item.data(Qt.UserRole)))

        # Renaming is the thing people want most often, so it gets the
        # standard shortcut as well as the menu.
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_menu)
        rename = QShortcut(QKeySequence(Qt.Key_F2), self)
        rename.activated.connect(self._rename_current)

    def _rename_current(self) -> None:
        workflow = self.current_workflow()
        if workflow:
            self.rename_requested.emit(workflow)

    def _on_menu(self, point) -> None:
        item = self.itemAt(point)
        if not item:
            return
        workflow = item.data(Qt.UserRole)
        self.setCurrentItem(item)

        menu = QMenu(self)
        menu.addAction("Rename…\tF2", lambda: self.rename_requested.emit(workflow))
        menu.addAction("Set up…", lambda: self.setup_requested.emit(workflow))
        menu.addSeparator()
        menu.addAction("Show the file", lambda: self.reveal_requested.emit(workflow))
        menu.exec(self.viewport().mapToGlobal(point))

    def set_workflows(self, workflows: list[Workflow]) -> None:
        previous = self.current_workflow()
        self.blockSignals(True)
        self.clear()
        for wf in workflows:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, wf)
            item.setSizeHint(QSize(0, 54))
            item.setToolTip(wf.error or wf.status_text())
            self.addItem(item)
            self.setItemWidget(item, _WorkflowCard(wf))
        self.blockSignals(False)

        # Keep the user's selection across a refresh where possible.
        target = 0
        if previous:
            for i in range(self.count()):
                if self.item(i).data(Qt.UserRole).path == previous.path:
                    target = i
                    break
        if self.count():
            self.setCurrentRow(target)

    def current_workflow(self) -> Workflow | None:
        item = self.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_pick(self, current, _previous) -> None:
        if current:
            self.picked.emit(current.data(Qt.UserRole))


# --------------------------------------------------------------------------
class PreviewPane(QLabel):
    """Shows ComfyUI's live preview while running, then the finished picture."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"background:{theme.BG_INPUT};border:1px solid {theme.BORDER};"
            f"border-radius:10px;color:{theme.TEXT_DIM};")
        self._pixmap: QPixmap | None = None
        #: The video whose frame we are waiting for. Guards against a slow
        #: grab landing after the user has moved on to something else.
        self._awaiting: str | None = None
        self.clear_preview()

    def clear_preview(self, message: str = "Your picture will appear here") -> None:
        self._pixmap = None
        self.setText(message)

    def show_bytes(self, data: bytes) -> None:
        image = QImage.fromData(data)
        if not image.isNull():
            self.show_pixmap(QPixmap.fromImage(image))

    def show_file(self, path: str | Path) -> None:
        pix = QPixmap(str(path))
        if not pix.isNull():
            self.show_pixmap(pix)
        else:
            self.clear_preview(f"Saved:\n{Path(path).name}")

    def show_video(self, path: str | Path) -> None:
        """Show a video's opening frame, so results are comparable at a glance.

        Until it arrives the filename is shown, which is what this pane used to
        say permanently. Decoding is asynchronous, so a slow or unreadable file
        never holds up the window.
        """
        from app.ui.video import grabber

        path = str(path)
        self._awaiting = path
        found = grabber().cached(path)
        if found is not None:
            self._show_frame(path, *found)
            return

        self.clear_preview(t("Saved {name}", name=Path(path).name))
        source = grabber()
        source.ready.connect(self._on_frame_ready)
        source.request(path)

    def _on_frame_ready(self, path: str, image: QImage, info) -> None:
        if path == getattr(self, "_awaiting", None):
            self._show_frame(path, image, info)

    def _show_frame(self, path: str, image: QImage, info) -> None:
        self.show_pixmap(QPixmap.fromImage(image))
        self.setToolTip(f"{Path(path).name}\n{info.summary()}")

    def show_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self._awaiting = None
        self.setText("")
        self._rescale()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap and not self._pixmap.isNull():
            super().setPixmap(self._pixmap.scaled(
                self.size() - QSize(8, 8), Qt.KeepAspectRatio, Qt.SmoothTransformation))


# --------------------------------------------------------------------------
class ResultGallery(QWidget):
    """Everything made this session, newest first. Click to open."""

    selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.title = QLabel(t("Your results"))
        self.title.setObjectName("Heading")
        header.addWidget(self.title)
        header.addStretch(1)
        self.open_folder_btn = QPushButton(t("Open folder"))
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self._open_folder)
        header.addWidget(self.open_folder_btn)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setIconSize(QSize(52, 52))
        self.list.itemClicked.connect(self._on_click)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list, 1)

        self.empty_hint = QLabel(
            t("Nothing yet — press Create to make something."))
        self.empty_hint.setObjectName("Hint")
        self.empty_hint.setWordWrap(True)
        layout.addWidget(self.empty_hint)

        self._last_dir: Path | None = None
        self._folder: Path | None = None
        self._listening = False

    def add(self, path: str | Path) -> None:
        path = Path(path)
        item = QListWidgetItem(path.name)
        item.setData(Qt.UserRole, str(path))
        item.setSizeHint(QSize(0, 62))
        if path.suffix.lower() in _IMAGE_EXT:
            pix = QPixmap(str(path))
            if not pix.isNull():
                # Pad to a square so every row's text starts at the same place;
                # a tall 2:3 picture is otherwise much narrower than a wide one
                # and its filename crowds the thumbnail.
                item.setIcon(_square(pix, self.list.iconSize().width()))
        elif path.suffix.lower() in _VIDEO_EXT:
            # A real thumbnail rather than a symbol: this list is where several
            # takes get compared, so telling them apart matters most here.
            from app.ui.video import grabber

            item.setText("🎬  " + path.name)
            source = grabber()
            found = source.cached(str(path))
            if found is not None:
                self._set_frame(str(path), found[0], found[1])
            else:
                # Once only: this list is rebuilt whenever the Read tab is
                # opened, and a fresh connection per video would leave the
                # handler running once per rebuild.
                if not self._listening:
                    source.ready.connect(self._on_frame_ready)
                    self._listening = True
                source.request(str(path))
        elif path.suffix.lower() in _AUDIO_EXT:
            item.setText("🎵  " + path.name)
        else:
            # Prompt Helper saves its result as .txt, which is neither a
            # picture nor a sound - a music note in front of a written prompt
            # says nothing true about it.
            item.setText("📝  " + path.name)
        item.setToolTip(f"{path}\n\n" + t("Double-click to open it"))
        self.list.insertItem(0, item)
        self.list.setCurrentRow(0)
        self._last_dir = path.parent
        self.open_folder_btn.setEnabled(True)
        self.empty_hint.setVisible(False)

    def clear(self) -> None:
        """Empty the list so it can be rebuilt from a folder.

        Used by the Read tab, which shows what is on disk rather than what this
        session made, and so has to redraw when the folder changes.
        """
        self.list.clear()
        self._last_dir = None
        self.empty_hint.setVisible(True)

    def _on_frame_ready(self, path: str, image: QImage, info) -> None:
        self._set_frame(path, image, info)

    def _set_frame(self, path: str, image: QImage, info) -> None:
        """Fill in a video's thumbnail once it has been decoded."""
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) != path:
                continue
            item.setIcon(_square(QPixmap.fromImage(image),
                                 self.list.iconSize().width()))
            item.setText(Path(path).name)          # the symbol is now redundant
            if info and info.summary():
                item.setToolTip(f"{path}\n{info.summary()}\n\n"
                                + t("Double-click to open it"))
            return

    def retranslate(self) -> None:
        self.title.setText(t("Your results"))
        self.open_folder_btn.setText(t("Open folder"))
        self.empty_hint.setText(t("Nothing yet — press Create to make something."))

    def _on_click(self, item: QListWidgetItem) -> None:
        self.selected.emit(item.data(Qt.UserRole))

    def _on_double_click(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", path])
        except OSError:
            open_in_explorer(path)

    def set_folder(self, folder: str | Path) -> None:
        """Where this tab saves its results.

        Set up front so the button works before anything has been made, which
        is when someone is most likely to go looking for the folder.
        """
        self._folder = Path(folder)
        self.open_folder_btn.setEnabled(True)
        self.open_folder_btn.setToolTip(str(self._folder))

    def _open_folder(self) -> None:
        target = self._last_dir or getattr(self, "_folder", None)
        if target:
            open_folder(target)


# --------------------------------------------------------------------------
class ModelPicker(QWidget):
    """Choose a different model from the same folder as the workflow's own.

    A workflow names one model, but people collect variants - six Z-Image
    branches in one folder is normal. The list comes from the running ComfyUI,
    so only models it can actually load are ever offered.
    """

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._own = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.heading = QLabel(t("MODEL"))
        self.heading.setObjectName("Heading")
        layout.addWidget(self.heading)

        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_pick)
        layout.addWidget(self.combo)

    def set_options(self, options: list[str], own: str, chosen: str = "") -> bool:
        """Fill the list with the workflow's related models. Returns whether
        there is anything to show."""
        self._own = own
        self.combo.blockSignals(True)
        self.combo.clear()
        for option in options:
            label = Path(option.replace("\\", "/")).stem
            if option == own:
                # Always clear which one the workflow itself came with.
                label += "   " + t("(in the workflow)")
            self.combo.addItem(label, option)
        wanted = chosen if chosen in options else own
        index = self.combo.findData(wanted)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)

        # Every workflow with a model gets the control, even with only its own
        # model installed - it shows which model the workflow uses.
        worth_showing = bool(options)
        self.setVisible(worth_showing)
        return worth_showing

    def current(self) -> str:
        return self.combo.currentData() or ""

    def _on_pick(self) -> None:
        self.changed.emit(self.current())

    def retranslate(self) -> None:
        self.heading.setText(t("MODEL"))


class LoraPicker(QWidget):
    """An optional LoRA: none, or one of the files ComfyUI has, at a strength.

    Offered only where the workflow's author parked a LoRA loader at strength
    0 - a slot for "a LoRA of your own". "None" takes the loader out of the
    run, so nothing has to be installed for the workflow to work.
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.heading = QLabel(t("LORA (OPTIONAL)"))
        self.heading.setObjectName("Heading")
        layout.addWidget(self.heading)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_pick)
        row.addWidget(self.combo, 1)

        self.strength = QDoubleSpinBox()
        self.strength.setRange(-2.0, 2.0)
        self.strength.setSingleStep(0.05)
        self.strength.setDecimals(2)
        self.strength.setValue(1.0)
        self.strength.setPrefix(t("Strength") + "  ")
        self.strength.setToolTip(t(
            "How strongly the LoRA changes the picture. 1 is its full effect; "
            "lower values blend it in more gently."))
        self.strength.valueChanged.connect(lambda _v: self.changed.emit())
        row.addWidget(self.strength)
        layout.addLayout(row)

    def set_options(self, options: list[str], chosen: str = "",
                    strength: float = 1.0) -> None:
        self.combo.blockSignals(True)
        self.strength.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(t("None"), "")
        for option in options:
            self.combo.addItem(option.replace("\\", " / ").rsplit(".", 1)[0], option)
        index = self.combo.findData(chosen) if chosen in options else 0
        self.combo.setCurrentIndex(max(0, index))
        self.strength.setValue(float(strength))
        self.strength.setEnabled(bool(self.current()))
        self.strength.blockSignals(False)
        self.combo.blockSignals(False)

    def current(self) -> str:
        return self.combo.currentData() or ""

    def current_strength(self) -> float:
        return float(self.strength.value())

    def _on_pick(self) -> None:
        self.strength.setEnabled(bool(self.current()))
        self.changed.emit()

    def retranslate(self) -> None:
        self.heading.setText(t("LORA (OPTIONAL)"))
        self.strength.setPrefix(t("Strength") + "  ")
        if self.combo.count():
            self.combo.setItemText(0, t("None"))
