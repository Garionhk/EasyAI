"""Layout boxes: rectangles that say what goes where in a picture.

Flux 3 Klein and MOSS-Image read a layout from ComfyUI's Create Bounding Boxes
node - each box has a place, a kind and a description, and a layout-prompt
node turns them into text the model was trained on. ComfyUI draws those boxes
in its own canvas; EasyAI has its own editor and hands the result over here.

Boxes are kept as fractions of the canvas (0 to 1) rather than pixels, so
they survive a change of picture or of shape. They become pixels only when a
run is put together and the canvas size is known.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace

#: What a box can hold. "character" is MOSS-Image's: an object box it is told
#: to draw as a person or creature. Flux 3 treats it as a plain object.
KINDS = ("object", "text", "character")

#: The node's own name for each kind.
_NODE_TYPE = {"object": "obj", "text": "text", "character": "obj"}


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float
    kind: str = "object"
    #: What the box shows, in words.
    desc: str = ""
    #: For text boxes: the exact words to render.
    text: str = ""
    #: Colour names a MOSS-Image layout gave this item, carried through
    #: unchanged so editing the boxes does not lose them.
    palette: tuple[str, ...] = field(default=())

    def clamped(self) -> "Box":
        """The same box, kept inside the canvas and at least a sliver wide."""
        x = min(max(self.x, 0.0), 0.99)
        y = min(max(self.y, 0.0), 0.99)
        w = min(max(self.w, 0.01), 1.0 - x)
        h = min(max(self.h, 0.01), 1.0 - y)
        kind = self.kind if self.kind in KINDS else "object"
        return replace(self, x=x, y=y, w=w, h=h, kind=kind)

    def to_json(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "kind": self.kind, "desc": self.desc, "text": self.text,
                "palette": list(self.palette)}

    @staticmethod
    def from_json(raw) -> "Box | None":
        if not isinstance(raw, dict):
            return None
        try:
            return Box(float(raw["x"]), float(raw["y"]), float(raw["w"]),
                       float(raw["h"]), str(raw.get("kind") or "object"),
                       str(raw.get("desc") or ""), str(raw.get("text") or ""),
                       tuple(str(c) for c in raw.get("palette") or ())).clamped()
        except (KeyError, TypeError, ValueError):
            return None


def to_editor_state(boxes: list[Box], width: int, height: int) -> list[dict]:
    """The boxes as Create Bounding Boxes stores them, in canvas pixels."""
    state = []
    for box in boxes:
        box = box.clamped()
        left, top = round(box.x * width), round(box.y * height)
        state.append({
            "x": left,
            "y": top,
            "width": max(1, min(width - left, round(box.w * width))),
            "height": max(1, min(height - top, round(box.h * height))),
            "metadata": {
                "type": _NODE_TYPE[box.kind],
                "text": box.text if box.kind == "text" else "",
                "desc": box.desc,
                "palette": [],
            },
        })
    return state


def numbers(boxes: list[Box]) -> list[int]:
    """The number each box is cited by in a prompt - ``<obj_N>``.

    Both layout nodes count every box, text boxes included, in drawing order
    from 1 (ComfyUI's comfy_extras/nodes_flux.py and nodes_moss_image.py).
    """
    return list(range(1, len(boxes) + 1))


def character_numbers(boxes: list[Box]) -> str:
    """MOSS-Image's ``character_boxes``: "1, 3" for the boxes holding characters."""
    return ", ".join(str(n) for box, n in zip(boxes, numbers(boxes))
                     if box.kind == "character")


def summary(boxes: list[Box]) -> tuple[int, int]:
    """(object boxes, text boxes), for the line under the prompt."""
    texts = sum(1 for b in boxes if b.kind == "text")
    return len(boxes) - texts, texts


# --- MOSS-Image layouts written into the prompt -------------------------------
# MOSS-Image reads its layout as JSON in the prompt itself - the same JSON
# ComfyUI's MOSS-Image Layout Prompt node writes (comfy_extras/
# nodes_moss_image.py):
#
#     {"summary": {"caption": "..."},
#      "items": [{"label": "object", "caption": "...", "bbox": [y0, x0, y1, x1]}],
#      "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}
#
# Each bbox is in pixels of the generated picture, measured from its centre.
# A prompt written that way - by hand, by the prompt helper, or pasted from a
# template - can be opened in the box editor, and boxes drawn there are
# written back the same way.

_LABEL_TO_KIND = {"text": "text", "character": "character", "object": "object"}


def parse_layout(text: str) -> dict | None:
    """The MOSS layout a prompt holds, or None if it is plain words."""
    text = (text or "").strip()
    if not text.startswith("{"):
        return None
    try:
        layout = json.loads(text)
    except ValueError:
        return None
    if not isinstance(layout, dict):
        return None
    if not isinstance(layout.get("items"), list) and not isinstance(layout.get("summary"), dict):
        return None
    return layout


def layout_caption(layout: dict) -> str:
    summary = layout.get("summary")
    return str(summary.get("caption") or "") if isinstance(summary, dict) else ""


def _edges(bbox, layout: dict) -> tuple[float, float, float, float] | None:
    """(left, top, right, bottom) in pixels from the centre, whatever the order."""
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return None
    try:
        a, b, c, d = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if str(layout.get("bbox_format") or "yxyx").lower() == "xyxy":
        return a, b, c, d
    return b, a, d, c


def layout_to_boxes(layout: dict, width: int, height: int) -> list[Box]:
    """The layout's items as boxes on a ``width`` x ``height`` picture.

    Anything reaching past the picture's edge - a layout written for a
    different shape - is kept inside it.
    """
    found = []
    for item in layout.get("items") or []:
        if not isinstance(item, dict):
            continue
        edges = _edges(item.get("bbox"), layout)
        if edges is None:
            continue
        left, top, right, bottom = edges
        x0 = (left + width // 2) / width
        y0 = (top + height // 2) / height
        x1 = (right + width // 2) / width
        y1 = (bottom + height // 2) / height
        x0, x1 = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
        y0, y1 = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
        palette = item.get("palette") if isinstance(item.get("palette"), list) else []
        found.append(Box(
            x0, y0, x1 - x0, y1 - y0,
            _LABEL_TO_KIND.get(str(item.get("label") or "").lower(), "object"),
            str(item.get("caption") or ""), str(item.get("text") or ""),
            tuple(str(c) for c in palette)).clamped())
    return found


def boxes_to_layout(boxes: list[Box], width: int, height: int,
                    base: dict | None = None, caption: str = "") -> dict:
    """A MOSS layout holding these boxes, on a ``width`` x ``height`` picture.

    ``base`` is the layout the prompt held before: its summary - caption,
    meta, palette, canvas - is kept as it was and only the items are
    replaced. Without one, ``caption`` becomes the summary, as the layout
    node does with a plain prompt.
    """
    layout = json.loads(json.dumps(base)) if isinstance(base, dict) else {}
    summary = layout.get("summary") if isinstance(layout.get("summary"), dict) else {}
    if not base:
        summary = {"caption": caption.strip()}
    items = []
    for box in boxes:
        box = box.clamped()
        item = {"label": box.kind, "caption": box.desc}
        if box.kind == "text":
            item["text"] = box.text
        if box.palette:
            item["palette"] = list(box.palette)
        # The node's own rounding: edges enclose the box, origin at the centre.
        item["bbox"] = [
            math.floor(round(box.y * height, 9)) - height // 2,
            math.floor(round(box.x * width, 9)) - width // 2,
            math.ceil(round((box.y + box.h) * height, 9)) - height // 2,
            math.ceil(round((box.x + box.w) * width, 9)) - width // 2,
        ]
        items.append(item)
    out = {key: value for key, value in layout.items()
           if key not in ("summary", "items", "bbox_coord_space", "bbox_format")}
    return {"summary": summary, **out, "items": items,
            "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}


def layout_text(layout: dict) -> str:
    """How the layout goes into the prompt box: one line, like the node's."""
    return json.dumps(layout, ensure_ascii=False, separators=(",", ":"))


def rescale_layout(text: str, old: tuple[int, int], new: tuple[int, int]) -> str:
    """The same layout on a picture of another size, boxes in proportion.

    The pixels are absolute, so changing the shape or the detail after
    drawing would otherwise push every box off where it was drawn.
    """
    layout = parse_layout(text)
    if layout is None or tuple(old) == tuple(new):
        return text
    boxes = layout_to_boxes(layout, *old)
    return layout_text(boxes_to_layout(boxes, *new, base=layout))


def layout_extent(layout: dict) -> tuple[int, int]:
    """The smallest picture (width, height) the layout's boxes fit inside."""
    half_w = half_h = 0.0
    for item in layout.get("items") or []:
        edges = _edges(item.get("bbox"), layout) if isinstance(item, dict) else None
        if edges:
            left, top, right, bottom = edges
            half_w = max(half_w, abs(left), abs(right))
            half_h = max(half_h, abs(top), abs(bottom))
    return int(math.ceil(half_w * 2)), int(math.ceil(half_h * 2))
