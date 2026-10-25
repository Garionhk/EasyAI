"""Corrects EasyAI's guesses about which node does what.

Autodetection is good but not perfect, and when it is wrong the user needs a way
to fix it that does not involve editing JSON. Each binding gets two dropdowns -
node, then input - populated only with things that actually exist in the graph,
so a saved manifest can never point at a node that isn't there.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from app.ratios import ENGINES
from app.workflows.manifest import (
    AUDIO_SLOTS, BINDING_KEYS, FILE_SLOTS, IMAGE_SLOTS, VIDEO_SLOTS,
    Binding, autodetect, describe_nodes, slot_kind,
)

#: Plain-English name and explanation for each binding. File slots are labelled
#: on the fly instead, since their names come from the workflow.
LABELS: dict[str, tuple[str, str]] = {
    "prompt":   ("Prompt", "Where the words the user types are put"),
    "negative": ("Things to avoid", "The negative prompt, if this workflow has one"),
    "width":    ("Width", "The width in pixels, set from the shape chooser"),
    "height":   ("Height", "The height in pixels, set from the shape chooser"),
    "ratio":    ("Shape chooser", "A node that takes an aspect ratio directly"),
    "megapixels": ("Total pixels",
                   "A node sized by megapixels rather than width and height. "
                   "Binding it lets the Image tab offer a detail setting."),
    "seed":     ("Seed", "Randomised each run so results differ"),
    "enhancer": ("Prompt improver",
                 "A node that rewrites the prompt with a language model. "
                 "Binding it here adds a tick box so the user can turn it off."),
    "lora":     ("Optional LoRA",
                 "A LoRA loader the user may fill or leave out. Offered as a "
                 "dropdown with None; None takes the loader out of the run."),
    "lora_strength": ("LoRA strength", "How strongly the optional LoRA applies"),
    "boxes":    ("Layout boxes",
                 "A Create Bounding Boxes node. Binding it adds the box editor."),
    "layout":   ("Layout prompt",
                 "The node that turns the boxes into prompt text. Taken out "
                 "when no boxes are drawn, so the prompt runs as plain text."),
    "length":   ("Length", "Number of frames or seconds, for video and music"),
    "batch":    ("How many", "Number of results per run"),
    "output":   ("Output name", "The file name prefix on the Save node"),
}

_NONE = "— not used —"


class TargetRow(QWidget):
    """Node dropdown + input dropdown for one target."""

    def __init__(self, nodes, binding: Binding | None, parent=None):
        super().__init__(parent)
        self._nodes = nodes            # [(id, class_type, title, [input names])]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.node_combo = QComboBox()
        self.node_combo.addItem(_NONE, None)
        for node_id, class_type, title, _ in nodes:
            label = f"{node_id}  ·  {class_type}"
            if title and title != class_type:
                label += f"  ({title})"
            self.node_combo.addItem(label, node_id)
        self.node_combo.currentIndexChanged.connect(self._refill_inputs)
        layout.addWidget(self.node_combo, 3)

        self.input_combo = QComboBox()
        layout.addWidget(self.input_combo, 2)

        self.set_binding(binding)

    def set_binding(self, binding: Binding | None) -> None:
        index = self.node_combo.findData(binding.node) if binding else 0
        self.node_combo.setCurrentIndex(index if index >= 0 else 0)
        self._refill_inputs()
        if binding:
            i = self.input_combo.findText(binding.input)
            if i >= 0:
                self.input_combo.setCurrentIndex(i)

    def _refill_inputs(self) -> None:
        node_id = self.node_combo.currentData()
        self.input_combo.clear()
        if node_id is None:
            self.input_combo.setEnabled(False)
            return
        self.input_combo.setEnabled(True)
        for nid, _, _, inputs in self._nodes:
            if nid == node_id:
                self.input_combo.addItems(inputs or [""])
                break

    def binding(self) -> Binding | None:
        node_id = self.node_combo.currentData()
        if node_id is None:
            return None
        return Binding(node=str(node_id), input=self.input_combo.currentText())


class BindingRow(QWidget):
    """All the targets for one binding key, with a button to add more.

    Most keys drive a single input, but width and height often have to be
    written to two nodes at once (empty latent + scheduler), so the editor has
    to be able to show and edit a list.
    """

    def __init__(self, nodes, bindings: list[Binding], parent=None):
        super().__init__(parent)
        self._nodes = nodes
        self.rows: list[TargetRow] = []

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self.add_btn = QPushButton("+ also set another node")
        self.add_btn.setFlat(True)
        self.add_btn.setStyleSheet("text-align:left;padding:2px;")
        self.add_btn.clicked.connect(lambda: self._add_row(None))
        self._layout.addWidget(self.add_btn)

        self.set_bindings(bindings)

    def set_bindings(self, bindings: list[Binding]) -> None:
        for row in self.rows:
            row.setParent(None)
        self.rows.clear()
        for binding in (bindings or [None]):
            self._add_row(binding)

    def _add_row(self, binding: Binding | None) -> None:
        row = TargetRow(self._nodes, binding)
        self.rows.append(row)
        self._layout.insertWidget(len(self.rows) - 1, row)

    def bindings(self) -> list[Binding]:
        seen: list[Binding] = []
        for row in self.rows:
            binding = row.binding()
            if binding and not any(b.node == binding.node and b.input == binding.input
                                   for b in seen):
                seen.append(binding)
        return seen


class ManifestEditor(QDialog):
    """Edit one workflow's sidecar manifest."""

    def __init__(self, workflow, parent=None):
        super().__init__(parent)
        self.workflow = workflow
        self.manifest = workflow.manifest
        # Only nodes with at least one editable (non-linked) input are useful.
        self.nodes = [row for row in describe_nodes(workflow.graph) if row[3]]

        self.setWindowTitle(f"Set up — {workflow.name}")
        self.resize(760, 720)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        blurb = QLabel(
            "EasyAI only changes the parts of a workflow listed here. "
            "If something is set to the wrong node, fix it below.")
        blurb.setWordWrap(True)
        blurb.setObjectName("Hint")
        outer.addWidget(blurb)

        if self.manifest.ambiguous:
            warning = QLabel(
                "⚠  EasyAI had to guess these: "
                + ", ".join(LABELS.get(k, (k, ""))[0] for k in self.manifest.ambiguous))
            warning.setWordWrap(True)
            outer.addWidget(warning)

        # --- general -----------------------------------------------------
        general = QGroupBox("This workflow")
        form = QFormLayout(general)
        self.name_edit = QLineEdit(self.manifest.name or self.workflow.name)
        form.addRow("Name shown in the list", self.name_edit)

        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Work it out automatically", None)
        for key, engine in ENGINES.items():
            if key != "_default":
                self.engine_combo.addItem(f"{engine.label}  ({key})", key)
        index = self.engine_combo.findData(self.manifest.engine)
        self.engine_combo.setCurrentIndex(index if index >= 0 else 0)
        self.engine_combo.setToolTip(
            "Decides the pixel sizes used for each shape. Pick the model family "
            "this workflow uses.")
        form.addRow("Model family", self.engine_combo)
        outer.addWidget(general)

        # --- bindings ----------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        binding_form = QFormLayout(inner)
        binding_form.setSpacing(10)

        self.rows: dict[str, BindingRow] = {}
        self.label_edits: dict[str, QLineEdit] = {}
        for key in BINDING_KEYS:
            kind = slot_kind(key)
            # Only offer empty file slots one past the last one in use, so the
            # list doesn't show ten blank picture rows on a simple workflow.
            if kind and not self.manifest.has(key) and not self._is_next_free(key, kind):
                continue

            # Not LABELS.get(key, self._slot_label(...)): Python evaluates the
            # fallback first, and it only makes sense for file slots.
            label, tip = LABELS[key] if key in LABELS else self._slot_label(key, kind)
            row = BindingRow(self.nodes, self.manifest.all(key))
            row.setToolTip(tip)
            self.rows[key] = row

            if kind:
                # File slots get an editable name, because that name is what
                # the user sees above the drop box ("First frame").
                caption_holder = QWidget()
                caption_col = QVBoxLayout(caption_holder)
                caption_col.setContentsMargins(0, 0, 0, 0)
                caption_col.setSpacing(2)
                caption_col.addWidget(QLabel(label))
                name_edit = QLineEdit(self.manifest.labels.get(key, ""))
                name_edit.setPlaceholderText(self.manifest.label_for(key))
                name_edit.setToolTip("What to call this file box in the app")
                caption_col.addWidget(name_edit)
                self.label_edits[key] = name_edit
                binding_form.addRow(caption_holder, row)
            else:
                caption = QLabel(label)
                caption.setToolTip(tip)
                binding_form.addRow(caption, row)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # --- buttons -----------------------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        reset = buttons.button(QDialogButtonBox.Reset)
        reset.setText("Guess again")
        reset.setToolTip("Throw away these settings and let EasyAI detect them afresh")
        reset.clicked.connect(self._redetect)
        outer.addWidget(buttons)

    def _is_next_free(self, key: str, kind: str) -> bool:
        """True for the first unused slot of its kind - the 'add another' row."""
        family = {"image": IMAGE_SLOTS, "audio": AUDIO_SLOTS,
                  "video": VIDEO_SLOTS}[kind]
        for slot in family:
            if not self.manifest.has(slot):
                return slot == key
        return False

    @staticmethod
    def _slot_label(key: str, kind: str) -> tuple[str, str]:
        family = {"image": IMAGE_SLOTS, "audio": AUDIO_SLOTS,
                  "video": VIDEO_SLOTS}.get(kind)
        noun = {"image": "Picture", "audio": "Sound", "video": "Video"}.get(kind)
        if not family or not noun:
            # A binding with no entry in LABELS and no file kind - show the key
            # rather than crashing the dialog, which is what used to happen.
            return key.replace("_", " ").capitalize(), ""
        return (f"{noun} {family.index(key) + 1}",
                f"A {kind} file the user supplies for this run")

    def _redetect(self) -> None:
        fresh = autodetect(self.workflow.graph, mode=self.workflow.mode,
                           name=self.workflow.name)
        for key, row in self.rows.items():
            row.set_bindings(fresh.all(key))
        for key, edit in self.label_edits.items():
            edit.setText(fresh.labels.get(key, ""))
            edit.setPlaceholderText(fresh.label_for(key))
        index = self.engine_combo.findData(fresh.engine)
        self.engine_combo.setCurrentIndex(index if index >= 0 else 0)

    def _save(self) -> None:
        for key, row in self.rows.items():
            self.manifest.set(key, row.bindings())
        for key, edit in self.label_edits.items():
            text = edit.text().strip()
            if text:
                self.manifest.labels[key] = text
            else:
                self.manifest.labels.pop(key, None)

        self.manifest.name = self.name_edit.text().strip() or self.workflow.name
        self.manifest.engine = self.engine_combo.currentData()
        # A hand-checked manifest is no longer a guess.
        self.manifest.autodetected = False
        self.manifest.ambiguous = []
        self.manifest.expose = [
            k for k in ("prompt",) + FILE_SLOTS if self.manifest.has(k)
        ]
        if self.manifest.has("length"):
            self.manifest.expose.append("length")

        try:
            self.manifest.save(self.workflow.manifest_path)
        except OSError as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self.accept()
