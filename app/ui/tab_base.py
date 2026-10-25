"""The generate screen. Every mode is this class with a few switches flipped.

Layout is always the same three columns, because a viewer who learns the Image
tab should already know the Video tab:

    [ workflows ]  [ prompt · inputs · shape · Create ]  [ preview · results ]

What varies per mode comes from app/modes.py and from the workflow's manifest -
a control only appears if the manifest actually binds something for it.
"""
from __future__ import annotations

import random
from pathlib import Path, PurePosixPath

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from app.comfy.client import ComfyClient
from app.comfy import objectinfo
from app.i18n import plural, t
from app.jobs import JobResult, JobWorker
from app.modes import Mode
from app import boxes as layout_boxes
from app.ratios import RATIOS, read_node_profile, solve_for
from app.ui import theme
from app.ui.widgets import (
    ColumnScroll, DropZone, DurationPicker, LoraPicker, MegapixelPicker, ModelPicker,
    PreviewPane,
    RatioPicker, ResultGallery, Switch, WorkflowList, open_in_explorer,
)
from app.workflows.loader import Workflow, scan
from app.workflows.manifest import slot_kind
from app.workflows.patch import MAX_SEED, GenerationRequest, missing_inputs

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_AUDIO_EXT = {".flac", ".mp3", ".wav", ".ogg", ".m4a"}
_VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov"}


def _ratio_heads(options: list[str] | None) -> list[str] | None:
    """['9:16 (Portrait Widescreen)'] -> ['9:16']."""
    if not options:
        return None
    return [o.split(" ", 1)[0].strip() for o in options]


class GenerateTab(QWidget):
    """One mode's whole screen."""

    #: bubbled up so the main window can show it in the status bar
    status_message = Signal(str)
    #: asks the main window to open the manifest editor for a workflow
    edit_requested = Signal(object)

    def __init__(self, mode: Mode, cfg, client: ComfyClient, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.cfg = cfg
        self.client = client
        self.worker: JobWorker | None = None
        #: The one shared queue, handed over by the window after construction.
        self.queue = None
        #: Items this tab put in, so it follows its own work and ignores the
        #: rest - the queue is shared by all three tabs.
        self._mine: set[int] = set()
        self._running_id: int | None = None
        self.workflows: list[Workflow] = []
        self._caps: objectinfo.Capabilities | None = None
        self._ratio_options: list[str] | None = None
        #: The selected workflow's manifest. Used instead of asking widgets
        #: whether they are visible - Qt reports every widget on a background
        #: tab as hidden, and a job that finishes after the user switches tabs
        #: would otherwise be treated as if its controls did not exist.
        self._manifest = None
        #: The picture preview. None on tabs whose result is words, not images.
        self.preview: PreviewPane | None = None
        #: Layout boxes drawn this session, per workflow name. MOSS-Image
        #: keeps its boxes in the prompt instead - see _moss_layout.
        self._boxes: dict[str, list[layout_boxes.Box]] = {}
        #: The picture size each MOSS layout's pixels were written for.
        self._moss_frame_used: dict[str, tuple[int, int]] = {}

        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_middle())
        splitter.addWidget(self._build_right())
        splitter.setSizes([250, 570, 380])
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter)

    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(8)

        heading = QLabel(f"{self.mode.icon}  Choose a style")
        heading.setObjectName("Heading")
        layout.addWidget(heading)

        self.workflow_list = WorkflowList()
        self.workflow_list.picked.connect(self._on_workflow_picked)
        self.workflow_list.rename_requested.connect(self._rename_workflow)
        self.workflow_list.setup_requested.connect(self.edit_requested.emit)
        self.workflow_list.reveal_requested.connect(
            lambda wf: open_in_explorer(wf.path))
        layout.addWidget(self.workflow_list, 1)

        self.empty_label = QLabel()
        self.empty_label.setObjectName("Hint")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        row = QHBoxLayout()
        self.refresh_btn = QPushButton(t("Refresh"))
        self.refresh_btn.clicked.connect(lambda: self.reload(check=True, refresh=True))
        row.addWidget(self.refresh_btn)
        self.setup_btn = QPushButton(t("Set up…"))
        self.setup_btn.setToolTip(
            t("Change which parts of this workflow EasyAI controls"))
        self.setup_btn.clicked.connect(self._on_edit_clicked)
        row.addWidget(self.setup_btn)
        layout.addLayout(row)
        return panel

    def _build_middle(self) -> QWidget:
        """The prompt and every option, scrolling above a fixed Create button.

        A workflow can now ask for a negative prompt, a model, ten reference
        pictures, shape, detail and length at once, which is more than fits
        on a laptop screen. Everything above Create scrolls; Create, its
        progress and the status line stay put, so the button is never scrolled
        out of reach and the result of a run is always in view.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 10, 0)
        layout.setSpacing(10)

        prompt_head = QHBoxLayout()
        prompt_head.setContentsMargins(0, 0, 0, 0)
        self.prompt_label = QLabel(t("What do you want to make?"))
        self.prompt_label.setObjectName("Title")
        prompt_head.addWidget(self.prompt_label)
        prompt_head.addStretch(1)
        self.prompt_counter = QLabel(t("{n} chars", n=0))
        self.prompt_counter.setObjectName("Counter")
        prompt_head.addWidget(self.prompt_counter)
        layout.addLayout(prompt_head)

        self.prompt_box = QTextEdit()
        self.prompt_box.setPlaceholderText(t(self.mode.prompt_hint))
        self.prompt_box.setMinimumHeight(150)
        self.prompt_box.textChanged.connect(self._on_prompt_changed)
        layout.addWidget(self.prompt_box)

        # What to leave out. Only for workflows that have a negative prompt,
        # and filled with the workflow's own - Anima and the LTX workflows ship
        # long ones that nobody could see before, so the box shows what is
        # really in use rather than an empty field that suggests nothing is.
        self.negative_label = QLabel(t("NEGATIVE PROMPT"))
        self.negative_label.setObjectName("Heading")
        self.negative_label.setVisible(False)
        layout.addWidget(self.negative_label)
        self.negative_box = QTextEdit()
        self.negative_box.setAcceptRichText(False)
        self.negative_box.setPlaceholderText(
            t("What you do not want to see - leave empty for nothing."))
        self.negative_box.setFixedHeight(66)
        self.negative_box.setVisible(False)
        layout.addWidget(self.negative_box)

        # Drop zones are rebuilt for each workflow, because how many files a
        # workflow wants - and what to call them - comes from its manifest.
        # First-and-last-frame video needs two pictures; some reference
        # workflows want ten.
        self.drop_zones: dict[str, DropZone] = {}
        self.drop_scroll = QScrollArea()
        self.drop_scroll.setWidgetResizable(True)
        self.drop_scroll.setFrameShape(QScrollArea.NoFrame)
        self.drop_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.drop_scroll.setVisible(False)

        # Shown only for workflows where files may be left out.
        self.drop_hint = QLabel(
            t("Attach only the ones you want — leave the rest empty."))
        self.drop_hint.setObjectName("Hint")
        self.drop_hint.setVisible(False)
        layout.addWidget(self.drop_hint)

        self.drop_holder = QWidget()
        self.drop_layout = QVBoxLayout(self.drop_holder)
        self.drop_layout.setContentsMargins(0, 0, 6, 0)
        self.drop_layout.setSpacing(6)
        self.drop_scroll.setWidget(self.drop_holder)
        layout.addWidget(self.drop_scroll)

        # Layout boxes, for the workflows that read a layout (Flux 3 Klein,
        # MOSS-Image). Drawn in their own window; only a summary lives here.
        self.boxes_wrap = QWidget()
        boxes_col = QVBoxLayout(self.boxes_wrap)
        boxes_col.setContentsMargins(0, 0, 0, 0)
        boxes_col.setSpacing(4)
        self.boxes_heading = QLabel(t("LAYOUT BOXES (OPTIONAL)"))
        self.boxes_heading.setObjectName("Heading")
        boxes_col.addWidget(self.boxes_heading)
        boxes_row = QHBoxLayout()
        boxes_row.setSpacing(8)
        self.boxes_btn = QPushButton(t("Draw boxes…"))
        self.boxes_btn.setToolTip(t(
            "Say where things go: draw a box, then describe what is in it."))
        self.boxes_btn.clicked.connect(self._on_draw_boxes)
        boxes_row.addWidget(self.boxes_btn)
        self.boxes_summary = QLabel("")
        self.boxes_summary.setObjectName("Hint")
        boxes_row.addWidget(self.boxes_summary, 1)
        self.boxes_clear = QPushButton(t("Clear"))
        self.boxes_clear.clicked.connect(self._on_clear_boxes)
        boxes_row.addWidget(self.boxes_clear)
        boxes_col.addLayout(boxes_row)
        self.boxes_note = QLabel("")
        self.boxes_note.setObjectName("Hint")
        self.boxes_note.setWordWrap(True)
        self.boxes_note.setVisible(False)
        boxes_col.addWidget(self.boxes_note)
        self.boxes_wrap.setVisible(False)
        layout.addWidget(self.boxes_wrap)

        # Which model, when the folder holds more than one of the family.
        self.model_picker = ModelPicker()
        self.model_picker.setVisible(False)
        self.model_picker.changed.connect(self._on_model_changed)
        layout.addWidget(self.model_picker)

        # An optional LoRA, where the workflow parks a loader for one.
        self.lora_picker = LoraPicker()
        self.lora_picker.setVisible(False)
        self.lora_picker.changed.connect(self._on_lora_changed)
        layout.addWidget(self.lora_picker)

        # Shape / length / batch row.
        options = QHBoxLayout()
        options.setSpacing(12)

        self.ratio_picker = RatioPicker()
        self.ratio_picker.setVisible(self.mode.uses_ratio)
        options.addWidget(self.ratio_picker, 2)

        self.megapixels = MegapixelPicker()
        self.megapixels.setVisible(False)
        self.megapixels.changed.connect(self._on_megapixels_changed)
        options.addWidget(self.megapixels, 2)

        self.duration = DurationPicker()
        self.duration.setVisible(False)
        options.addWidget(self.duration, 2)

        self.batch_wrap, self.batch_spin = self._labelled_spin(
            "How many", 1, 16, int(self.cfg.get("batch_count") or 1),
            "Make more than one at a time")
        self.batch_wrap.setVisible(False)
        options.addWidget(self.batch_wrap, 1)
        options.addStretch(0)
        layout.addLayout(options)

        # LTX 2.5 and friends run the prompt through a language model first. It
        # writes much better prompts, but loads a large model and takes time, so
        # it is a visible switch rather than a hidden behaviour.
        self.enhance_check = Switch(t("Let the AI improve my wording first"))
        self.enhance_check.setToolTip(t(
            "Rewrites your prompt into the longer, more detailed description "
            "these video models prefer.\n\n"
            "Better results, but it loads an extra model and adds time to the "
            "first run."))
        self.enhance_check.setChecked(True)
        self.enhance_check.setVisible(False)
        self.enhance_check.toggled.connect(lambda _on: self._update_boxes_row())
        layout.addWidget(self.enhance_check)

        self.seed_lock = Switch(
            t("Repeat the same result, so I can compare changes"))
        self.seed_lock.setToolTip(t(
            "Every creation starts from a random number, so pressing Create "
            "twice normally gives you two different results.\n\n"
            "Tick this to keep that number the same. Then if you change a word "
            "in your prompt, the difference you see is caused by your change "
            "and nothing else — useful for showing what a word actually does.\n\n"
            "Leave it off for normal use."))
        self.seed_lock.setChecked(bool(self.cfg.get("lock_seed")))
        self.seed_lock.setVisible(False)
        layout.addWidget(self.seed_lock)

        layout.addStretch(1)

        self.middle_scroll = ColumnScroll()
        self.middle_scroll.setWidget(panel)

        column = QWidget()
        outer = QVBoxLayout(column)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.middle_scroll, 1)

        footer = QWidget()
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(6, 8, 10, 0)
        layout.setSpacing(10)
        outer.addWidget(footer)

        self.create_btn = QPushButton(t("Create {what}", what=t(self.mode.label)))
        self.create_btn.setObjectName("Primary")
        self.create_btn.clicked.connect(self._on_create)
        layout.addWidget(self.create_btn)

        self.cancel_btn = QPushButton(t("Stop"))
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self.cancel_btn)

        self.shortcut_hint = QLabel(t("CTRL + ENTER  to run"))
        self.shortcut_hint.setObjectName("Counter")
        self.shortcut_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.shortcut_hint)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Hint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return column

    def _on_prompt_changed(self) -> None:
        count = len(self.prompt_box.toPlainText())
        self.prompt_counter.setText(f"{count} chars")
        # A MOSS layout lives in the prompt, so typing can add or remove boxes.
        if getattr(self, "boxes_wrap", None) is not None and not self.boxes_wrap.isHidden():
            self._update_boxes_row()

    def _build_right(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(10)

        self.preview = PreviewPane()
        self.preview.clear_preview(f"Your {self.mode.label.lower()} will appear here")
        layout.addWidget(self.preview, 3)

        self.gallery = ResultGallery()
        self.gallery.set_folder(self.cfg.output_dir(self.mode.key))
        self.gallery.selected.connect(self._on_gallery_pick)
        layout.addWidget(self.gallery, 2)
        return panel

    @staticmethod
    def _labelled_spin(label, low, high, value, tip) -> tuple[QWidget, QSpinBox]:
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        heading = QLabel(label)
        heading.setObjectName("Heading")
        col.addWidget(heading)
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setToolTip(tip)
        col.addWidget(spin)
        col.addStretch(1)
        return wrap, spin

    # ------------------------------------------------------------- loading
    def reload(self, check: bool = True, refresh: bool = False) -> None:
        """Rescan the workflow folder and re-run the pre-flight check."""
        root = self.cfg.workflow_dir()

        # Read the node catalogue first: which file inputs may be left empty is
        # a question only the running ComfyUI can answer, and the manifest is
        # written during the scan.
        #
        # Refresh re-reads it rather than trusting what was read at startup.
        # The node list is where the model choices come from, so a model copied
        # in while EasyAI is open must show up without restarting anything.
        if refresh:
            self.invalidate_capabilities()
        if check and (self._caps is None or not self._caps.available):
            self._caps = objectinfo.fetch(self.client, refresh=refresh)

        self.workflows = scan(root, self.mode.key, caps=self._caps)

        if check and self._caps and self._caps.available:
            objectinfo.check_all(self.workflows, self._caps)

        self.workflow_list.set_workflows(self.workflows)
        self._update_empty_state(root)

        if self.workflows:
            self.status_message.emit(
                f"{self.mode.label}: {objectinfo.summarise(self.workflows)}")

    def invalidate_capabilities(self) -> None:
        """Force the next reload to re-read /object_info (after a restart, say)."""
        self._caps = None
        self.client.clear_cache()

    def _update_empty_state(self, root) -> None:
        empty = not self.workflows
        self.empty_label.setVisible(empty)
        self.workflow_list.setVisible(not empty)
        if empty:
            folder = Path(root) / self.mode.key
            self.empty_label.setText(
                f"No {self.mode.label.lower()} workflows yet.\n\n"
                f"Put ComfyUI API files (Workflow → Export (API)) into:\n\n{folder}\n\n"
                f"then press Refresh."
            )
        self._set_controls_enabled(not empty)

    # ------------------------------------------------------------ selection
    # -- choosing a model from the same family -----------------------------
    @staticmethod
    def _family_of(name: str) -> str:
        r"""The subfolder a model sits in, which is what makes it a family.

        ``ZI\z_image_turbo_bf16.safetensors`` -> ``ZI``, and a file loose in
        the folder root gives ``""`` - so loose files are a family of their
        own rather than being lumped in with every subfolder.

        ComfyUI writes these with a backslash on Windows and a forward slash
        elsewhere, so both are accepted.
        """
        parent = PurePosixPath(name.replace("\\", "/")).parent.as_posix()
        return "" if parent == "." else parent

    def _model_options(self, manifest, workflow) -> tuple[list[str], str]:
        """(the models related to this workflow's own, the workflow's own).

        Related means the same folder - ``ZI\\`` for Z-Image, ``Moss\\`` for
        MOSS-Image - which is how these models are kept: one folder per
        family. A model from another family would not work with the rest of
        the workflow, so it is not offered.

        The list comes from the engine - live, or as it last listed them - so
        it only holds models ComfyUI can really load, and it answers per loader
        class, which is why UNETLoader and UnetLoaderGGUF get different folders.
        """
        if not (manifest and manifest.has("model") and workflow):
            return [], ""

        binding = manifest.get("model")
        node = (workflow.graph.get(binding.node) or {})
        own = str((node.get("inputs") or {}).get(binding.input) or "")
        class_type = str(node.get("class_type") or "")
        if not (own and class_type):
            return [], ""

        offered = self._engine_options(class_type, binding.input)
        family = self._family_of(own)
        siblings = [o for o in offered if self._family_of(o) == family]
        # The workflow's own model belongs in the list even if the engine has
        # not caught up with it, or the control would silently change it.
        if own not in siblings:
            siblings.append(own)
        return sorted(siblings, key=str.lower), own

    def _engine_options(self, class_type: str, field_name: str) -> list[str]:
        """The files an input accepts: from the running engine, or else as
        it listed them the last time it was running."""
        if self._caps and self._caps.available:
            return self._caps.enum_options(class_type, field_name) or []
        return objectinfo.remembered_options(class_type, field_name)

    def _remembered_model(self, workflow) -> str:
        return (self.cfg.get("model_choice") or {}).get(workflow.name, "")

    def _remember_model(self, workflow, model: str, own: str) -> None:
        """Keep the choice, or forget it once it is back to the original."""
        chosen = dict(self.cfg.get("model_choice") or {})
        if model and model != own:
            chosen[workflow.name] = model
        else:
            chosen.pop(workflow.name, None)
        self.cfg.set("model_choice", chosen)
        self.cfg.save()

    def _on_model_changed(self, model: str) -> None:
        workflow = self.workflow_list.current_workflow()
        if workflow:
            self._remember_model(workflow, model, self.model_picker._own)

    # -- an optional LoRA ----------------------------------------------------
    def _lora_options(self, manifest, workflow) -> list[str]:
        """The LoRAs worth offering for this workflow's loader.

        A LoRA only works with the model family it was trained for, and a
        collection runs to hundreds. When the model sits in a folder - Moss        - and LoRAs sit in a folder of the same name, those are the ones
        offered; otherwise everything the engine has.
        """
        if not (manifest and manifest.has("lora") and workflow):
            return []
        binding = manifest.get("lora")
        class_type = str((workflow.graph.get(binding.node) or {}).get("class_type") or "")
        offered = sorted(self._engine_options(class_type, binding.input), key=str.lower)
        _, own_model = self._model_options(manifest, workflow)
        family = self._family_of(own_model).lower() if own_model else ""
        if family:
            same = [o for o in offered if self._family_of(o).lower() == family]
            if same:
                return same
        return offered

    def _show_lora(self, manifest, workflow) -> None:
        show = bool(manifest and manifest.has("lora"))
        self.lora_picker.setVisible(show)
        if not show:
            return
        remembered = (self.cfg.get("lora_choice") or {}).get(workflow.name) or {}
        self.lora_picker.set_options(
            self._lora_options(manifest, workflow),
            str(remembered.get("file") or ""),
            float(remembered.get("strength") or 1.0))

    def _on_lora_changed(self) -> None:
        workflow = self.workflow_list.current_workflow()
        if not workflow:
            return
        chosen = dict(self.cfg.get("lora_choice") or {})
        if self.lora_picker.current():
            chosen[workflow.name] = {"file": self.lora_picker.current(),
                                     "strength": self.lora_picker.current_strength()}
        else:
            chosen.pop(workflow.name, None)
        self.cfg.set("lora_choice", chosen)
        self.cfg.save()

    # -- layout boxes --------------------------------------------------------
    def _layout_class(self, manifest, workflow) -> str:
        binding = manifest.get("layout") if manifest else None
        if binding is None or not workflow:
            return ""
        return str((workflow.graph.get(binding.node) or {}).get("class_type") or "").lower()

    def _boxes_background_slot(self, manifest, workflow) -> str:
        """The file slot whose picture the boxes are drawn on, if any.

        An edit's canvas shows the picture being edited - the box node's
        background - so the boxes land on what the user is looking at.
        """
        binding = manifest.get("boxes") if manifest else None
        if binding is None or not workflow:
            return ""
        background = ((workflow.graph.get(binding.node) or {}).get("inputs") or {}).get("background")
        if not (isinstance(background, list) and background):
            return ""
        loader = str(background[0])
        for key in manifest.file_slots():
            if any(b.node == loader for b in manifest.all(key)):
                return key
        return ""

    # -- MOSS-Image: the layout is the prompt --------------------------------
    def _is_moss_layout(self, manifest, workflow) -> bool:
        return "moss" in self._layout_class(manifest, workflow)

    def _moss_frame(self, manifest, workflow) -> tuple[int, int]:
        """The size of the picture MOSS-Image will make, as its size node will
        work it out - the layout's pixels are measured on it."""
        profile = None
        if manifest and manifest.has("ratio"):
            profile = read_node_profile(workflow.graph.get(manifest.get("ratio").node))
        megapixels, multiple = profile if profile else (2.0, 16)
        if not self.megapixels.isHidden():
            megapixels = self.megapixels.megapixels()
        ratio = self.ratio_picker.current_ratio() if self.ratio_picker.isEnabled() else "1:1"
        return solve_for(ratio, megapixels, multiple)

    def _current_boxes(self, manifest, workflow) -> list[layout_boxes.Box]:
        """The boxes in force: drawn for Flux 3, read from the prompt for MOSS."""
        if self._is_moss_layout(manifest, workflow):
            layout = layout_boxes.parse_layout(self.prompt_box.toPlainText())
            if layout is None:
                return []
            frame = (self._moss_frame_used.get(workflow.name)
                     or self._moss_frame(manifest, workflow))
            return layout_boxes.layout_to_boxes(layout, *frame)
        return self._boxes.get(workflow.name, [])

    def _write_moss_layout(self, workflow, boxes, frame) -> None:
        """Put the boxes into the prompt as a MOSS layout, keeping its summary.

        With no prompt at all the layout is the whole prompt. With plain words
        they become the layout's caption, as ComfyUI's layout node does. With
        no boxes left, a layout that only had a caption goes back to plain
        words.
        """
        text = self.prompt_box.toPlainText()
        layout = layout_boxes.parse_layout(text)
        if boxes:
            new = layout_boxes.boxes_to_layout(
                boxes, *frame, base=layout,
                caption="" if layout else text.strip())
            self.prompt_box.setPlainText(layout_boxes.layout_text(new))
            self._moss_frame_used[workflow.name] = tuple(frame)
        elif layout is not None:
            summary = layout.get("summary") if isinstance(layout.get("summary"), dict) else {}
            if set(summary) <= {"caption"} and set(layout) <= {
                    "summary", "items", "bbox_coord_space", "bbox_format"}:
                self.prompt_box.setPlainText(layout_boxes.layout_caption(layout))
            else:
                layout["items"] = []
                self.prompt_box.setPlainText(layout_boxes.layout_text(layout))
            self._moss_frame_used.pop(workflow.name, None)

    def _update_boxes_row(self) -> None:
        manifest = self._manifest
        workflow = self.workflow_list.current_workflow()
        show = bool(manifest and manifest.has("boxes") and manifest.has("layout"))
        self.boxes_wrap.setVisible(show)
        if not show or not workflow:
            return
        drawn = self._current_boxes(manifest, workflow)
        objects, texts = layout_boxes.summary(drawn)
        if not drawn:
            self.boxes_summary.setText(t("None — the prompt alone decides the layout."))
        else:
            parts = [plural(objects, "{n} box", "{n} boxes")]
            if texts:
                parts.append(plural(texts, "{n} text", "{n} texts"))
            if self._is_moss_layout(manifest, workflow):
                parts.append(t("written in the prompt"))
            self.boxes_summary.setText(", ".join(parts))
        self.boxes_clear.setEnabled(bool(drawn))
        self.boxes_btn.setText(t("Edit boxes…") if drawn else t("Draw boxes…"))

        warn = bool(drawn and not self.enhance_check.isHidden()
                    and self.enhance_check.isChecked())
        self.boxes_note.setVisible(warn)
        if warn:
            self.boxes_note.setText(t(
                "The AI wording helper is on, and it may rewrite the layout. "
                "Turn it off if the boxes must be followed exactly."))

    def _on_draw_boxes(self) -> None:
        from app.ui.box_editor import BoxEditor

        workflow = self.workflow_list.current_workflow()
        manifest = self._manifest
        if not (workflow and manifest):
            return

        background = None
        slot = self._boxes_background_slot(manifest, workflow)
        if slot:
            zone = self.drop_zones.get(slot)
            path = zone.path() if zone else ""
            if not path:
                QMessageBox.information(self, t("Almost there"), t(
                    "Attach “{label}” first - the boxes are drawn on it.",
                    label=manifest.label_for(slot)))
                return
            background = QPixmap(path)

        moss = self._is_moss_layout(manifest, workflow)
        note = ""
        if moss:
            # The canvas is the picture MOSS will make, at its real shape.
            frame = self._moss_frame(manifest, workflow)
            aspect = frame
            layout = layout_boxes.parse_layout(self.prompt_box.toPlainText())
            written_for = self._moss_frame_used.get(workflow.name) or frame
            if layout is not None:
                need_w, need_h = layout_boxes.layout_extent(layout)
                if need_w > written_for[0] + 2 or need_h > written_for[1] + 2:
                    note = t(
                        "This layout was written for a picture at least "
                        "{need} px, but the shape chosen makes {have} px, so "
                        "boxes past the edge are cut to fit. To keep them as "
                        "written, choose a matching shape and detail first.",
                        need=f"{need_w} × {need_h}",
                        have=f"{written_for[0]} × {written_for[1]}")
        else:
            ratio = self.ratio_picker.current_ratio() if self.ratio_picker.isEnabled() else "1:1"
            aspect = RATIOS.get(ratio, (1, 1))
        layout_class = self._layout_class(manifest, workflow)
        editor = BoxEditor(
            self._current_boxes(manifest, workflow),
            aspect=aspect,
            background=background,
            allow_character=moss,
            cites_objects="flux3" in layout_class,
            note=note,
            parent=self)
        if not editor.exec():
            return
        drawn = editor.boxes()
        if moss:
            self._write_moss_layout(workflow, drawn, frame)
        else:
            self._boxes[workflow.name] = drawn
        # MOSS-Image's own template keeps the prompt helper off with boxes: it
        # rewrites the layout away. Flux 3's is built to read it, so it stays.
        if drawn and moss and self.enhance_check.isChecked() \
                and not self.enhance_check.isHidden():
            self.enhance_check.setChecked(False)
        self._update_boxes_row()

    def _on_clear_boxes(self) -> None:
        workflow = self.workflow_list.current_workflow()
        if workflow and self._is_moss_layout(self._manifest, workflow):
            self._write_moss_layout(workflow, [], (0, 0))
        elif workflow:
            self._boxes.pop(workflow.name, None)
        self._update_boxes_row()

    def use_read_prompt(self, found) -> str:
        """Take a prompt read back from a picture, choosing its workflow.

        Returns the name of the workflow chosen, or "" when the picture's
        workflow is not on this tab and only the words were taken. A MOSS-Image
        layout keeps the size of the picture it made, so the box editor opens
        with every box where it was - and the boxes move with the shape if
        another one is picked afterwards.
        """
        name = getattr(found, "workflow_name", "") or ""
        chosen = ""
        for index, workflow in enumerate(self.workflows):
            if name and workflow.name == name:
                self.workflow_list.setCurrentRow(index)
                chosen = name
                break
        self.prompt_box.setPlainText(found.prompt)
        workflow = self.workflow_list.current_workflow()
        size = getattr(found, "size", None)
        if workflow and size and self._is_moss_layout(self._manifest, workflow) \
                and layout_boxes.parse_layout(found.prompt) is not None:
            self._moss_frame_used[workflow.name] = tuple(size)
            self._match_shape(size)
            # As when boxes are drawn: MOSS's helper would rewrite the layout.
            if not self.enhance_check.isHidden() and self.enhance_check.isChecked():
                self.enhance_check.setChecked(False)
        self._update_boxes_row()
        return chosen

    def _match_shape(self, size) -> None:
        """Pick the shape a picture has, when it is one of the shapes offered."""
        width, height = size
        if not (width and height) or not self.ratio_picker.isEnabled():
            return
        for ratio, (rw, rh) in RATIOS.items():
            if abs((width / height) / (rw / rh) - 1) < 0.02:
                self.ratio_picker.set_ratio(ratio)
                break

    def _on_workflow_picked(self, wf: Workflow) -> None:
        """Reshape the middle column to match what this workflow can do."""
        manifest = wf.manifest
        self._manifest = manifest

        if not wf.loadable:
            self.status_label.setText(wf.error)
            self._set_controls_enabled(False)
            return

        self._set_controls_enabled(True)
        self.prompt_box.setEnabled(bool(manifest and manifest.has("prompt")))
        if manifest and not manifest.has("prompt"):
            self.prompt_box.setPlaceholderText(
                "This workflow does not take a prompt — just press Create.")

        self._show_negative(manifest, wf)
        self._rebuild_drop_zones(manifest, wf)
        self._show_lora(manifest, wf)

        # The model file this workflow generates with - on every tab, since
        # every workflow has one. Only models of the same family are offered.
        siblings, own = self._model_options(manifest, wf)
        self.model_picker.set_options(siblings, own, self._remembered_model(wf))

        if self.mode.uses_ratio:
            can_set = bool(manifest and manifest.can_set_ratio)
            self.ratio_picker.set_engine(manifest.engine if manifest else None)
            # A workflow that picks its size through a chooser node can only
            # offer the shapes that node knows about, so hide the rest rather
            # than letting the user select one that silently does nothing.
            self._ratio_options = self._ratio_options_for(manifest)
            self.ratio_picker.set_allowed(_ratio_heads(self._ratio_options))
            self.ratio_picker.set_node_profile(self._ratio_profile_for(manifest, wf))
            self.ratio_picker.set_enabled_with_reason(
                can_set,
                "" if can_set else
                "This workflow takes its size from the picture you supply.")
            if can_set:
                self.ratio_picker.set_ratio(self.cfg.get("default_ratio") or "2:3")

        # Total pixels. Offered whenever the workflow can actually change it -
        # a megapixels input, or width and height we can recompute.
        show_mp = bool(self.mode.uses_megapixels and manifest
                       and manifest.can_set_megapixels)
        self.megapixels.setVisible(show_mp)
        if show_mp:
            self.megapixels.configure(self.cfg, self.mode.key,
                                      current=self._workflow_megapixels(manifest, wf))
            self.ratio_picker.set_megapixels(self.megapixels.megapixels())
        else:
            self.ratio_picker.set_megapixels(None)

        show_length = bool(manifest and manifest.is_exposed("length"))
        self.duration.setVisible(show_length)
        if show_length:
            self.duration.configure(manifest, self.cfg)
        self.batch_wrap.setVisible(bool(manifest and manifest.has("batch")))
        self.seed_lock.setVisible(bool(manifest and manifest.has("seed")))

        # A workflow whose whole job is rewriting the prompt has no use for an
        # "improve my wording" switch - turning it off would leave nothing.
        has_enhancer = bool(manifest and manifest.has("enhancer")
                            and not self.mode.produces_text)
        self.enhance_check.setVisible(has_enhancer)
        if has_enhancer:
            self.enhance_check.setChecked(bool(self.cfg.get("use_prompt_enhancer")))
            # MOSS-Image's helper rewrites a drawn layout away, so with boxes
            # it starts off, as in ComfyUI's own template.
            if self._is_moss_layout(manifest, wf) \
                    and self._current_boxes(manifest, wf):
                self.enhance_check.setChecked(False)
        self._update_boxes_row()

        if wf.ready:
            self.status_label.setText("")
        elif wf.missing_nodes or wf.missing_models:
            self.status_label.setText(objectinfo.install_hint(wf))
        elif wf.needs_attention:
            self.status_label.setText(t(
                "EasyAI had to guess how this workflow works. "
                "Press 'Set up…' to check it."))
        else:
            self.status_label.setText("")

        self.create_btn.setEnabled(wf.ready)

    def _uses_ratio_node(self, manifest) -> bool:
        """Whether a size-chooser node decides this workflow's output size.

        If the workflow has one, it decides - even when there are also plain
        width and height inputs to write. Flux 2 Klein has both: the chooser
        feeds the latent, while the width and height belong to the scheduler
        and change nothing about the picture's size. Treating the presence of
        those two as "we can set the size ourselves" meant the chooser was
        never told the ratio, so every shape came out at whatever the workflow
        was last saved with.
        """
        return bool(manifest and manifest.has("ratio"))

    def _ratio_options_for(self, manifest) -> list[str] | None:
        """The exact strings this workflow's size-chooser node accepts.

        None when the workflow sets width and height directly, or when we
        haven't been able to read the node list from ComfyUI.
        """
        if not self._uses_ratio_node(manifest):
            return None
        if not (self._caps and self._caps.available):
            return None
        binding = manifest.get("ratio")
        workflow = self.workflow_list.current_workflow()
        graph = workflow.graph if workflow else {}
        class_type = str((graph.get(binding.node) or {}).get("class_type") or "")
        if not class_type:
            return None
        return self._caps.enum_options(class_type, binding.input)

    def _ratio_profile_for(self, manifest, workflow) -> tuple[float, int] | None:
        """The chooser node's own megapixels and rounding step.

        With these the shape list can show the size the file will really have.
        Without them it would show our table's guess, which for these workflows
        is not what comes out - one is set to 0.9 MP, another to 0.5.
        """
        if not self._uses_ratio_node(manifest) or not workflow:
            return None
        binding = manifest.get("ratio")
        return read_node_profile(workflow.graph.get(binding.node))

    def _rebuild_drop_zones(self, manifest, wf=None) -> None:
        """One labelled drop zone per file the selected workflow asks for.

        Any file the user already chose is carried over when it lines up with a
        slot of the same kind, so switching between two similar workflows does
        not throw away their pictures.
        """
        previous = {key: zone.path() for key, zone in self.drop_zones.items()}

        for zone in self.drop_zones.values():
            self.drop_layout.removeWidget(zone)
            zone.deleteLater()
        self.drop_zones.clear()

        slots = manifest.file_slots() if manifest else []
        if not slots:
            self.drop_scroll.setVisible(False)
            self.drop_hint.setVisible(False)
            return

        self.drop_hint.setVisible(bool(manifest.has_optional_files))

        # Reference groups EasyAI filled out to the node's real capacity - ten
        # for Qwen Image 2.1 - are shown one slot at a time. The node numbers
        # pictures by position, so a gap would quietly turn the third picture
        # into <image 2>; revealing the next slot only once the one before is
        # filled makes a gap impossible.
        self._reveal_groups = (manifest.grown_slot_groups(wf.graph)
                               if manifest and wf is not None else [])

        for key in slots:
            kind = slot_kind(key)
            extensions = {"image": _IMAGE_EXT, "audio": _AUDIO_EXT,
                          "video": _VIDEO_EXT}.get(kind, _IMAGE_EXT)
            label = manifest.label_for(key)
            if manifest.is_optional(key):
                label += "  (optional)"
            zone = DropZone(label, extensions, compact=len(slots) > 3)
            zone.setFixedHeight(78 if len(slots) > 3 else 102)
            if previous.get(key):
                zone.set_path(previous[key])
            self.drop_zones[key] = zone
            self.drop_layout.addWidget(zone)
            if any(key in group for group in self._reveal_groups):
                zone.changed.connect(lambda _path: self._reveal_next())

        self.drop_scroll.setVisible(True)
        self._reveal_next()

    def _reveal_next(self) -> None:
        """Keep each grown reference group gap-free, then resize the area.

        Emptying a slot in the middle moves the pictures after it up one, so
        what is attached always runs Reference 1, 2, 3... with the next empty
        slot waiting underneath.
        """
        if getattr(self, "_revealing", False):
            return
        self._revealing = True
        try:
            for group in getattr(self, "_reveal_groups", []):
                zones = [self.drop_zones[k] for k in group if k in self.drop_zones]
                paths = [z.path() for z in zones if z.path()]
                for index, zone in enumerate(zones):
                    wanted = paths[index] if index < len(paths) else ""
                    if zone.path() != wanted:
                        zone.set_path(wanted)
                    zone.setVisible(index <= len(paths))
        finally:
            self._revealing = False
        self._size_drop_area()

    def _size_drop_area(self) -> None:
        shown = [z for z in self.drop_zones.values() if not z.isHidden()]
        if not shown:
            return
        # The scroll area is widgetResizable, which squashes the inner widget to
        # the viewport and lets the boxes draw outside it. Sizing the holder to
        # its real content is what makes the scrollbar appear instead.
        one = 78 if len(self.drop_zones) > 3 else 102
        spacing = self.drop_layout.spacing()
        self.drop_holder.setFixedHeight(len(shown) * one + max(0, len(shown) - 1) * spacing)
        # Show three at a time, so the prompt box and Create stay on screen
        # even when a workflow wants ten reference pictures.
        self.drop_scroll.setFixedHeight(min(len(shown), 3) * one
                                        + min(len(shown), 3) * spacing)
        self.middle_scroll.updateGeometry()

    def _preview_message(self, text: str) -> None:
        if self.preview is not None:
            self.preview.clear_preview(text)

    def _preview_file(self, path) -> None:
        if self.preview is not None:
            self.preview.show_file(path)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.prompt_box, self.negative_box, self.create_btn,
                       self.ratio_picker, self.setup_btn):
            widget.setEnabled(enabled)

    def _show_negative(self, manifest, wf) -> None:
        """Show the negative prompt box, holding this workflow's own text."""
        binding = manifest.get("negative") if manifest else None
        self.negative_label.setVisible(binding is not None)
        self.negative_box.setVisible(binding is not None)
        if binding is None:
            return
        value = ((wf.graph.get(binding.node) or {}).get("inputs") or {}).get(binding.input)
        self.negative_box.setPlainText(value if isinstance(value, str) else "")

    def _rename_workflow(self, wf: Workflow) -> None:
        """Give a workflow a friendly name for the list.

        The name lives in the manifest, not the filename, so renaming never
        touches the exported workflow itself - re-export it and the name
        stays put.
        """
        if not wf or not wf.manifest:
            return

        new_name, accepted = QInputDialog.getText(
            self, "Rename",
            f"What should this be called?\n\nFile: {wf.path.name}",
            text=wf.name)
        if not accepted:
            return

        new_name = new_name.strip() or wf.path.stem
        if new_name == wf.name:
            return

        wf.manifest.name = new_name
        wf.name = new_name
        try:
            wf.manifest.save(wf.manifest_path)
        except OSError as e:
            QMessageBox.warning(self, t("Could not save the name"), str(e))
            return

        self.workflow_list.set_workflows(self.workflows)
        self.status_message.emit(t("Renamed to “{name}”.", name=new_name))

    def _on_edit_clicked(self) -> None:
        wf = self.workflow_list.current_workflow()
        if wf and wf.loadable:
            self.edit_requested.emit(wf)

    def _on_gallery_pick(self, path: str) -> None:
        suffix = Path(path).suffix.lower()
        if suffix in _IMAGE_EXT:
            self._preview_file(path)
        elif suffix in _VIDEO_EXT and self.preview is not None:
            self.preview.show_video(path)
        else:
            self._preview_message(
                Path(path).name + "\n\n"
                + t("Double-click it in the list to play it."))

    # ------------------------------------------------------------ generating
    def _on_create(self) -> None:
        wf = self.workflow_list.current_workflow()
        if not wf or not wf.manifest:
            return

        request = self._collect_request(wf)
        problems = missing_inputs(wf.manifest, request)
        if problems:
            QMessageBox.information(self, t("Almost there"), "\n\n".join(problems))
            return

        if not self.client.is_alive():
            QMessageBox.warning(
                self, t("The AI engine is not running"),
                t("EasyAI cannot reach ComfyUI.\n\n"
                  "Use Settings → Start engine, or start ComfyUI yourself, "
                  "then try again."))
            return

        self._start_worker(wf, request)

    def _collect_request(self, wf: Workflow) -> GenerationRequest:
        manifest = wf.manifest
        moss = bool(manifest.has("boxes") and self._is_moss_layout(manifest, wf))
        if moss:
            # The layout's pixels belong to the size it was drawn for. If the
            # shape or detail has changed since, move the boxes with it.
            used = self._moss_frame_used.get(wf.name)
            frame = self._moss_frame(manifest, wf)
            if used and tuple(used) != tuple(frame):
                self.prompt_box.setPlainText(layout_boxes.rescale_layout(
                    self.prompt_box.toPlainText(), used, frame))
                self._moss_frame_used[wf.name] = tuple(frame)
        request = GenerationRequest(prompt=self.prompt_box.toPlainText().strip())
        # Sent whenever the box is offered: it starts out holding the
        # workflow's own text, so leaving it alone changes nothing, and
        # emptying it really does mean "no negative".
        if not self.negative_box.isHidden() and manifest.has("negative"):
            request.negative = self.negative_box.toPlainText().strip()

        # Snapshotted here, like everything else, so a queued item keeps the
        # model that was chosen when Create was pressed.
        if not self.model_picker.isHidden() and manifest.has("model"):
            chosen = self.model_picker.current()
            if chosen and chosen != self.model_picker._own:
                request.model = chosen

        if self.mode.uses_ratio and manifest.can_set_ratio and self.ratio_picker.isEnabled():
            request.ratio = self.ratio_picker.current_ratio()

        for key, zone in self.drop_zones.items():
            if zone.path():
                request.files[key] = zone.path()

        if manifest.has("lora"):
            request.lora = self.lora_picker.current()
            request.lora_strength = self.lora_picker.current_strength()
        if manifest.has("boxes") and manifest.has("layout"):
            # MOSS's layout is already in the prompt, so the layout node is
            # taken out and the prompt goes to the model as written.
            request.boxes = [] if moss else list(self._boxes.get(wf.name, []))

        if self.mode.uses_megapixels and manifest.can_set_megapixels:
            request.megapixels = self.megapixels.megapixels()
        if self.duration.isVisible():
            request.length = self.duration.value_for_workflow()
            self.cfg.set("video_length_default", self.duration.seconds())
        if self.batch_wrap.isVisible():
            request.batch = self.batch_spin.value()
        if manifest.has("enhancer"):
            request.enhance = self.enhance_check.isChecked()
            self.cfg.set("use_prompt_enhancer", request.enhance)

        # Locking reuses the previous run's seed so the user can change one word
        # and see only that word's effect; otherwise the runner rolls a new one.
        if manifest.has("seed"):
            self.cfg.set("lock_seed", self.seed_lock.isChecked())
            if self.seed_lock.isChecked():
                request.seed = self._locked_seed()
        return request

    def _workflow_megapixels(self, manifest, wf) -> float | None:
        """The pixel budget this workflow was built with, if it states one."""
        binding = manifest.get("megapixels") if manifest else None
        if binding is None or not wf:
            return None
        value = (wf.graph.get(binding.node) or {}).get("inputs", {}).get(binding.input)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

    def _on_megapixels_changed(self, value: float) -> None:
        # The shape list shows real pixel counts, so it has to follow.
        self.ratio_picker.set_megapixels(value)
        self.cfg.set(f"{self.mode.key}_megapixels", value)

    def _seed_is_locked(self) -> bool:
        return bool(self._manifest and self._manifest.has("seed")
                    and self.seed_lock.isChecked())

    def _locked_seed(self) -> int:
        """The seed to repeat, per mode.

        Seeds are remembered per tab: a locked Video run must not inherit the
        number an Image run happened to leave behind. If nothing has run yet
        there is nothing to repeat, so one is chosen now and kept - otherwise
        ticking the box on a fresh install would silently do nothing.
        """
        seeds = dict(self.cfg.get("locked_seeds") or {})
        seed = seeds.get(self.mode.key)
        if not seed:
            seed = random.randint(0, MAX_SEED)
            seeds[self.mode.key] = seed
            self.cfg.set("locked_seeds", seeds)
        return int(seed)

    def _remember_seed(self, seed: int | None) -> None:
        """Keep the last seed used, so ticking the box afterwards repeats it."""
        if seed is None:
            return
        seeds = dict(self.cfg.get("locked_seeds") or {})
        seeds[self.mode.key] = int(seed)
        self.cfg.set("locked_seeds", seeds)

    def _start_worker(self, wf: Workflow, request: GenerationRequest) -> None:
        """Add this run to the queue rather than starting it here.

        Every press of Create adds an item, which runs when the ones before it
        are done. The tab still follows its own item, so a single job looks
        exactly as it always did.
        """
        from app.queue import QueueItem

        item = QueueItem(
            mode=self.mode.key,
            workflow=wf,
            request=request,
            output_dir=self.cfg.output_dir(self.mode.key),
            timeout=int(self.cfg.get("job_timeout") or 1800),
            ratio_options=self._ratio_options,
        )
        self._mine.add(item.id)
        self.queue.add(item)

    # -- following our own items in the shared queue ------------------------
    def attach_queue(self, manager) -> None:
        """Given the one queue, watch it for items this tab put in."""
        self.queue = manager
        manager.item_changed.connect(self._on_queue_item)
        manager.file_ready.connect(self._on_queue_file)
        manager.preview.connect(self._on_queue_preview)

    def _on_queue_item(self, item) -> None:
        if item.id not in self._mine:
            return
        from app.queue import State

        if item.state is State.RUNNING:
            if not self._running_id:
                self._running_id = item.id
                self._set_running(True)
                self._preview_message(t("Working…"))
            self._on_progress(item.percent, item.message)
        elif item.state.finished and self._running_id == item.id:
            self._running_id = None
            self._set_running(False)
            if item.state is State.DONE and item.result is not None:
                self._on_success(item.result)
            elif item.state is State.FAILED:
                self._on_failure(item.error)
            else:
                self.status_label.setText(t(JobWorker.CANCELLED))

    def _on_queue_file(self, item, path: str) -> None:
        if item.id in self._mine:
            self._on_file_ready(path)

    def _on_queue_preview(self, item, data: bytes) -> None:
        if item.id in self._mine and self.preview is not None:
            self.preview.show_bytes(data)

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.status_label.setText(message)

    def _on_file_ready(self, path: str) -> None:
        self.gallery.add(path)
        if Path(path).suffix.lower() in _IMAGE_EXT:
            self._preview_file(path)

    def _on_success(self, result: JobResult) -> None:
        # Only unlocked runs move the remembered seed on; a locked run is
        # supposed to keep repeating the same one.
        if not self._seed_is_locked():
            self._remember_seed(result.seed)

        done = t("Done in {n} seconds", n=f"{result.elapsed:.0f}")
        bits = [done]
        if result.width:
            bits.append(f"{result.width} × {result.height}")
        if len(result.files) > 1:
            bits.append(plural(len(result.files), "{n} file", "{n} files"))
        if result.seed is not None:
            bits.append(t("seed {n}", n=result.seed))
        self.status_label.setText("   ·   ".join(bits))
        self.status_message.emit(f"{t(self.mode.label)}: {done}")

        # Anything the run could not honour. Said plainly rather than left in
        # a log, because the picture looks finished either way.
        if result.notes:
            self.status_label.setText(
                "   ·   ".join(bits) + "\n⚠  " + "\n⚠  ".join(result.notes))
            self.status_label.setStyleSheet(f"color:{theme.WARN};")
        else:
            self.status_label.setStyleSheet("")

        first = Path(result.files[0]) if result.files else None
        if first and first.suffix.lower() in _VIDEO_EXT and self.preview is not None:
            # Its opening frame, so a finished video is recognisable without
            # having to open it in another program.
            self.preview.show_video(first)
        elif first and first.suffix.lower() not in _IMAGE_EXT:
            self._preview_message(
                t("Saved {name}", name=first.name) + "\n\n"
                + t("Double-click it in the list below to play it."))

    def _on_failure(self, message: str) -> None:
        self.status_label.setText(message)
        self._preview_message(t("Nothing was made"))
        if message != t(JobWorker.CANCELLED):
            QMessageBox.warning(self, t("That did not work"), message)

    def retranslate(self) -> None:
        """Re-apply every fixed label after the language changes.

        Rebuilding the tab instead would be shorter, but it would throw away
        whatever the user has typed, the workflow they picked and everything
        made this session - a heavy price for changing a dropdown.
        """
        self.refresh_btn.setText(t("Refresh"))
        self.setup_btn.setText(t("Set up…"))
        self.setup_btn.setToolTip(
            t("Change which parts of this workflow EasyAI controls"))
        self.prompt_label.setText(t("What do you want to make?"))
        self.prompt_box.setPlaceholderText(t(self.mode.prompt_hint))
        self.negative_label.setText(t("NEGATIVE PROMPT"))
        self.negative_box.setPlaceholderText(
            t("What you do not want to see - leave empty for nothing."))
        self._on_prompt_changed()
        self.drop_hint.setText(
            t("Attach only the ones you want — leave the rest empty."))
        self.enhance_check.setText(t("Let the AI improve my wording first"))
        self.seed_lock.setText(t("Repeat the same result, so I can compare changes"))
        self.create_btn.setText(t("Create {what}", what=t(self.mode.label)))
        self.cancel_btn.setText(t("Stop"))
        self.shortcut_hint.setText(t("CTRL + ENTER  to run"))

        self.boxes_heading.setText(t("LAYOUT BOXES (OPTIONAL)"))
        self.boxes_btn.setToolTip(t(
            "Say where things go: draw a box, then describe what is in it."))
        self.boxes_clear.setText(t("Clear"))

        for widget in (self.ratio_picker, self.megapixels, self.duration,
                       self.gallery, self.lora_picker, self.model_picker):
            if hasattr(widget, "retranslate"):
                widget.retranslate()
        for zone in self.drop_zones.values():
            zone.retranslate()

        # Re-runs the status line and the drop-zone labels for whatever is
        # selected, which is where the rest of this screen's wording comes from.
        current = self.workflow_list.current_workflow()
        if current:
            self._on_workflow_picked(current)

    def _on_cancel(self) -> None:
        """Stop this tab's running item. Anything else queued carries on."""
        if self.queue is None or self._running_id is None:
            return
        for item in self.queue.items:
            if item.id == self._running_id:
                self.status_label.setText(t("Stopping…"))
                self.queue.cancel(item)
                return

    def _set_running(self, running: bool) -> None:
        """This tab has an item running in the shared queue.

        Everything stays usable. Lining up the next one while this is working
        is the whole point of a queue, so Create keeps working, the workflow
        list keeps its selection and the prompt stays editable - all three of
        which used to be locked, because only one job could ever exist.

        Stop and the progress bar simply appear alongside.
        """
        self.create_btn.setVisible(True)
        self.cancel_btn.setVisible(running)
        self.progress.setVisible(running)
        if running:
            self.progress.setValue(0)

    # ------------------------------------------------------------- shutdown
    def stop_work(self) -> None:
        """Nothing to do: the window stops the shared queue on the way out."""
        return
