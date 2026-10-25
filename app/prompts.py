"""Reading the prompt back out of a finished picture or video.

ComfyUI writes the graph it ran into every file it saves - a `prompt` text
chunk in a PNG, a block of JSON in the header of an MP4 - so the prompt is
never really lost, only unreadable. This module gets it back.

Nothing here touches the engine. A prompt can be read with ComfyUI closed,
which matters because looking through old results is exactly what someone does
before starting it up.

The one thing this must not do is guess when it does not have to. A graph can
hold several text boxes - `Krea 2 t2i` has three, one of them the prompt
enhancer's system instruction - and detection run from scratch picks the wrong
one. So a file is first matched to the workflow that made it, and that
workflow's saved manifest, which already knows which box is the prompt, is
believed over any fresh detection.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.jobs import safe_name

#: What is worth opening. PNG is where ComfyUI puts it; the others are tried
#: rather than refused, because a file that carries nothing simply reads as
#: "no prompt" and that is a better answer than "wrong sort of file".
IMAGE_EXT = {".png", ".webp", ".jpg", ".jpeg"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
READABLE_EXT = IMAGE_EXT | VIDEO_EXT

#: PNG text chunks ComfyUI writes. `prompt` is the API graph and the one that
#: matters; `workflow` is the editor's own format, kept as a fallback.
_PNG_KEYS = ("prompt", "workflow")

#: How much of a video to search. Every file tested carries the JSON about 4 KB
#: in, but a different encoder can put the metadata atom at the end, so both
#: ends are read. A megabyte each way costs nothing and covers both layouts.
_SCAN = 1024 * 1024

#: `2026-08-15_000247_Krea_2_t2i` or `..._Krea_2_t2i_02` for a batch.
_STAMPED = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_(.+?)(?:_\d{2})?$")

#: Below this, two graphs have nothing to do with each other. Deliberately
#: low: ComfyUI expands subgraphs when it submits, so a result's graph can
#: differ a good deal from the file that produced it and still be the same
#: workflow. The real protection is _usable() below, not this number.
_MIN_OVERLAP = 0.3

_decoder = json.JSONDecoder()


@dataclass
class PromptRead:
    """What was recovered from one file."""

    prompt: str
    #: The workflow it was matched to, empty when nothing matched.
    workflow_name: str = ""
    #: False when the prompt came from detection rather than from a workflow's
    #: own manifest - shown as a best guess rather than stated as fact.
    certain: bool = True
    #: The picture's size, (width, height), when the file is a picture. A
    #: MOSS-Image layout's boxes are measured in its pixels.
    size: tuple[int, int] | None = None
    #: Every prompt of a batch - one per line, fed through a prompt batcher -
    #: when the file came from one. ``prompt`` is then ``batch[index]``.
    batch: list[str] = field(default_factory=list)
    index: int = 0
    #: Whether ``index`` was settled by the file's place in its run, rather
    #: than left at the first line.
    index_certain: bool = False


# --- getting the graph out of the file ------------------------------------
def _graph_from_image(path: Path) -> dict | None:
    try:
        from PIL import Image

        with Image.open(path) as picture:
            info = dict(picture.info)
    except Exception:
        return None
    for key in _PNG_KEYS:
        raw = info.get(key)
        if not isinstance(raw, str):
            continue
        try:
            graph = json.loads(raw)
        except ValueError:
            continue
        if isinstance(graph, dict) and graph:
            return graph
    return None


def _looks_like_graph(found) -> bool:
    """A graph is a dict of nodes, each carrying a class_type."""
    if not isinstance(found, dict) or len(found) < 2:
        return False
    nodes = [n for n in found.values()
             if isinstance(n, dict) and "class_type" in n]
    return len(nodes) >= 2


def _graph_from_bytes(blob: bytes) -> dict | None:
    """Find the first embedded JSON object that looks like a graph.

    Scanned with raw_decode rather than by counting braces: a prompt
    containing a closing brace would break brace counting, and prompts do
    contain them.
    """
    text = blob.decode("utf-8", "replace")
    at = 0
    while True:
        at = text.find('{"', at)
        if at < 0:
            return None
        try:
            found, _ = _decoder.raw_decode(text, at)
        except ValueError:
            at += 1
            continue
        if _looks_like_graph(found):
            return found
        at += 1


def _graph_from_video(path: Path) -> dict | None:
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            head = handle.read(_SCAN)
            tail = b""
            if size > _SCAN * 2:
                handle.seek(-_SCAN, 2)
                tail = handle.read(_SCAN)
    except OSError:
        return None
    return _graph_from_bytes(head) or (_graph_from_bytes(tail) if tail else None)


def graph_in(path: str | Path) -> dict | None:
    """The graph a file was made with, or None if it does not carry one."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXT:
        return _graph_from_video(path)
    if suffix in IMAGE_EXT:
        return _graph_from_image(path)
    return None


# --- matching the file to the workflow that made it -----------------------
def _signature(graph: dict) -> frozenset:
    return frozenset((node_id, (node or {}).get("class_type"))
                     for node_id, node in graph.items()
                     if isinstance(node, dict))


def _overlap(one: frozenset, other: frozenset) -> float:
    both = one | other
    return len(one & other) / len(both) if both else 0.0


#: Inputs that carry text through a node - a prompt batcher's ``prompts``, a
#: primitive's ``value``.
_TEXT_INPUTS = ("value", "text", "prompt", "prompts", "string", "text_a", "positive")


def _text_from(graph: dict, value, depth: int = 0) -> str | None:
    """The words an input holds, following wires back to where they were typed.

    A prompt box is often fed rather than typed into: a batcher in front of
    MOSS-Image's prompt, a primitive shared by two encoders. The words then sit
    one or two nodes upstream, and stopping at the wire made the reader give up
    and guess - which picked the prompt helper's instructions instead.
    """
    if isinstance(value, str):
        return value if value.strip() else None
    if depth > 6 or not (isinstance(value, list) and value):
        return None
    node = graph.get(str(value[0]))
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs") or {}
    found = [inputs[name] for name in _TEXT_INPUTS if name in inputs]
    # Typed text on the node itself first, then anything it is fed.
    for candidate in found:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    for candidate in found:
        text = _text_from(graph, candidate, depth + 1)
        if text:
            return text
    return None


def _usable(graph: dict, workflow, binding) -> str | None:
    """The prompt this binding points at, if it can be trusted here.

    The guard that keeps a loose signature match honest: the node must still
    exist in this file's graph, be the same kind of node as in the workflow,
    and hold actual text. Without it a stranger's picture could latch onto an
    unrelated workflow and report a filename as a prompt.
    """
    if binding is None:
        return None
    node = graph.get(binding.node)
    if not isinstance(node, dict):
        return None
    mine = (getattr(workflow, "graph", None) or {}).get(binding.node)
    if isinstance(mine, dict) and mine.get("class_type") != node.get("class_type"):
        return None
    return _text_from(graph, (node.get("inputs") or {}).get(binding.input))


def _prompt_binding(workflow):
    manifest = getattr(workflow, "manifest", None)
    return manifest.get("prompt") if manifest is not None else None


def _by_filename(path: Path, workflows: list):
    """EasyAI names its results after the workflow, so this match is exact."""
    match = _STAMPED.match(path.stem)
    if not match:
        return None, None
    wanted = match.group(1).strip("_")
    for workflow in workflows:
        if safe_name(workflow.name).strip("_") == wanted:
            return workflow, _prompt_binding(workflow)
    return None, None


def _by_signature(graph: dict, workflows: list):
    """For a result whose workflow has since been renamed, or whose subgraphs
    ComfyUI expanded on the way in."""
    mine = _signature(graph)
    best, score = None, 0.0
    for workflow in workflows:
        if not getattr(workflow, "graph", None):
            continue
        rating = _overlap(mine, _signature(workflow.graph))
        if rating > score:
            best, score = workflow, rating
    if best is None or score < _MIN_OVERLAP:
        return None, None
    return best, _prompt_binding(best)


def _by_detection(graph: dict, mode: str, name: str) -> str | None:
    """Last resort, for a workflow this copy of EasyAI has never seen."""
    from app.workflows.manifest import autodetect

    try:
        manifest = autodetect(graph, mode=mode, name=name)
    except Exception:
        return None
    binding = manifest.get("prompt")
    if binding is None:
        return None
    node = graph.get(binding.node)
    if not isinstance(node, dict):
        return None
    value = (node.get("inputs") or {}).get(binding.input)
    return value if isinstance(value, str) and value.strip() else None


# --- MOSS-Image layouts drawn on ComfyUI's box canvas -----------------------
def _picture_size(path: Path) -> tuple[int, int] | None:
    if path.suffix.lower() not in IMAGE_EXT:
        return None
    try:
        from PIL import Image

        with Image.open(path) as picture:
            return picture.size
    except Exception:
        return None


def _int_from(graph: dict, value, output: int = 0) -> int | None:
    """A width or height: typed in, or worked out by a ResolutionSelector."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not (isinstance(value, list) and len(value) == 2):
        return None
    node = graph.get(str(value[0])) or {}
    inputs = node.get("inputs") or {}
    if str(node.get("class_type", "")).lower() == "resolutionselector":
        from app.ratios import solve_for
        ratio = str(inputs.get("aspect_ratio", "1:1")).split(" ", 1)[0]
        size = solve_for(ratio, float(inputs.get("megapixels") or 1.0),
                         int(inputs.get("multiple") or 8))
        return size[int(value[1]) if int(value[1]) in (0, 1) else 0]
    return None


def _moss_layout(graph: dict, prompt: str, size) -> str:
    """The prompt as MOSS-Image saw it, when its boxes were drawn in ComfyUI.

    ComfyUI's MOSS-Image Layout Prompt node joins the words and the boxes from
    Create Bounding Boxes into one JSON layout only while the picture is being
    made, so the file holds them apart. Joining them again here gives back
    the layout - which EasyAI's box editor can open - instead of the words
    alone.
    """
    from app import boxes as layout_boxes

    for node in graph.values():
        if not (isinstance(node, dict)
                and node.get("class_type") == "MOSSImageLayoutPrompt"):
            continue
        inputs = node.get("inputs") or {}
        if _text_from(graph, inputs.get("prompt")) != prompt:
            continue
        element = inputs.get("element")
        canvas = graph.get(str(element[0])) if isinstance(element, list) and element else None
        if not isinstance(canvas, dict) or canvas.get("class_type") != "CreateBoundingBoxes":
            continue
        cinputs = canvas.get("inputs") or {}
        state = cinputs.get("editor_state")
        state = state.get("__value__") if isinstance(state, dict) else state
        cw = _int_from(graph, cinputs.get("width"), 0)
        ch = _int_from(graph, cinputs.get("height"), 1)
        if not (isinstance(state, list) and state and cw and ch):
            continue
        characters = {int(n) for n in re.findall(r"\d+", str(inputs.get("character_boxes") or ""))}
        found = []
        for number, box in enumerate(state, 1):
            meta = box.get("metadata") or {} if isinstance(box, dict) else {}
            try:
                x, y = float(box["x"]) / cw, float(box["y"]) / ch
                w, h = float(box["width"]) / cw, float(box["height"]) / ch
            except (KeyError, TypeError, ValueError):
                continue
            kind = ("text" if meta.get("type") == "text"
                    else "character" if number in characters else "object")
            found.append(layout_boxes.Box(x, y, w, h, kind, str(meta.get("desc") or ""),
                                          str(meta.get("text") or "")).clamped())
        if not found:
            continue
        width = _int_from(graph, inputs.get("width"), 0) or (size or (cw, ch))[0]
        height = _int_from(graph, inputs.get("height"), 1) or (size or (cw, ch))[1]
        return layout_boxes.layout_text(
            layout_boxes.boxes_to_layout(found, width, height, caption=prompt))
    return prompt


# --- batches: one prompt per line ------------------------------------------
#: Nodes that turn a block of lines into one run per line.
_BATCHERS = ("promptbatcher", "promptbatch", "promptlist", "textlist")


def _batch_lines(graph: dict, text: str) -> list[str]:
    """The separate prompts in a batched prompt, or [] when it is one prompt.

    A prompt batcher runs every line as its own picture, so the file's graph
    holds them all - a hundred MOSS-Image layouts, in one case - while the
    picture was made from just one.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    batched = any(any(b in str((n or {}).get("class_type", "")).lower().replace("_", "")
                      for b in _BATCHERS)
                  for n in graph.values() if isinstance(n, dict))
    if batched:
        return lines
    from app.boxes import parse_layout
    # Without a batcher, lines that are each a whole MOSS layout are still
    # separate prompts: no single prompt is several JSON objects in a row.
    if all(parse_layout(line) is not None for line in lines):
        return lines
    return []


_COUNTER = re.compile(r"^(.*?)(\d+)_?$")


def _place_in_run(path: Path, size: int) -> tuple[int, bool]:
    """Which line of a batch made this file: (index, certain).

    The files of one run sit next to each other - ComfyUI numbers them
    00263, 00264, ... - and carry exactly the same graph. The file's place
    among them is the line it was made from. Certain when the run has exactly
    as many files as the batch has lines; otherwise a run was cut short or
    files were deleted, and the place is only a guess.
    """
    match = _COUNTER.match(path.stem)
    if not match:
        return 0, False
    stem = match.group(1)
    try:
        siblings = sorted(q for q in path.parent.iterdir()
                          if q.suffix.lower() == path.suffix.lower()
                          and (_COUNTER.match(q.stem) or [None, None])[1] == stem)
    except OSError:
        return 0, False
    if path not in siblings:
        return 0, False
    here = siblings.index(path)

    def raw(q: Path) -> str | None:
        try:
            from PIL import Image

            with Image.open(q) as picture:
                return picture.info.get("prompt")
        except Exception:
            return None

    mine = raw(path)
    if mine is None:
        return 0, False
    first = here
    while first > 0 and here - first < size and raw(siblings[first - 1]) == mine:
        first -= 1
    last = here
    while last + 1 < len(siblings) and last - first + 1 < size \
            and raw(siblings[last + 1]) == mine:
        last += 1
    run = last - first + 1
    return (here - first) % size, run == size


# --- the whole job --------------------------------------------------------
def read_prompt(path: str | Path,
                workflows: list | None = None) -> PromptRead | None:
    """The prompt a file was made with, or None if it does not carry one.

    Tried in order of how much can be trusted: the workflow named in the
    filename, then the workflow whose graph this most resembles, then
    detection. The first two answer from a saved manifest and are stated as
    fact; the third is marked as a guess.
    """
    path = Path(path)
    graph = graph_in(path)
    if not graph:
        return None
    workflows = workflows or []

    size = _picture_size(path)

    def found(text: str, name: str, certain: bool) -> PromptRead:
        lines = _batch_lines(graph, text)
        if lines:
            index, sure = _place_in_run(path, len(lines))
            return PromptRead(_moss_layout(graph, lines[index], size).strip(), name,
                              certain=certain, size=size, batch=lines,
                              index=index, index_certain=sure)
        return PromptRead(_moss_layout(graph, text, size).strip(), name,
                          certain=certain, size=size)

    workflow, binding = _by_filename(path, workflows)
    if workflow is not None:
        text = _usable(graph, workflow, binding)
        if text:
            return found(text, workflow.name, True)

    workflow, binding = _by_signature(graph, workflows)
    if workflow is not None:
        text = _usable(graph, workflow, binding)
        if text:
            return found(text, workflow.name, True)

    mode = "video" if path.suffix.lower() in VIDEO_EXT else "image"
    guessed = _by_detection(graph, mode, path.stem)
    if guessed:
        return found(guessed, "", False)
    return None


def recent_results(folders, limit: int = 80) -> list:
    """Everything readable in the results folders, newest first."""
    found = []
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if item.is_file() and item.suffix.lower() in READABLE_EXT:
                try:
                    found.append((item.stat().st_mtime, item))
                except OSError:
                    continue
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in found[:limit]]
