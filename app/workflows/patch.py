"""Writes the user's choices into a copy of the workflow graph.

Only inputs named by the manifest are touched, and only on a deep copy - the
loaded workflow is never mutated, so a failed run leaves nothing behind.

This replaces the old approach of scanning for a class_type and writing to every
node that matched, which quietly corrupted workflows with more than one
LoadImage.
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.i18n import t
from app.ratios import (
    clamp_megapixels, read_node_profile, resolve, snap_to_output_grid, solve_for,
)
from app.workflows.manifest import (
    FILE_SLOTS, Binding, Manifest, loader_consumers,
)

# ComfyUI seeds are unsigned 64-bit, but most nodes validate against this range.
MAX_SEED = 2 ** 53 - 1


class PatchError(Exception):
    """A binding pointed at a node or input that isn't in the graph."""


@dataclass
class GenerationRequest:
    """Everything the user chose for one run."""
    prompt: str = ""
    negative: str | None = None
    ratio: str | None = None
    #: A different model from the same folder, or None to keep the one the
    #: workflow names. Only ever a value the engine offered.
    model: str | None = None
    #: Output pixel budget in megapixels. None keeps the workflow's own.
    megapixels: float | None = None
    #: Local paths keyed by file slot, e.g. {"image_in": "first.png",
    #: "image_in_2": "last.png"}. A workflow can ask for several pictures.
    files: dict[str, str | Path] = field(default_factory=dict)
    length: int | None = None
    batch: int | None = None
    seed: int | None = None          # None means "pick a fresh one"
    #: None = leave the workflow as its author set it up; False = route around
    #: the prompt enhancer; True = use it.
    enhance: bool | None = None
    #: An optional LoRA: None leaves the workflow as it is, "" means none (the
    #: loader is taken out), anything else is a file the engine offered.
    lora: str | None = None
    lora_strength: float | None = None
    #: Layout boxes from EasyAI's box editor, as app.boxes.Box objects. None
    #: leaves the workflow alone; an empty list means "no boxes", and the
    #: layout node is taken out so the prompt runs as plain text.
    boxes: list | None = None
    filename_prefix: str | None = None
    #: Filled in by the job runner after uploading, keyed by the same slots.
    uploaded: dict[str, str] = field(default_factory=dict)

    # Convenience for the single-input case and for older callers.
    @property
    def image_path(self):
        return self.files.get("image_in")

    @image_path.setter
    def image_path(self, value):
        self._set_slot("image_in", value)

    @property
    def audio_path(self):
        return self.files.get("audio_in")

    @audio_path.setter
    def audio_path(self, value):
        self._set_slot("audio_in", value)

    @property
    def video_path(self):
        return self.files.get("video_in")

    @video_path.setter
    def video_path(self, value):
        self._set_slot("video_in", value)

    def _set_slot(self, key: str, value) -> None:
        if value:
            self.files[key] = value
        else:
            self.files.pop(key, None)


@dataclass
class PatchReport:
    """What actually changed, for logging and for the progress panel."""
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    #: Whether the prompt enhancer ran, or None if the workflow has none.
    enhancer: bool | None = None
    megapixels: float | None = None
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _set(graph: dict, bindings, value, report: PatchReport,
         key: str, strict: bool = False) -> bool:
    """Write one value through every target of a binding key.

    Returns True if at least one write landed. Several targets is normal - a
    Flux 2 graph needs the same width in both the empty latent and the
    scheduler.
    """
    if bindings is None:
        return False
    if isinstance(bindings, Binding):
        bindings = [bindings]

    landed = False
    for binding in bindings:
        landed |= _set_one(graph, binding, value, report, key, strict)
    return landed


def _set_one(graph: dict, binding: Binding | None, value, report: PatchReport,
             key: str, strict: bool = False) -> bool:
    if binding is None:
        return False

    node = graph.get(binding.node)
    if not isinstance(node, dict):
        message = f"{key}: node {binding.node} is not in this workflow"
        if strict:
            raise PatchError(message)
        report.skipped.append(message)
        return False

    inputs = node.setdefault("inputs", {})
    if not binding.input:
        report.skipped.append(f"{key}: no input field set")
        return False
    if binding.input not in inputs:
        message = f"{key}: '{binding.input}' is not an input on node {binding.node}"
        if strict:
            raise PatchError(message)
        report.skipped.append(message)
        return False
    if isinstance(inputs[binding.input], list):
        # Wired from another node - overwriting would break the graph.
        report.skipped.append(
            f"{key}: '{binding.input}' is connected to another node, left alone")
        return False

    inputs[binding.input] = value
    report.applied.append(key)
    return True


def _ratio_head(option: str) -> str:
    """'9:16 (Portrait Widescreen)' -> '9:16'."""
    return option.split(" ", 1)[0].strip()


def match_ratio_option(ratio: str, options: list[str] | None, current: str) -> str | None:
    """Pick the node's own wording for a ratio, e.g. '2:3 (Portrait Photo)'.

    The label is never invented. These nodes accept a fixed list of strings and
    the descriptive half varies per entry - Square, Portrait Photo, Ultrawide -
    so guessing produces a value ComfyUI rejects. When the list is unknown, or
    the ratio simply isn't offered, None is returned and the node is left as the
    workflow author set it.
    """
    if options:
        for option in options:
            if _ratio_head(option) == ratio:
                return option
        return None

    # No list to check against: only safe if the node already holds a bare
    # ratio with no descriptive suffix.
    if current and _ratio_head(current) == current and ":" in current:
        return ratio
    return None


def prune_unused_files(graph: dict, manifest: Manifest, request: GenerationRequest,
                       report: PatchReport) -> None:
    """Remove the inputs for optional files the user chose not to supply.

    Declining to write a value is not enough. The workflow still holds the
    filename its author used - '李佳芯 Original.jpeg' - which exists on their
    machine and nobody else's, so the run would fail on a missing file. The
    input has to leave the graph entirely, which the node allows because these
    reference groups are declared optional.
    """
    # Where each touched group starts counting, read before anything is
    # removed - MiniMax numbers from ref_image_0, Qwen Image 2.1 from image_1,
    # and after the first member goes there is no telling which it was.
    touched_groups: dict[tuple[str, str], int] = {}

    for key in manifest.file_slots():
        if request.files.get(key) or not manifest.is_optional(key):
            continue

        for binding in manifest.all(key):
            for consumer_id, input_name in loader_consumers(graph, binding.node):
                consumer = graph.get(consumer_id)
                if not isinstance(consumer, dict):
                    continue
                inputs = consumer.get("inputs") or {}
                if "." in input_name:
                    group = input_name.split(".", 1)[0]
                    touched_groups.setdefault(
                        (consumer_id, group), _first_index(inputs, group))
                inputs.pop(input_name, None)

            # Drop the loader too, once nothing reads from it, so ComfyUI does
            # not try to open a file that is not there.
            if not loader_consumers(graph, binding.node):
                graph.pop(binding.node, None)

        # Recorded as done, not skipped: skipped items are shown to the user
        # as warnings, and leaving an optional picture empty is a choice the
        # run honoured. With ten reference slots it used to put eight or nine
        # warning lines under every single Qwen Image 2.1 result.
        report.applied.append(f"{key}:left out")

    for (consumer_id, group), first in sorted(touched_groups.items()):
        _renumber_group(graph, consumer_id, group, first)


def _member_index(name: str) -> int:
    digits = re.findall(r"\d+", name.rsplit(".", 1)[-1])
    return int(digits[-1]) if digits else 0


def _first_index(inputs: dict, group: str) -> int:
    """The number a group's first member carries - 0 or 1 in practice."""
    indices = [_member_index(k) for k in inputs if k.startswith(f"{group}.")]
    return min(indices) if indices else 0


def _renumber_group(graph: dict, consumer_id: str, group: str, first: int = 0) -> None:
    """Close the gaps in an auto-growing input group.

    These groups are numbered from a prefix - ref_image_0, ref_image_1 - and
    removing the middle one leaves a hole. ComfyUI accepts the hole, but the
    node reads the group by index, so renumbering keeps the remaining files in
    the positions the node expects rather than relying on that.

    ``first`` is where the group counted from before anything was removed. It
    has to be kept: renumbering Qwen Image 2.1's image_1 as image_0 gives it a
    name its node does not have, and the run fails.
    """
    inputs = (graph.get(consumer_id) or {}).get("inputs") or {}
    members = [k for k in inputs if k.startswith(f"{group}.")]
    if not members:
        return

    members.sort(key=_member_index)
    values = [inputs.pop(name) for name in members]
    stem = re.sub(r"\d+$", "", members[0].split(".", 1)[1])
    for position, value in enumerate(values, first):
        inputs[f"{group}.{stem}{position}"] = value


def _readers(graph: dict, node_id: str) -> list[tuple[str, str]]:
    """(consumer id, input name) for every input wired to a node's output."""
    found = []
    for other_id, other in graph.items():
        if str(other_id) == node_id or not isinstance(other, dict):
            continue
        for field_name, value in (other.get("inputs") or {}).items():
            if isinstance(value, list) and value and str(value[0]) == node_id:
                found.append((str(other_id), field_name))
    return found


def _bypass_node(graph: dict, node_id: str, replacement) -> int:
    """Point everything reading from a node at ``replacement``, then drop it.

    ``replacement`` is a link (``["12", 0]``) or a literal value. Returns how
    many inputs were rewired.
    """
    readers = _readers(graph, node_id)
    for consumer, field_name in readers:
        graph[consumer]["inputs"][field_name] = copy.deepcopy(replacement)
    graph.pop(node_id, None)
    return len(readers)


#: ComfySwitchNode picks one of two wired values by a boolean.
_SWITCH_CLASSES = ("comfyswitchnode",)


def _enhancer_switches(graph: dict, node_id: str) -> list[tuple[str, bool]]:
    """(switch id, value that selects the enhancer) for each switch it feeds.

    Flux 3 Klein's and MOSS-Image's workflows do not wire the enhancer straight
    to the encoder; a switch chooses between the enhanced prompt and the plain
    one. Flux 3 ships with it on the enhancer's side, MOSS on the plain side -
    so leaving the switch alone made the enhancer box do nothing for MOSS.
    """
    found = []
    for consumer, field_name in _readers(graph, node_id):
        node = graph.get(consumer) or {}
        if str(node.get("class_type", "")).lower() in _SWITCH_CLASSES \
                and field_name in ("on_true", "on_false"):
            found.append((consumer, field_name == "on_true"))
    return found


def use_enhancer(graph: dict, manifest: Manifest, report: PatchReport) -> None:
    """Make sure the enhancer's output is the one that gets used."""
    binding = manifest.get("enhancer")
    if binding is None:
        return
    for switch_id, value in _enhancer_switches(graph, binding.node):
        inputs = graph[switch_id].setdefault("inputs", {})
        if not isinstance(inputs.get("switch"), list):
            inputs["switch"] = value
    report.applied.append("enhancer:on")


def bypass_enhancer(graph: dict, manifest: Manifest, prompt_text: str,
                    report: PatchReport) -> bool:
    """Route the prompt around the enhancer node and drop it from the graph.

    An API workflow has no notion of a muted node - ComfyUI strips those at
    export - so switching the enhancer off means rewiring by hand: every input
    that reads from the enhancer is pointed at whatever fed the enhancer
    instead, and the node is then removed so its language model never loads.

    Returns True if the prompt was written inline while rewiring, in which case
    the caller must not write it again through a now-deleted node.
    """
    binding = manifest.get("enhancer")
    if binding is None:
        return False
    node = graph.get(binding.node)
    if not isinstance(node, dict):
        return False

    # What feeds the enhancer? A link if the prompt comes from another node,
    # otherwise the user's own words.
    upstream = (node.get("inputs") or {}).get(binding.input) if binding.input else None
    inlined = not isinstance(upstream, list)
    replacement = upstream if isinstance(upstream, list) else prompt_text

    rewired = _bypass_node(graph, binding.node, replacement)
    report.applied.append("enhancer:off")
    if rewired == 0:
        report.skipped.append(
            "enhancer: nothing was reading from it, so nothing needed rerouting")
    return inlined


def _target_size(patched: dict, manifest: Manifest, request: GenerationRequest,
                 report: PatchReport, ratio_options: list[str] | None,
                 ) -> tuple[tuple[int, int] | None, bool]:
    """Work out the pixels this run should produce, setting the chooser node.

    When the workflow has a size-chooser node, that node decides - so it is
    told the ratio and then asked, in effect, what it will produce. Otherwise
    the engine table answers.

    Returns (size, chooser_was_set).
    """
    if manifest.has("ratio"):
        binding = manifest.get("ratio")
        node = patched.get(binding.node) or {}
        current = str((node.get("inputs") or {}).get(binding.input, ""))
        option = match_ratio_option(request.ratio, ratio_options, current)
        if option is None:
            # Either the engine's node list was unreadable, or the chooser
            # genuinely has no such shape. Either way the picture comes out at
            # the workflow's own setting, which is not what was asked for -
            # so this has to reach the user, not just a log nobody sees.
            report.skipped.append(t(
                "The shape stayed at this workflow's own setting: {ratio} could "
                "not be applied. Check the AI engine is running, then press "
                "Refresh.", ratio=request.ratio))
            return None, False
        applied = _set(patched, [binding], option, report, "ratio")

        # read_node_profile sees the megapixels already written above.
        profile = read_node_profile(node)
        if profile:
            return (snap_to_output_grid(solve_for(request.ratio, *profile),
                                        manifest.engine), applied)

    size = resolve(request.ratio, manifest.engine, megapixels=request.megapixels)
    return snap_to_output_grid(size, manifest.engine), False


def apply(graph: dict, manifest: Manifest, request: GenerationRequest,
          ratio_options: list[str] | None = None) -> tuple[dict, PatchReport]:
    """Return a patched deep copy of the graph plus a report of what changed.

    ``ratio_options`` is the list a size-chooser node accepts, read from the
    running ComfyUI. Without it a ratio node is left alone rather than written
    with a made-up label.
    """
    patched = copy.deepcopy(graph)
    report = PatchReport()

    # --- prompt enhancer --------------------------------------------------
    # Done first, because switching it off can remove the very node the prompt
    # would otherwise be written into.
    prompt_already_written = False
    if manifest.has("enhancer"):
        if request.enhance is False:
            prompt_already_written = bypass_enhancer(
                patched, manifest, request.prompt or "", report)
            report.enhancer = False
        elif request.enhance is True:
            use_enhancer(patched, manifest, report)
            report.enhancer = True
        else:
            report.enhancer = True
            report.applied.append("enhancer:on")

    # --- text -------------------------------------------------------------
    if request.prompt is not None and not prompt_already_written:
        _set(patched, manifest.all("prompt"), request.prompt, report, "prompt")
    if request.negative is not None:
        _set(patched, manifest.all("negative"), request.negative, report, "negative")

    # --- input files (already uploaded, so these are ComfyUI-side names) ---
    # Each slot is a separate picture/sound, so each gets its own value.
    for key in FILE_SLOTS:
        name = request.uploaded.get(key)
        if name:
            _set(patched, manifest.all(key), name, report, key)

    # Anything optional the user skipped is taken out of the graph rather than
    # left pointing at a file only the workflow's author has.
    prune_unused_files(patched, manifest, request, report)

    # --- the model --------------------------------------------------------
    # Done before the size, because a workflow may size itself from whichever
    # model is loaded.
    if request.model and manifest.has("model"):
        _set(patched, manifest.all("model"), request.model, report, "model")

    # --- an optional LoRA -------------------------------------------------
    apply_lora(patched, manifest, request, report)

    # --- size -------------------------------------------------------------
    # A megapixels input means the workflow sizes itself from a pixel budget,
    # so hand it the number and let it do the arithmetic. Done first, so the
    # size worked out below reflects the new budget.
    if request.megapixels is not None and manifest.has("megapixels"):
        budget = clamp_megapixels(request.megapixels)
        if _set(patched, manifest.all("megapixels"), budget, report, "megapixels"):
            report.megapixels = budget

    if request.ratio:
        target, chooser_set = _target_size(
            patched, manifest, request, report, ratio_options)
        if target:
            # Write the result into every literal width/height as well. A
            # workflow can have both a chooser node and a node holding plain
            # numbers - Flux 2 keeps the latent size on the chooser but the
            # scheduler on literals - and if the two disagree the sampler is
            # scheduled for a different picture than it is asked to draw.
            wrote = _set(patched, manifest.all("width"), int(target[0]), report, "width")
            wrote |= _set(patched, manifest.all("height"), int(target[1]), report, "height")
            # Only claim a size when something will actually act on it. Width
            # and height wired from another node are left alone, and reporting
            # our number would promise a picture the workflow will not make.
            if chooser_set or wrote:
                report.width, report.height = int(target[0]), int(target[1])

    # --- seed -------------------------------------------------------------
    # A workflow ships with a fixed seed, so without this every user gets an
    # identical result. Randomise unless the user asked to lock it.
    seed = request.seed if request.seed is not None else random.randint(0, MAX_SEED)
    if _set(patched, manifest.all("seed"), int(seed), report, "seed"):
        report.seed = int(seed)

    # --- optional extras --------------------------------------------------
    if request.length is not None:
        _set(patched, manifest.all("length"), int(request.length), report, "length")
    if request.batch is not None:
        _set(patched, manifest.all("batch"), int(request.batch), report, "batch")
    if request.filename_prefix:
        _set(patched, manifest.all("output"), request.filename_prefix, report, "output")

    # --- layout boxes -----------------------------------------------------
    # Last, because the canvas has to match the size worked out above.
    apply_boxes(patched, manifest, request, report)

    return patched, report


def apply_lora(graph: dict, manifest: Manifest, request: GenerationRequest,
               report: PatchReport) -> None:
    """Load the chosen LoRA, or take the loader out when none is wanted.

    A LoRA loader cannot be told "no file": ComfyUI checks the name against
    the files it has, and the author's file is on the author's machine. So
    "None" means routing the model around the loader entirely.
    """
    binding = manifest.get("lora")
    if binding is None or request.lora is None:
        return
    node = graph.get(binding.node)
    if not isinstance(node, dict):
        report.skipped.append(f"lora: node {binding.node} is not in this workflow")
        return
    if not request.lora:
        source = (node.get("inputs") or {}).get("model")
        if isinstance(source, list):
            _bypass_node(graph, binding.node, source)
            report.applied.append("lora:none")
        else:
            report.skipped.append("lora: the loader has no model input to route around")
        return
    _set(graph, [binding], request.lora, report, "lora")
    strength = 1.0 if request.lora_strength is None else float(request.lora_strength)
    _set(graph, manifest.all("lora_strength"), strength, report, "lora_strength")


def _picture_size(path) -> tuple[int, int] | None:
    try:
        from PIL import Image
        with Image.open(path) as picture:
            return picture.size
    except Exception:                                   # noqa: BLE001
        return None


def _snap16(value: float) -> int:
    return max(64, int(round(value / 16)) * 16)


def _canvas_size(manifest: Manifest, node: dict, request: GenerationRequest,
                 report: PatchReport) -> tuple[int, int]:
    """The pixel grid the boxes are drawn on.

    An edit draws on its picture - the canvas's background is the reference
    the user attached - so the canvas takes that picture's shape. A new image
    draws on the frame being generated, whose size the steps above settled.
    """
    inputs = node.get("inputs") or {}
    background = inputs.get("background")
    if isinstance(background, list) and background:
        loader = str(background[0])
        for key in manifest.file_slots():
            path = request.files.get(key)
            if path and any(b.node == loader for b in manifest.all(key)):
                size = _picture_size(path)
                if size:
                    return _snap16(size[0]), _snap16(size[1])
    if report.width and report.height:
        return int(report.width), int(report.height)
    width, height = inputs.get("width"), inputs.get("height")
    if isinstance(width, int) and isinstance(height, int):
        return width, height
    return 1024, 1024


def apply_boxes(graph: dict, manifest: Manifest, request: GenerationRequest,
                report: PatchReport) -> None:
    """Hand EasyAI's layout boxes to the workflow, or take the layout out."""
    from app import boxes as _boxes

    canvas_binding, layout_binding = manifest.get("boxes"), manifest.get("layout")
    if canvas_binding is None or layout_binding is None or request.boxes is None:
        return
    canvas = graph.get(canvas_binding.node)
    layout = graph.get(layout_binding.node)
    if not isinstance(canvas, dict) or not isinstance(layout, dict):
        report.skipped.append("boxes: the box nodes are not in this workflow")
        return

    if not request.boxes:
        # No boxes: the prompt goes on as plain text. A layout node fed a link
        # is routed around; one holding the prompt itself becomes a plain text
        # node in place, so whatever reads it still reads the same words.
        prompt = (layout.get("inputs") or {}).get(layout_binding.input or "prompt")
        if isinstance(prompt, list):
            _bypass_node(graph, layout_binding.node, prompt)
        else:
            graph[layout_binding.node] = {
                "class_type": "PrimitiveStringMultiline",
                "inputs": {"value": prompt if isinstance(prompt, str) else ""},
                "_meta": {"title": (layout.get("_meta") or {}).get("title") or "Prompt"},
            }
        if not _readers(graph, canvas_binding.node):
            graph.pop(canvas_binding.node, None)
        report.applied.append("boxes:none")
        return

    width, height = _canvas_size(manifest, canvas, request, report)
    inputs = canvas.setdefault("inputs", {})
    for name, value in (("width", width), ("height", height)):
        if not isinstance(inputs.get(name), list):
            inputs[name] = value
    # Lists in an API prompt are links; ComfyUI takes a literal list only
    # wrapped like this, which is also how it exports one.
    inputs[canvas_binding.input or "editor_state"] = {
        "__value__": _boxes.to_editor_state(request.boxes, width, height)}
    inputs["last_incoming"] = {"__value__": []}

    layout_inputs = layout.setdefault("inputs", {})
    for name, value in (("width", width), ("height", height)):
        if name in layout_inputs and not isinstance(layout_inputs[name], list):
            layout_inputs[name] = value
    if "character_boxes" in layout_inputs:
        layout_inputs["character_boxes"] = _boxes.character_numbers(request.boxes)
    report.applied.append(f"boxes:{len(request.boxes)}")


def missing_inputs(manifest: Manifest, request: GenerationRequest) -> list[str]:
    """User-facing complaints to raise before queueing anything."""
    problems = []
    if manifest.has("prompt") and not (request.prompt or "").strip():
        problems.append("Please type a prompt first.")

    supplied = 0
    for key in manifest.file_slots():
        label = manifest.label_for(key)
        path = request.files.get(key)
        if path:
            supplied += 1
            if not Path(path).is_file():
                problems.append(f"“{label}” no longer exists:\n{path}")
        elif not manifest.is_optional(key):
            # A required loader cannot be left empty - the filename baked into
            # the workflow points at the author's machine, not the user's.
            problems.append(f"Please choose a file for “{label}”.")

    # Reference workflows let you pick and choose, but running one with nothing
    # attached is almost never what someone meant, so ask for one.
    if supplied == 0 and manifest.has_optional_files:
        problems.append("Please attach at least one file to work from.")
    return problems
