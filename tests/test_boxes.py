"""Layout boxes: from EasyAI's editor to ComfyUI's Create Bounding Boxes.

Run with:  python -m pytest tests/test_boxes.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.boxes import Box, character_numbers, numbers, summary, to_editor_state


def test_boxes_become_canvas_pixels():
    state = to_editor_state([Box(0.25, 0.5, 0.5, 0.25, "object", "a cat")], 1000, 800)
    assert state == [{"x": 250, "y": 400, "width": 500, "height": 200,
                      "metadata": {"type": "obj", "text": "", "desc": "a cat",
                                   "palette": []}}]


def test_text_boxes_carry_their_words_and_others_do_not():
    state = to_editor_state([Box(0, 0, 1, 0.1, "text", "a title", "SALE"),
                             Box(0, 0.5, 0.5, 0.5, "object", "a dog", "ignored")], 100, 100)
    assert state[0]["metadata"]["type"] == "text" and state[0]["metadata"]["text"] == "SALE"
    assert state[1]["metadata"]["text"] == ""


def test_a_box_never_leaves_the_canvas():
    box = Box(0.9, 0.9, 0.5, 0.5).clamped()
    assert box.x + box.w <= 1.0 and box.y + box.h <= 1.0
    state = to_editor_state([Box(0.9, 0.9, 0.5, 0.5)], 640, 480)[0]
    assert state["x"] + state["width"] <= 640 and state["y"] + state["height"] <= 480


def test_every_box_is_numbered_text_included():
    """Both layout nodes count every box from 1 - ComfyUI's own source."""
    boxes = [Box(0, 0, 1, 1, "text"), Box(0, 0, 1, 1), Box(0, 0, 1, 1, "character")]
    assert numbers(boxes) == [1, 2, 3]
    assert character_numbers(boxes) == "3"


def test_characters_are_objects_to_the_canvas():
    state = to_editor_state([Box(0, 0, 1, 1, "character", "a knight")], 10, 10)
    assert state[0]["metadata"]["type"] == "obj"


def test_round_trip_through_json():
    box = Box(0.1, 0.2, 0.3, 0.4, "text", "title", "HELLO")
    assert Box.from_json(box.to_json()) == box
    assert Box.from_json({"x": "no"}) is None


def test_the_summary_counts_texts_apart():
    assert summary([Box(0, 0, 1, 1, "text"), Box(0, 0, 1, 1), Box(0, 0, 1, 1)]) == (2, 1)


def test_the_editor_hands_back_what_was_given():
    from PySide6.QtWidgets import QApplication

    from app.ui.box_editor import BoxEditor

    app = QApplication.instance() or QApplication([])
    boxes = [Box(0.1, 0.1, 0.3, 0.3, "object", "a cat"),
             Box(0.5, 0.0, 0.5, 0.2, "text", "a title", "SALE")]
    editor = BoxEditor(boxes, aspect=(3, 2), allow_character=True)
    back = editor.boxes()
    assert [b.kind for b in back] == ["object", "text"]
    assert [b.desc for b in back] == ["a cat", "a title"]
    for got, want in zip(back, boxes):
        assert abs(got.x - want.x) < 1e-6 and abs(got.w - want.w) < 1e-6
    editor._items[0].setSelected(True)
    editor._move(1)
    assert [b.kind for b in editor.boxes()] == ["text", "object"]
    editor._delete()
    assert [b.kind for b in editor.boxes()] == ["text"]
    editor.deleteLater()
    del app


# --- MOSS-Image layouts in the prompt -------------------------------------------
import json

from app.boxes import (boxes_to_layout, layout_caption, layout_text, layout_to_boxes,
                       parse_layout, rescale_layout)

#: Trimmed from a real MOSS-Image prompt: a frame-filling trunk and one owl.
OWL = json.dumps({
    "summary": {"meta": ["wildlife photograph"], "palette": ["snow white"],
                "caption": "A snowy owl in a tree hollow."},
    "items": [
        {"label": "object", "caption": "a huge old tree trunk", "bbox": [-624, -772, 624, 772]},
        {"label": "character", "caption": "an adult snowy owl", "bbox": [-394, -402, 306, -32],
         "palette": ["white"]},
        {"label": "text", "caption": "a sign", "text": "OWLS", "bbox": [500, 300, 600, 700]},
    ],
    "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"})


def test_plain_words_are_not_a_layout():
    assert parse_layout("a cat on a mat") is None
    assert parse_layout("{not json") is None
    assert parse_layout(OWL) is not None


def test_a_prompts_layout_becomes_boxes():
    boxes = layout_to_boxes(parse_layout(OWL), 1544, 1248)
    assert [b.kind for b in boxes] == ["object", "character", "text"]
    trunk = boxes[0]
    assert (trunk.x, trunk.y) == (0.0, 0.0) and trunk.w > 0.99 and trunk.h > 0.99
    assert boxes[1].palette == ("white",) and boxes[2].text == "OWLS"


def test_boxes_go_back_into_the_prompt_exactly():
    layout = parse_layout(OWL)
    back = boxes_to_layout(layout_to_boxes(layout, 1544, 1248), 1544, 1248, base=layout)
    assert [i["bbox"] for i in back["items"]] == [i["bbox"] for i in layout["items"]]
    assert back["summary"] == layout["summary"]          # meta and palette kept
    assert back["items"][1]["palette"] == ["white"]
    assert back["bbox_format"] == "yxyx"


def test_boxes_with_no_prompt_are_the_whole_prompt():
    text = layout_text(boxes_to_layout([Box(0, 0, 0.5, 0.5, "object", "a red cat")], 1000, 800))
    layout = parse_layout(text)
    assert layout_caption(layout) == ""
    assert layout["items"] == [{"label": "object", "caption": "a red cat",
                                "bbox": [-400, -500, 0, 0]}]


def test_plain_words_become_the_caption():
    layout = boxes_to_layout([Box(0, 0, 1, 1)], 1000, 800, caption="a quiet street ")
    assert layout["summary"] == {"caption": "a quiet street"}


def test_a_layout_follows_a_change_of_size():
    text = layout_text(boxes_to_layout([Box(0.25, 0.25, 0.5, 0.5)], 1000, 1000))
    moved = parse_layout(rescale_layout(text, (1000, 1000), (2000, 1000)))
    assert moved["items"][0]["bbox"] == [-250, -500, 250, 500]
    assert rescale_layout("plain words", (1, 1), (2, 2)) == "plain words"


def test_an_xyxy_layout_is_read_the_right_way_round():
    layout = {"items": [{"label": "object", "caption": "x", "bbox": [-500, -400, 0, 0]}],
              "bbox_format": "xyxy"}
    box = layout_to_boxes(layout, 1000, 800)[0]
    assert (box.x, box.y, box.w, box.h) == (0.0, 0.0, 0.5, 0.5)


def test_the_size_a_layout_needs_is_known():
    from app.boxes import layout_extent
    assert layout_extent(parse_layout(OWL)) == (1544, 1248)
