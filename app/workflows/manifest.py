"""The sidecar file that says which node does what.

An API-format workflow is just a bag of nodes; nothing in it marks "this is the
prompt". A manifest names the handful of inputs EasyAI is allowed to touch:

    prompt, negative, image_in, audio_in, video_in, width, height, seed,
    length, ratio, batch, output

It is written next to the workflow as ``<name>.manifest.json`` so it can be read,
diffed and hand-edited. If none exists we generate one by inspecting the graph,
write it once, and never silently regenerate - so user edits are never lost.

Two rules drive every detection:

*Node ids are strings and not always numeric.* Subgraph-expanded workflows use
ids like ``"122:107"``. Never coerce to int, never sort numerically.

*Linked inputs are off limits.* In API format ``"width": ["113", 0]`` means the
value is wired from another node. Writing a literal over that link breaks the
graph, so any list-valued input is invisible to detection.
"""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ratios import guess_engine

MANIFEST_SUFFIX = ".manifest.json"

#: Bump when a new binding key is added, so manifests written by an older
#: version get the new key filled in. Without this, a workflow saved before the
#: key existed would never gain the control it enables.
#:   1 - original
#:   2 - added "megapixels"
MANIFEST_VERSION = 3

#: How many separate pictures one workflow may ask for. Ten covers the
#: "Flux 2 with 10 Ref Image" style; first/last frame video needs two or three.
MAX_IMAGE_SLOTS = 10

IMAGE_SLOTS = ("image_in",) + tuple(f"image_in_{i}" for i in range(2, MAX_IMAGE_SLOTS + 1))
AUDIO_SLOTS = ("audio_in", "audio_in_2")
VIDEO_SLOTS = ("video_in", "video_in_2")

#: Every slot that takes a file from the user, in the order they are offered.
FILE_SLOTS = IMAGE_SLOTS + AUDIO_SLOTS + VIDEO_SLOTS

#: Every binding EasyAI understands, in editor display order.
BINDING_KEYS = (
    ("prompt", "negative")
    + FILE_SLOTS
    + ("model", "lora", "lora_strength", "width", "height", "ratio",
       "megapixels", "seed", "length", "batch", "enhancer", "boxes", "layout",
       "output")
)

#: Bindings the user can be shown a control for.
CONTROL_KEYS = ("prompt", "negative") + FILE_SLOTS + ("length", "batch")


def slot_kind(key: str) -> str:
    """'image' | 'audio' | 'video' for a file slot, '' for anything else."""
    if key in IMAGE_SLOTS:
        return "image"
    if key in AUDIO_SLOTS:
        return "audio"
    if key in VIDEO_SLOTS:
        return "video"
    return ""

# --- what detection looks for --------------------------------------------
_TEXT_INPUT_NAMES = ("text", "value", "prompt", "prompt_text", "string",
                     "positive", "positive_prompt")
_NEG_INPUT_NAMES = ("negative", "negative_prompt", "text_negative")
_NEGATIVE_WORDS = re.compile(r"\bneg(ative)?\b", re.I)
_POSITIVE_WORDS = re.compile(r"\bpos(itive)?\b|\bprompt\b", re.I)
_SYSTEM_WORDS = re.compile(r"\bsystem\b|\binstructions?\b", re.I)

# Classes that usually hold the text a user is meant to edit, best first.
_TEXT_CLASS_RANK = (
    "primitivestringmultiline", "primitivestring", "string", "stringliteral",
    "cliptextencode", "textencodeqwenimageedit", "cr prompt text",
)

_IMAGE_LOADERS = {"loadimage": "image", "loadimagemask": "image",
                  "loadimageoutput": "image", "easy loadimagebase64": "image"}
_AUDIO_LOADERS = {"loadaudio": "audio", "vhs_loadaudio": "audio",
                  "vhs_loadaudioupload": "audio"}
_VIDEO_LOADERS = {"loadvideo": "video", "vhs_loadvideo": "video",
                  "vhs_loadvideopath": "video"}

_SEED_INPUT_NAMES = ("noise_seed", "seed", "rand_seed")
_LENGTH_INPUT_NAMES = ("length", "num_frames", "frames", "video_frames",
                       "frame_count", "duration", "seconds")

#: Nodes with a "length" input that has nothing to do with how long the video
#: is - ImageFromBatch.length means "take this many images from a batch".
_LENGTH_EXCLUDE = ("frombatch", "imagebatch", "repeatimage", "batchindex")

#: Titles on the primitive that actually holds the duration. Every video
#: workflow here computes frames from seconds with a maths node, so the number
#: worth exposing is upstream of the frame count, not the frame count itself.
_DURATION_WORDS = re.compile(r"\b(duration|seconds?|secs?|length)\b", re.I)
_FPS_WORDS = re.compile(r"\b(frame\s*rate|fps|framerate)\b", re.I)
_FPS_INPUT_NAMES = ("fps", "frame_rate", "framerate")
#: Frame rates common enough that a bare number is more likely fps than seconds.
_LIKELY_FPS = {8, 12, 16, 24, 25, 30, 48, 50, 60}
_RATIO_INPUT_NAMES = ("aspect_ratio", "ratio", "aspect")

_SAVE_CLASSES = ("saveimage", "savevideo", "saveaudio", "saveaudiomp3",
                 "saveaudioopus", "vhs_videocombine", "save image", "image save")
_PREVIEW_CLASSES = ("previewimage", "previewaudio", "previewvideo")

# Size nodes are recognised by having both width and height as literals, but a
# name hint breaks ties when a workflow has several.
_SIZE_CLASS_HINTS = ("empty", "latent", "resolution", "size", "image scale",
                     "scheduler", "dimensions")


def _is_literal(value: Any) -> bool:
    """True if an input holds a value rather than a link to another node."""
    return not isinstance(value, (list, tuple))


@dataclass
class Binding:
    """One editable input: which node, which field on it."""
    node: str
    input: str

    def to_json(self) -> dict:
        return {"node": self.node, "input": self.input}

    @staticmethod
    def from_json(raw: Any) -> "Binding | None":
        if not isinstance(raw, dict):
            return None
        node, inp = raw.get("node"), raw.get("input")
        if not node:
            return None
        return Binding(node=str(node), input=str(inp) if inp else "")


def _as_binding_list(raw: object) -> list[Binding]:
    """Accept a single binding, a list of them, or nothing."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [b for b in (Binding.from_json(item) for item in raw) if b]
    single = Binding.from_json(raw)
    return [single] if single else []


@dataclass
class Manifest:
    """One binding key can drive several inputs.

    Flux 2 workflows are the reason: both ``EmptyFlux2LatentImage`` and
    ``Flux2Scheduler`` take width and height, and they have to agree or the
    sampler is scheduled for a different resolution than the latent it is
    given. So every key holds a list, even though most hold exactly one.
    """
    name: str = ""
    mode: str = "image"
    engine: str | None = None
    bindings: dict[str, list[Binding]] = field(default_factory=dict)
    #: What to call each file slot in the UI, e.g. {"image_in": "First frame"}.
    labels: dict[str, str] = field(default_factory=dict)
    #: File slots the user may leave empty. The inputs they feed get removed
    #: from the graph rather than left holding the workflow author's own
    #: filename, which would not exist on anyone else's machine.
    optional_slots: list[str] = field(default_factory=list)
    #: Whether the 'length' binding is measured in "seconds" or "frames".
    length_unit: str = "seconds"
    #: Frame rate, so seconds can be shown as frames and back.
    fps: int | None = None
    #: Reference-picture groups EasyAI filled out to the node's real capacity,
    #: as written by app.workflows.grow.plan. Kept so the same slots exist when
    #: the workflow loads with ComfyUI closed.
    grow: list[dict] = field(default_factory=list)
    expose: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    autodetected: bool = True
    notes: str = ""
    version: int = MANIFEST_VERSION

    # -- access ------------------------------------------------------------
    def all(self, key: str) -> list[Binding]:
        value = self.bindings.get(key)
        if not value:
            return []
        # Tolerate a bare Binding assigned straight into the dict.
        return [value] if isinstance(value, Binding) else list(value)

    def get(self, key: str) -> Binding | None:
        """The primary target - what the editor shows and tests assert on."""
        found = self.all(key)
        return found[0] if found else None

    def set(self, key: str, value: Binding | list[Binding] | None) -> None:
        if value is None:
            self.bindings[key] = []
        elif isinstance(value, list):
            self.bindings[key] = list(value)
        else:
            self.bindings[key] = [value]

    def has(self, key: str) -> bool:
        return bool(self.all(key))

    def is_exposed(self, key: str) -> bool:
        return key in self.expose and self.has(key)

    # -- file inputs -------------------------------------------------------
    def label_for(self, key: str) -> str:
        """What to call a slot in the UI."""
        stored = (self.labels or {}).get(key)
        if stored:
            return stored
        kind = slot_kind(key)
        default = {"image": "Picture", "audio": "Sound file", "video": "Video"}.get(kind, key)
        slots = {"image": IMAGE_SLOTS, "audio": AUDIO_SLOTS, "video": VIDEO_SLOTS}.get(kind)
        if slots and len(self.file_slots(kind)) > 1:
            return f"{default} {slots.index(key) + 1}"
        return default

    def file_slots(self, kind: str = "") -> list[str]:
        """Bound file slots, in the order they should be shown."""
        return [k for k in FILE_SLOTS
                if self.has(k) and (not kind or slot_kind(k) == kind)]

    def is_optional(self, key: str) -> bool:
        """May this slot be left empty?"""
        return key in (self.optional_slots or [])

    def grown_slot_groups(self, graph: dict) -> list[list[str]]:
        """The file slots of each grown reference group, in the node's order.

        The UI shows these one at a time - the next appears once the one
        before it is filled - because the node numbers pictures by position.
        A gap would silently turn the third picture into ``<image 2>``.
        """
        from app.workflows import grow as _grow

        by_node = {b.node: key for key in self.file_slots() for b in self.all(key)}
        groups = []
        for spec in self.grow or []:
            keys = [by_node[n] for n in _grow.member_loaders(graph, spec) if n in by_node]
            if len(keys) > 1:
                groups.append(keys)
        return groups

    @property
    def has_optional_files(self) -> bool:
        return any(self.is_optional(k) for k in self.file_slots())

    @property
    def can_set_size(self) -> bool:
        return self.has("width") and self.has("height")

    @property
    def can_set_ratio(self) -> bool:
        return self.can_set_size or self.has("ratio")

    @property
    def can_set_megapixels(self) -> bool:
        """True when the output's pixel count can be changed.

        Either the workflow has a megapixels input, or it takes width and
        height literals we can recompute for a different budget.
        """
        return self.has("megapixels") or self.can_set_size

    # -- serialisation -----------------------------------------------------
    def to_json(self) -> dict:
        def encode(key: str):
            found = self.all(key)
            if not found:
                return None
            # Keep the common single-target case readable in the file.
            return found[0].to_json() if len(found) == 1 else [b.to_json() for b in found]

        return {
            "version": self.version,
            "name": self.name,
            "mode": self.mode,
            "engine": self.engine,
            "bindings": {key: encode(key) for key in BINDING_KEYS if encode(key) is not None},
            "labels": {k: v for k, v in (self.labels or {}).items() if v},
            "optional_slots": list(self.optional_slots or []),
            "length_unit": self.length_unit,
            "fps": self.fps,
            "grow": [dict(g) for g in (self.grow or [])],
            "expose": list(self.expose),
            "ambiguous": list(self.ambiguous),
            "autodetected": self.autodetected,
            "notes": self.notes,
        }

    @staticmethod
    def from_json(raw: dict) -> "Manifest":
        bindings_raw = raw.get("bindings") or {}
        return Manifest(
            name=str(raw.get("name") or ""),
            mode=str(raw.get("mode") or "image"),
            engine=raw.get("engine") or None,
            bindings={k: _as_binding_list(bindings_raw.get(k)) for k in BINDING_KEYS},
            labels={str(k): str(v) for k, v in (raw.get("labels") or {}).items()},
            optional_slots=[str(x) for x in (raw.get("optional_slots") or [])],
            length_unit=str(raw.get("length_unit") or "seconds"),
            fps=int(raw["fps"]) if raw.get("fps") else None,
            grow=[dict(g) for g in (raw.get("grow") or []) if isinstance(g, dict)],
            expose=[str(x) for x in (raw.get("expose") or [])],
            ambiguous=[str(x) for x in (raw.get("ambiguous") or [])],
            autodetected=bool(raw.get("autodetected", True)),
            notes=str(raw.get("notes") or ""),
            version=int(raw.get("version") or MANIFEST_VERSION),
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The temp name carries the process id: the app and a second copy (or a
        # script) can both be scanning the same folder, and a shared temp name
        # lets one truncate the other's half-written file.
        tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_json(), f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    # -- health ------------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        return not any(self.all(key) for key in BINDING_KEYS)

    def broken_bindings(self, graph: dict) -> list[str]:
        """Binding keys pointing at nodes or inputs this graph does not have."""
        broken = []
        for key in BINDING_KEYS:
            for binding in self.all(key):
                node = graph.get(binding.node)
                if not isinstance(node, dict):
                    broken.append(key)
                    break
                if binding.input and binding.input not in (node.get("inputs") or {}):
                    broken.append(key)
                    break
        return broken

    def describes(self, graph: dict) -> bool:
        """Is this manifest actually about this graph?

        A manifest can be left over from an earlier version of the workflow -
        re-exporting renumbers nodes, and an editor file replaced by a proper
        API export shares nothing with what came before.
        """
        if self.is_empty:
            return False
        return not self.broken_bindings(graph)

    @staticmethod
    def load(path: str | Path) -> "Manifest":
        with open(path, "r", encoding="utf-8") as f:
            return Manifest.from_json(json.load(f))


def manifest_path_for(workflow_path: str | Path) -> Path:
    """workflows/image/flux2.json -> workflows/image/flux2.manifest.json"""
    p = Path(workflow_path)
    return p.with_name(p.stem + MANIFEST_SUFFIX)


# --- detection ------------------------------------------------------------
def _title(node: dict) -> str:
    return str((node.get("_meta") or {}).get("title") or "")


def _cls(node: dict) -> str:
    return str(node.get("class_type") or "")


def _iter_nodes(graph: dict):
    """(node_id, node) for well-formed nodes only. Ids stay strings."""
    for node_id, node in graph.items():
        if isinstance(node, dict) and "class_type" in node:
            yield str(node_id), node


def _class_rank(class_type: str) -> int:
    low = class_type.lower()
    for i, name in enumerate(_TEXT_CLASS_RANK):
        if name in low:
            return i
    return len(_TEXT_CLASS_RANK)


def _consumer_edges(graph: dict) -> dict[str, list[tuple[str, str]]]:
    """source_node_id -> [(consumer_node_id, consumer_input_name), ...]."""
    edges: dict[str, list[tuple[str, str]]] = {}
    for node_id, node in _iter_nodes(graph):
        for input_name, value in (node.get("inputs") or {}).items():
            # A link is ["source_node_id", output_index].
            if isinstance(value, (list, tuple)) and value:
                src = str(value[0])
                edges.setdefault(src, []).append((node_id, input_name.lower()))
    return edges


#: Role of a node feeding both of a sampler's conditionings. Internal only;
#: _role_map never returns it.
_BOTH = "both"


def _role_map(graph: dict) -> dict[str, str]:
    """Work out which nodes are the positive prompt and which are the negative.

    Titles are unreliable - real workflows have two encoders both called
    "CLIP Text Encode (Prompt)". What is reliable is where their output goes: a
    sampler takes inputs literally named ``positive`` and ``negative``. Roles are
    then propagated back up the chain so a PrimitiveString feeding an encoder
    feeding a sampler is labelled too.
    """
    edges = _consumer_edges(graph)
    roles: dict[str, str] = {}

    for src, consumers in edges.items():
        names = [input_name for _, input_name in consumers]
        feeds_negative = any("negative" in n for n in names)
        feeds_positive = any("positive" in n for n in names)
        if feeds_negative and feeds_positive:
            # One encoder producing both conditionings - Qwen Image 2.1's
            # TextEncodeQwenImage21 takes `prompt` and `negative_prompt` and
            # feeds the sampler's positive AND negative. It has no single role,
            # so what feeds it is judged by which of its inputs it reaches.
            roles[src] = _BOTH
        elif feeds_negative:
            roles[src] = "negative"
        elif feeds_positive:
            roles[src] = "positive"

    # Walk upstream: a node inherits the role of what it feeds, as long as
    # everything it feeds agrees. Graphs are shallow here, so a few passes do.
    for _ in range(8):
        changed = False
        for src, consumers in edges.items():
            if src in roles:
                continue
            downstream = set()
            for dst, input_name in consumers:
                role = roles.get(dst)
                if role == _BOTH:
                    role = "negative" if "negative" in input_name else "positive"
                downstream.add(role)
            downstream.discard(None)
            if len(downstream) == 1:
                roles[src] = downstream.pop()
                changed = True
        if not changed:
            break
    return {node: role for node, role in roles.items() if role != _BOTH}


def _find_text_fields(graph: dict) -> tuple[list[tuple[str, str, dict]], list[tuple[str, str, dict]]]:
    """Split every editable string input into (positive candidates, negative candidates)."""
    positives: list[tuple[str, str, dict]] = []
    negatives: list[tuple[str, str, dict]] = []
    roles = _role_map(graph)

    for node_id, node in _iter_nodes(graph):
        title = _title(node)
        for input_name, value in (node.get("inputs") or {}).items():
            if not (_is_literal(value) and isinstance(value, str)):
                continue
            low_name = input_name.lower()

            # Most trustworthy first: what the graph does with this node's
            # output, then the field's own name, then the node title.
            role = roles.get(node_id)
            if low_name in _NEG_INPUT_NAMES:
                role = "negative"
            elif role is None:
                if _NEGATIVE_WORDS.search(title):
                    role = "negative"
                elif low_name in _TEXT_INPUT_NAMES:
                    role = "positive"
                else:
                    continue

            if low_name not in _TEXT_INPUT_NAMES and low_name not in _NEG_INPUT_NAMES:
                continue
            (negatives if role == "negative" else positives).append((node_id, input_name, node))

    return positives, negatives


#: How much an input's own name suggests it holds the user's prompt. A node
#: that calls its input "prompt" is telling us outright; "text" is generic and
#: is just as likely to be a fixed quality tag on a second pass.
_INPUT_NAME_RANK = {
    "prompt": 0, "prompt_text": 0, "positive_prompt": 0,
    "value": 1,
    "text": 2,
    "positive": 2,
    "string": 3,
}


def _pick_text(cands: list[tuple[str, str, dict]], graph_order: list[str]) -> tuple[Binding | None, bool]:
    """Choose the best text field. Returns (binding, was_ambiguous)."""
    if not cands:
        return None, False
    if len(cands) == 1:
        node_id, input_name, _ = cands[0]
        return Binding(node_id, input_name), False

    def rank(item):
        """How good a candidate is, ignoring where it sits in the file."""
        _, input_name, node = item
        title = _title(node)
        return (
            # A language model's standing instructions - "Text (System
            # prompt)" in MOSS-Image's workflow - sit in a text box like any
            # other, but are never what the user typed.
            1 if _SYSTEM_WORDS.search(title) else 0,
            # The input's own name is the most trustworthy signal. In a MiniMax
            # workflow the real prompt sits on MiniMaxH3ImageToVideo.prompt
            # while a second pass carries a CLIPTextEncode.text of "high
            # quality 4k" - going by node class alone picks the wrong one.
            _INPUT_NAME_RANK.get(input_name.lower(), 4),
            0 if _POSITIVE_WORDS.search(title) else 1,    # "Positive Prompt" beats untitled
            _class_rank(_cls(node)),                      # primitive strings next
        )

    ranked = sorted(cands, key=lambda item: (rank(item), graph_order.index(item[0])))
    best = ranked[0]
    # Only call it ambiguous when nothing separates the top two on merit.
    # Several candidates with a clear winner is the normal case, and flagging
    # those just trains the user to ignore the warning.
    tied = rank(ranked[0]) == rank(ranked[1])
    return Binding(best[0], best[1]), tied


#: Words that say where a frame belongs in a sequence. Real workflows title
#: their loaders "FIRST FRAME" / "Middle IMG" / "End IMG", and node ids do not
#: follow that order - in one file LAST is node 47 and FIRST is node 45 - so the
#: words have to win over file order.
_SEQUENCE_WORDS = (
    (re.compile(r"\b(first|start|begin|opening|head)\b", re.I), -1000),
    (re.compile(r"\b(middle|mid)\b", re.I), 0),
    (re.compile(r"\b(last|end|final|closing|tail)\b", re.I), 1000),
)

#: Titles that carry no information, so we fall back to something better.
_GENERIC_TITLES = {"load image", "loadimage", "image", "load image (from output)",
                   "load audio", "loadaudio", "load video", "loadvideo"}


def _humanise(raw: str) -> str:
    """'start_image' -> 'Start image';  'image_2' -> 'Image 2'.

    Dotted names come from grouped inputs like ``ref_images.ref_image_0``; only
    the last part says anything useful.
    """
    text = raw.rsplit(".", 1)[-1]
    text = re.sub(r"[_\-]+", " ", text).strip()
    text = re.sub(r"(?<=[a-zA-Z])(?=\d)", " ", text)   # image2 -> image 2
    return text[:1].upper() + text[1:] if text else raw


def _consumer_names(edges: dict[str, list[tuple[str, str]]], node_id: str) -> list[str]:
    return [name for _, name in edges.get(node_id, [])]


def _slot_hint(node: dict, consumers: list[str]) -> tuple[str, float]:
    """Work out a label and a sort position for one loader node.

    Looks at the node's own title first, then at the name of the input it feeds
    - both are used in the wild, and either beats guessing.
    """
    title = _title(node).strip()
    informative_title = title and title.lower() not in _GENERIC_TITLES

    # An input name like "image5" or "start_image" is informative; a bare
    # "image" or "images" is not.
    named = [c for c in consumers if c not in ("image", "images", "audio", "video", "input")]

    source = title if informative_title else (named[0] if named else "")

    rank: float | None = None
    for pattern, position in _SEQUENCE_WORDS:
        if pattern.search(source):
            rank = position
            break
    if rank is None:
        digits = re.findall(r"\d+", source)
        if digits:
            rank = float(digits[-1])

    label = _humanise(source) if source else ""
    return label, (rank if rank is not None else float("nan"))


def _find_loaders(graph: dict, table: dict[str, str], slots: tuple[str, ...],
                  graph_order: list[str], edges: dict[str, list[tuple[str, str]]],
                  fallback_label: str) -> tuple[dict[str, Binding], dict[str, str], bool]:
    """Assign every file-loading node of one kind to a numbered slot.

    Returns (bindings by slot key, labels by slot key, more_than_fits).
    """
    found: list[tuple[str, str, str, float]] = []   # node_id, field, label, rank
    for node_id, node in _iter_nodes(graph):
        field_name = table.get(_cls(node).lower())
        if not field_name:
            continue
        if not _is_literal((node.get("inputs") or {}).get(field_name)):
            continue
        label, rank = _slot_hint(node, _consumer_names(edges, node_id))
        found.append((node_id, field_name, label, rank))

    if not found:
        return {}, {}, False

    # Sort by the sequence hint where there is one, otherwise by file order.
    # NaN ranks (no hint at all) sort last but keep their relative order.
    def sort_key(item):
        node_id, _, _, rank = item
        has_rank = rank == rank              # False only for NaN
        return (0 if has_rank else 1,
                rank if has_rank else 0.0,
                graph_order.index(node_id))

    found.sort(key=sort_key)

    bindings: dict[str, Binding] = {}
    labels: dict[str, str] = {}
    for index, (node_id, field_name, label, _) in enumerate(found[:len(slots)]):
        key = slots[index]
        bindings[key] = Binding(node_id, field_name)
        labels[key] = label or (fallback_label if len(found) == 1
                                else f"{fallback_label} {index + 1}")

    return bindings, labels, len(found) > len(slots)


def _live_nodes(graph: dict) -> set[str]:
    """Nodes whose work reaches a saved or previewed result.

    ComfyUI only runs what an output node needs, so a node left lying in the
    graph does nothing - Flux 3 Klein's edit workflow keeps an
    EmptyFlux2LatentImage that feeds nothing, while the real size comes from
    the reference pictures. Binding the size controls to it would offer a
    control that changes nothing.

    When no recognisable, wired output node exists, every node counts as
    live: calling a whole workflow dead would be worse than an idle control.
    """
    nodes = dict(_iter_nodes(graph))
    consumed = set(_consumer_edges(graph))
    sinks = [node_id for node_id, node in nodes.items()
             if node_id not in consumed
             and any(k in _cls(node).lower() for k in _SAVE_CLASSES + _PREVIEW_CLASSES
                     + ("save", "preview"))]
    # A result node wired to nothing is a fragment, not a workflow - nothing
    # can be said about which of its nodes run.
    if not any(isinstance(v, (list, tuple)) and v
               for node_id in sinks for v in (nodes[node_id].get("inputs") or {}).values()):
        return set(nodes)

    live: set[str] = set()
    stack = list(sinks)
    while stack:
        node_id = stack.pop()
        if node_id in live or node_id not in nodes:
            continue
        live.add(node_id)
        for value in (nodes[node_id].get("inputs") or {}).values():
            if isinstance(value, (list, tuple)) and value:
                stack.append(str(value[0]))
    return live


def _find_size(graph: dict, graph_order: list[str]) -> tuple[list[Binding], list[Binding], bool]:
    """Find every node exposing width and height as editable literals.

    All size-like nodes are bound, not just the best one. A Flux 2 graph has
    both an ``EmptyFlux2LatentImage`` and a ``Flux2Scheduler``, and setting only
    one leaves the sampler scheduling for the wrong resolution. Nodes that don't
    look like size nodes are left out unless nothing else matched.
    """
    hinted: list[str] = []
    others: list[str] = []
    live = _live_nodes(graph)
    for node_id, node in _iter_nodes(graph):
        if node_id not in live or _cls(node).lower() in _BOX_CLASSES:
            continue
        inputs = node.get("inputs") or {}
        w, h = inputs.get("width"), inputs.get("height")
        if isinstance(w, (int, float)) and isinstance(h, (int, float)) \
                and _is_literal(w) and _is_literal(h):
            low = _cls(node).lower()
            (hinted if any(k in low for k in _SIZE_CLASS_HINTS) else others).append(node_id)

    chosen = hinted or others[:1]
    if not chosen:
        return [], [], False

    chosen.sort(key=graph_order.index)
    widths = [Binding(n, "width") for n in chosen]
    heights = [Binding(n, "height") for n in chosen]
    # Only flag for review when we ignored something, not when we bound several.
    return widths, heights, bool(hinted and others)


def _find_all_seeds(graph: dict, graph_order: list[str]) -> list[Binding]:
    """Every seed the user's run should randomise.

    Multi-stage workflows have more than one - a MiniMax H3 Turbo graph has a
    RandomNoise for the generation and another for the second pass. Setting only
    one leaves the other frozen, so every press gives a partly identical result.
    All of them get the same fresh value.
    """
    found: list[tuple[str, str]] = []
    for node_id, node in _iter_nodes(graph):
        for input_name, value in (node.get("inputs") or {}).items():
            if input_name.lower() in _SEED_INPUT_NAMES and \
                    isinstance(value, (int, float)) and _is_literal(value):
                found.append((node_id, input_name))
    found.sort(key=lambda h: graph_order.index(h[0]))
    return [Binding(node_id, input_name) for node_id, input_name in found]


def _find_named_int(graph: dict, names: tuple[str, ...], graph_order: list[str],
                    prefer: tuple[str, ...] = ()) -> tuple[Binding | None, bool]:
    hits: list[tuple[str, str, dict]] = []
    for node_id, node in _iter_nodes(graph):
        for input_name, value in (node.get("inputs") or {}).items():
            if input_name.lower() in names and isinstance(value, (int, float)) and _is_literal(value):
                hits.append((node_id, input_name, node))
    if not hits:
        return None, False

    def score(item):
        node_id, _, node = item
        low = _cls(node).lower()
        preferred = 0 if any(p in low for p in prefer) else 1
        return (preferred, graph_order.index(node_id))

    best = min(hits, key=score)
    return Binding(best[0], best[1]), len(hits) > 1


#: Nodes that rewrite the user's prompt with a language model before it reaches
#: the text encoder. LTX 2.5 workflows lean on these heavily - they turn a short
#: line into the long cinematic description the model was trained on - but they
#: load a multi-gigabyte LLM, so the user needs to be able to turn it off.
_ENHANCER_HINTS = ("promptenhancer", "promptenhance", "enhanceprompt",
                   "llmprompt", "promptgen", "generateltx2prompt")
#: TextGenerateLTX2Prompt is the one LTX 2.5 workflows use: it takes the short
#: prompt and a CLIP model and returns the long cinematic version.
_ENHANCER_EXACT = {"enhancer", "advpromptenhancer",
                   "textgenerate", "textgenerateltx2prompt"}
_ENHANCER_TEXT_INPUTS = ("prompt_text", "prompt", "text", "string", "positive_prompt")


def _is_enhancer(class_type: str) -> bool:
    flat = class_type.lower().replace("_", "").replace("-", "").replace(" ", "")
    if flat in _ENHANCER_EXACT:
        return True
    return any(hint in flat for hint in _ENHANCER_HINTS)


def _find_enhancer(graph: dict, graph_order: list[str]) -> Binding | None:
    """Find a prompt-enhancer node and the text input the user's words go into."""
    hits: list[tuple[str, str]] = []
    for node_id, node in _iter_nodes(graph):
        if not _is_enhancer(_cls(node)):
            continue
        inputs = node.get("inputs") or {}
        for field_name in _ENHANCER_TEXT_INPUTS:
            # Record the field whether it holds the text directly or is wired
            # from another node - switching the enhancer off needs to know
            # where its input came from so it can reconnect to the same source.
            if field_name in inputs:
                hits.append((node_id, field_name))
                break
        else:
            hits.append((node_id, ""))
    if not hits:
        return None
    hits.sort(key=lambda h: graph_order.index(h[0]))
    return Binding(hits[0][0], hits[0][1])


def _find_fps(graph: dict) -> int | None:
    """The workflow's frame rate, for converting seconds to frames on screen."""
    titled: list[int] = []
    plain: list[int] = []
    for _, node in _iter_nodes(graph):
        title = _title(node)
        for input_name, value in (node.get("inputs") or {}).items():
            if not (isinstance(value, (int, float)) and _is_literal(value)) or value <= 0:
                continue
            if input_name.lower() in _FPS_INPUT_NAMES:
                titled.append(int(value))
            elif _FPS_WORDS.search(title) and int(value) in _LIKELY_FPS:
                titled.append(int(value))
            elif int(value) in _LIKELY_FPS:
                plain.append(int(value))
    for pool in (titled, plain):
        if pool:
            return max(set(pool), key=pool.count)     # most common wins
    return None


def _find_duration(graph: dict, graph_order: list[str]) -> tuple[Binding | None, str, bool]:
    """Find the number that controls how long the video is.

    Returns (binding, unit, ambiguous) where unit is "seconds" or "frames".

    Real workflows rarely let you type a frame count. They compute it - LTX 2.5
    uses ``a * b + 1`` over a Duration and a Frame Rate primitive, MiniMax uses
    ``max(5, round(a * 24)) + ...`` over a single Duration - so the useful knob
    is the seconds primitive feeding the maths, not the frame count it produces.
    This walks upstream from the frame-count input to find it.
    """
    edges_up: list[tuple[str, object]] = []
    for node_id, node in _iter_nodes(graph):
        if any(bad in _cls(node).lower() for bad in _LENGTH_EXCLUDE):
            continue
        for input_name, value in (node.get("inputs") or {}).items():
            if input_name.lower() in _LENGTH_INPUT_NAMES:
                edges_up.append((node_id, value))

    if not edges_up:
        return None, "frames", False

    seconds: list[Binding] = []
    frames: list[Binding] = []

    for node_id, value in edges_up:
        if not isinstance(value, list):
            # A frame count typed straight into the node.
            field = next(k for k in (graph[node_id].get("inputs") or {})
                         if k.lower() in _LENGTH_INPUT_NAMES)
            frames.append(Binding(node_id, field))
            continue
        found = _walk_for_duration(graph, str(value[0]))
        if found:
            seconds.append(found)

    if seconds:
        seconds.sort(key=lambda b: graph_order.index(b.node))
        return seconds[0], "seconds", len(seconds) > 1
    if frames:
        frames.sort(key=lambda b: graph_order.index(b.node))
        return frames[0], "frames", len(frames) > 1
    return None, "frames", False


def _walk_for_duration(graph: dict, node_id: str, depth: int = 0,
                       seen: set[str] | None = None) -> Binding | None:
    """Follow a frame-count calculation back to the seconds primitive."""
    seen = seen if seen is not None else set()
    if depth > 4 or node_id in seen:
        return None
    seen.add(node_id)

    node = graph.get(node_id)
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs") or {}
    title = _title(node)

    # A numeric primitive titled "Duration" is the answer.
    for input_name, value in inputs.items():
        if isinstance(value, (int, float)) and _is_literal(value):
            if _DURATION_WORDS.search(title) and not _FPS_WORDS.search(title):
                return Binding(node_id, input_name)

    # Otherwise keep going upstream. "values.a" first: both expression styles
    # here put the duration in a and the frame rate in b.
    links = [(k, v) for k, v in inputs.items() if isinstance(v, list) and v]
    links.sort(key=lambda kv: (not kv[0].lower().endswith(".a"), kv[0]))
    for _, value in links:
        found = _walk_for_duration(graph, str(value[0]), depth + 1, seen)
        if found:
            return found

    # Nothing was labelled. Fall back to a plain number that does not look like
    # a frame rate, reached through a calculation.
    if depth > 0 and not _FPS_WORDS.search(title):
        for input_name, value in inputs.items():
            if isinstance(value, (int, float)) and _is_literal(value) \
                    and 0 < value <= 300 and int(value) not in _LIKELY_FPS:
                return Binding(node_id, input_name)
    return None


def _find_ratio(graph: dict) -> Binding | None:
    """Some workflows drive size from a ratio picker node (e.g. ResolutionSelector)."""
    live = _live_nodes(graph)
    for node_id, node in _iter_nodes(graph):
        if node_id not in live:
            continue
        for input_name, value in (node.get("inputs") or {}).items():
            if input_name.lower() in _RATIO_INPUT_NAMES and isinstance(value, str):
                return Binding(node_id, input_name)
    return None


def _find_megapixels(graph: dict, ratio: Binding | None) -> Binding | None:
    """A node that sizes its output by total pixels rather than width x height.

    ``ResolutionSelector`` and ``ImageScaleToTotalPixels`` both work this way.
    The one sitting on the ratio node wins, since that is the node actually
    deciding the output size.
    """
    hits: list[Binding] = []
    live = _live_nodes(graph)
    for node_id, node in _iter_nodes(graph):
        if node_id not in live:
            continue
        for input_name, value in (node.get("inputs") or {}).items():
            # Dotted names belong to a dynamic option - Flux 3 Klein's
            # ReferenceMultiLatent keeps its budget in "resolution.megapixels",
            # present only while "resize" is chosen.
            if input_name.lower().rsplit(".", 1)[-1] != "megapixels":
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) \
                    and _is_literal(value):
                binding = Binding(node_id, input_name)
                if ratio is not None and node_id == ratio.node:
                    return binding
                hits.append(binding)
    return hits[0] if hits else None


def _find_output(graph: dict, graph_order: list[str]) -> Binding | None:
    saves, previews = [], []
    for node_id, node in _iter_nodes(graph):
        low = _cls(node).lower()
        if any(s in low for s in _SAVE_CLASSES):
            saves.append(node_id)
        elif any(p in low for p in _PREVIEW_CLASSES):
            previews.append(node_id)
    pool = saves or previews
    if not pool:
        return None
    node_id = min(pool, key=graph_order.index)
    # filename_prefix is the only field worth writing; not every save node has it.
    inputs = graph[node_id].get("inputs") or {}
    field_name = "filename_prefix" if "filename_prefix" in inputs else ""
    return Binding(node_id, field_name)


def loader_consumers(graph: dict, node_id: str) -> list[tuple[str, str]]:
    """(consumer node id, input name) for everything a loader feeds.

    One loader can feed several inputs - a video loader supplies both the
    footage and its soundtrack - and skipping it has to drop all of them.
    """
    return [(consumer, name)
            for consumer, name in _consumer_edges(graph).get(str(node_id), [])]


def _find_optional_slots(graph: dict, bindings: dict[str, list[Binding]],
                         caps) -> list[str]:
    """Which file slots the user is allowed to leave empty.

    A slot is optional only when every input it feeds is optional on the
    consuming node. Being wrong in the permissive direction would delete
    something the workflow needs, so anything unknown counts as required.
    """
    if caps is None or not getattr(caps, "available", False):
        return []

    edges = _consumer_edges(graph)
    optional: list[str] = []
    for key in FILE_SLOTS:
        targets = bindings.get(key) or []
        if not targets:
            continue
        consumers = [c for b in targets for c in edges.get(b.node, [])]
        if not consumers:
            continue
        if all(caps.is_optional_input(_cls(graph.get(consumer) or {}), input_name)
               for consumer, input_name in consumers):
            optional.append(key)
    return optional


#: Inputs that name the main model, best first. CLIP and VAE are absent: they
#: have to match the model, so offering them would mostly offer ways to break
#: a workflow.
_MAIN_MODEL_INPUTS = ("unet_name", "gguf_name", "ckpt_name", "model_name")

#: Loaders that read a model file but are not the model that makes the
#: picture. LTX 2.3 reads its checkpoint again for the text encoder and the
#: audio VAE; LTX 2.5's first model-like loader is the latent upscaler.
_NOT_MAIN_MODEL = ("upscale", "textencoder", "text_encoder", "vae", "clip",
                   "controlnet", "lora")


def _find_main_model(graph: dict, order: list[str],
                     mode: str = "image") -> tuple[list[Binding], bool]:
    """Every input that names the model this workflow generates with.

    Returns (bindings, ambiguous). Usually one loader; when several loaders
    read the same file - LTX 2.3 opens its checkpoint for the model, the text
    encoder and the audio VAE - all of them are bound, so choosing another
    model changes them together instead of mixing two.

    The Prompt Helper's model is its language model, loaded as a CLIP.
    """
    candidates: list[tuple[int, int, Binding, str]] = []
    for position, node_id in enumerate(order):
        node = graph.get(node_id) or {}
        inputs = node.get("inputs") or {}
        low = _cls(node).lower()
        if any(word in low for word in _NOT_MAIN_MODEL):
            continue
        for rank, field in enumerate(_MAIN_MODEL_INPUTS):
            value = inputs.get(field)
            # A wired input is computed elsewhere; only a literal filename can
            # be swapped for another.
            if isinstance(value, str) and value.strip():
                candidates.append((rank, position, Binding(node_id, field), value))
                break

    if not candidates and mode == "prompt-enhancer":
        for node_id in order:
            value = ((graph.get(node_id) or {}).get("inputs") or {}).get("clip_name")
            if isinstance(value, str) and value.strip():
                candidates.append((0, 0, Binding(node_id, "clip_name"), value))
                break
    if not candidates:
        return [], False

    candidates.sort(key=lambda c: (c[0], c[1]))
    _, _, primary, filename = candidates[0]
    others = {c[3] for c in candidates if c[3] != filename}

    # Everything else that reads the very same file goes with it.
    bindings = [primary]
    for node_id in order:
        if node_id == primary.node:
            continue
        for field, value in ((graph.get(node_id) or {}).get("inputs") or {}).items():
            if field in _MAIN_MODEL_INPUTS and value == filename:
                bindings.append(Binding(node_id, field))
    return bindings, bool(others)


#: LoRA loaders, and the inputs naming the file and its strength.
_LORA_CLASSES = ("loraloadermodelonly", "loraloader")
_LORA_STRENGTHS = ("strength_model", "strength")


def _find_optional_lora(graph: dict, order: list[str]) -> tuple[Binding | None, Binding | None]:
    """A LoRA its author left switched off, which EasyAI offers as a choice.

    Most LoRAs in these workflows are part of the recipe - a turbo LoRA the
    sampler settings assume - and must stay as they are. A loader set to
    strength 0 does nothing at all, which is how an author parks a slot for
    "a LoRA of your own". That one, and only that one, becomes a picker.
    """
    live = _live_nodes(graph)
    for node_id in order:
        node = graph.get(node_id) or {}
        if node_id not in live or _cls(node).lower() not in _LORA_CLASSES:
            continue
        inputs = node.get("inputs") or {}
        if not isinstance(inputs.get("lora_name"), str):
            continue
        strength = next((k for k in _LORA_STRENGTHS if k in inputs), None)
        value = inputs.get(strength) if strength else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
            return Binding(node_id, "lora_name"), Binding(node_id, strength)
    return None, None


#: Nodes that draw layout boxes, and the prompt nodes that read them.
_BOX_CLASSES = ("createboundingboxes",)
_LAYOUT_CLASSES = ("flux3layoutprompt", "mossimagelayoutprompt")


def _find_boxes(graph: dict, order: list[str]) -> tuple[Binding | None, Binding | None]:
    """The box canvas and the layout prompt it feeds, when a workflow has both.

    The canvas is ComfyUI's interactive box editor. EasyAI fills it from its
    own editor; the layout node is what turns the boxes into prompt text, and
    it is removed when no boxes are drawn so the prompt runs as plain text.
    """
    edges = _consumer_edges(graph)
    for node_id in order:
        node = graph.get(node_id) or {}
        if _cls(node).lower() not in _BOX_CLASSES:
            continue
        for consumer, _ in edges.get(node_id, []):
            if _cls(graph.get(consumer) or {}).lower() in _LAYOUT_CLASSES:
                return Binding(node_id, "editor_state"), Binding(consumer, "prompt")
    return None, None


def autodetect(graph: dict, mode: str = "image", name: str = "", caps=None) -> Manifest:
    """Build a manifest by inspecting an API-format graph.

    Everything with more than one plausible candidate is listed in
    ``ambiguous`` so the UI can nudge the user to check it in the editor.
    """
    # Fill any single-picture reference group out to what its node can take,
    # on a copy - the caller's graph is theirs.
    from app.workflows import grow as _grow

    grown = _grow.plan(graph, caps)
    if grown:
        graph = copy.deepcopy(graph)
        _grow.apply(graph, grown)

    graph_order = [nid for nid, _ in _iter_nodes(graph)]
    bindings: dict[str, list[Binding]] = {k: [] for k in BINDING_KEYS}
    ambiguous: list[str] = []

    def note(key: str, result: tuple[Binding | None, bool]) -> None:
        binding, was_ambiguous = result
        bindings[key] = [binding] if binding else []
        if binding and was_ambiguous:
            ambiguous.append(key)

    positives, negatives = _find_text_fields(graph)
    note("prompt", _pick_text(positives, graph_order))
    note("negative", _pick_text(negatives, graph_order))

    # File inputs. A workflow may want several pictures - first and last frame
    # for video, or a row of reference images - so each gets its own slot with
    # its own label.
    edges = _consumer_edges(graph)
    labels: dict[str, str] = {}
    for table, slots, fallback in (
        (_IMAGE_LOADERS, IMAGE_SLOTS, "Picture"),
        (_AUDIO_LOADERS, AUDIO_SLOTS, "Sound"),
        (_VIDEO_LOADERS, VIDEO_SLOTS, "Video"),
    ):
        slot_bindings, slot_labels, overflow = _find_loaders(
            graph, table, slots, graph_order, edges, fallback)
        for key, binding in slot_bindings.items():
            bindings[key] = [binding]
        labels.update(slot_labels)
        if overflow:
            ambiguous.append(slots[0])

    models, model_ambiguous = _find_main_model(graph, graph_order, mode)
    bindings["model"] = models
    if models and model_ambiguous:
        ambiguous.append("model")

    widths, heights, size_ambiguous = _find_size(graph, graph_order)
    bindings["width"], bindings["height"] = widths, heights
    if widths and size_ambiguous:
        ambiguous.append("width")

    ratio = _find_ratio(graph)
    bindings["ratio"] = [ratio] if ratio else []

    megapixels = _find_megapixels(graph, ratio)
    bindings["megapixels"] = [megapixels] if megapixels else []

    enhancer = _find_enhancer(graph, graph_order)
    bindings["enhancer"] = [enhancer] if enhancer else []

    bindings["seed"] = _find_all_seeds(graph, graph_order)

    lora, lora_strength = _find_optional_lora(graph, graph_order)
    bindings["lora"] = [lora] if lora else []
    bindings["lora_strength"] = [lora_strength] if lora_strength else []

    boxes, layout = _find_boxes(graph, graph_order)
    bindings["boxes"] = [boxes] if boxes else []
    bindings["layout"] = [layout] if layout else []

    duration, unit, duration_ambiguous = _find_duration(graph, graph_order)
    bindings["length"] = [duration] if duration else []
    if duration and duration_ambiguous:
        ambiguous.append("length")
    output = _find_output(graph, graph_order)
    bindings["output"] = [output] if output else []

    # Show a control only where we actually found something to drive. Length is
    # included by default for video, where "how long is it" is a question every
    # user asks, unlike the other advanced knobs.
    expose = [k for k in ("prompt",) + FILE_SLOTS if bindings.get(k)]
    if bindings.get("length") and mode == "video":
        expose.append("length")

    # The pictures of a grown group are numbered the way the node reads them.
    by_node = {b.node: key for key in FILE_SLOTS for b in bindings.get(key, [])}
    for spec in grown:
        for position, loader in enumerate(_grow.member_loaders(graph, spec), 1):
            if loader in by_node:
                labels[by_node[loader]] = f"Reference {position}"

    return Manifest(
        name=name,
        mode=mode,
        engine=guess_engine(graph),
        bindings=bindings,
        labels=labels,
        grow=grown,
        optional_slots=_find_optional_slots(graph, bindings, caps),
        length_unit=unit,
        fps=_find_fps(graph),
        expose=expose,
        ambiguous=ambiguous,
        autodetected=True,
    )


def describe_nodes(graph: dict) -> list[tuple[str, str, str, list[str]]]:
    """(id, class_type, title, editable input names) for the manifest editor."""
    rows = []
    for node_id, node in _iter_nodes(graph):
        editable = [name for name, value in (node.get("inputs") or {}).items()
                    if _is_literal(value)]
        rows.append((node_id, _cls(node), _title(node), editable))
    return rows
