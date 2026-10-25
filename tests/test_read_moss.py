"""Reading MOSS-Image prompts back out of pictures.

Run with:  python -m pytest tests/test_read_moss.py -q

Three things the reader got wrong on real MOSS-Image pictures:

* The prompt was fed into the workflow's prompt box through a prompt batcher,
  so the box held a wire, not words. The reader gave up and guessed - and
  guessed the prompt helper's instructions.
* That batcher held a hundred prompts, one per line, while each picture was
  made from one of them.
* Pictures made with boxes drawn in ComfyUI keep the words and the boxes
  apart; only the words came back.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.boxes import layout_text, boxes_to_layout, parse_layout, Box
from app.prompts import read_prompt
from app.workflows.loader import load_workflow

ROOT = Path(__file__).resolve().parent.parent
MOSS = ROOT / "workflows" / "image" / "Moss Image - Text to Image - GarionHK.json"


def layout(caption: str) -> str:
    return layout_text(boxes_to_layout([Box(0.1, 0.1, 0.5, 0.5, "object", "a cat")],
                                       1184, 1776, caption=caption))


LINES = [layout(f"picture number {i}") for i in range(1, 4)]


def batched_graph() -> dict:
    """The user's own MOSS graph: lines -> batcher -> the prompt box."""
    graph = json.loads(MOSS.read_text(encoding="utf-8"))
    graph["27"] = {"class_type": "PrimitiveStringMultiline",
                   "inputs": {"value": "\n".join(LINES)},
                   "_meta": {"title": "Text (Multiline)"}}
    graph["26"] = {"class_type": "SimplePromptBatcher",
                   "inputs": {"prepend": "", "prompts": ["27", 0], "append": ""}}
    graph["18:17"]["inputs"]["value"] = ["26", 0]
    return graph


def save(path: Path, graph: dict, size=(1184, 1776)) -> Path:
    info = PngInfo()
    info.add_text("prompt", json.dumps(graph))
    Image.new("RGB", size).save(path, pnginfo=info)
    return path


def moss_workflow():
    return load_workflow(MOSS, "image", False, None)


def test_a_prompt_fed_through_a_batcher_is_found(tmp_path):
    picture = save(tmp_path / "moss_image_t2i_00001_.png", batched_graph())
    found = read_prompt(picture, [moss_workflow()])
    assert found.workflow_name == "Moss Image - Text to Image"
    assert "prompt engineer" not in found.prompt
    assert parse_layout(found.prompt) is not None


def test_without_the_workflow_the_system_prompt_is_not_the_guess(tmp_path):
    picture = save(tmp_path / "moss_image_t2i_00001_.png", batched_graph())
    found = read_prompt(picture, [])
    assert not found.certain
    assert "prompt engineer" not in found.prompt


def test_each_picture_of_a_batch_gets_its_own_line(tmp_path):
    graph = batched_graph()
    pictures = [save(tmp_path / f"moss_image_t2i_{n:05d}_.png", graph) for n in (7, 8, 9)]
    for line, picture in enumerate(pictures):
        found = read_prompt(picture, [moss_workflow()])
        assert len(found.batch) == 3
        assert (found.index, found.index_certain) == (line, True)
        assert parse_layout(found.prompt)["summary"]["caption"] == f"picture number {line + 1}"
        assert found.size == (1184, 1776)


def test_a_cut_short_run_is_only_a_guess(tmp_path):
    graph = batched_graph()
    picture = save(tmp_path / "moss_image_t2i_00007_.png", graph)
    save(tmp_path / "moss_image_t2i_00008_.png", graph)
    found = read_prompt(picture, [moss_workflow()])
    assert found.index == 0 and found.index_certain is False


def test_boxes_drawn_in_comfyui_come_back_as_a_layout(tmp_path):
    """The words in the prompt box, the boxes on ComfyUI's canvas."""
    graph = json.loads(MOSS.read_text(encoding="utf-8"))
    graph["18:17"]["inputs"]["value"] = "a knight and his dog"
    graph["20"]["inputs"]["editor_state"] = {"__value__": [
        {"x": 0, "y": 0, "width": 592, "height": 888,
         "metadata": {"type": "obj", "text": "", "desc": "a knight", "palette": []}},
        {"x": 0, "y": 1600, "width": 1184, "height": 176,
         "metadata": {"type": "text", "text": "SALE", "desc": "a banner", "palette": []}},
    ]}
    graph["21"]["inputs"]["character_boxes"] = "1"
    graph["19"]["inputs"].update({"aspect_ratio": "2:3 (Portrait Photo)", "megapixels": 2.0,
                                  "multiple": 16})
    picture = save(tmp_path / "moss_image_t2i_00001_.png", graph)

    found = read_prompt(picture, [moss_workflow()])
    result = parse_layout(found.prompt)
    assert result["summary"] == {"caption": "a knight and his dog"}
    assert [i["label"] for i in result["items"]] == ["character", "text"]
    assert result["items"][0]["bbox"] == [-888, -592, 0, 0]
    assert result["items"][1]["text"] == "SALE"


def test_a_plain_prompt_is_unchanged(tmp_path):
    graph = json.loads(MOSS.read_text(encoding="utf-8"))
    graph["18:17"]["inputs"]["value"] = "a lighthouse at dusk"
    picture = save(tmp_path / "moss_image_t2i_00001_.png", graph)
    found = read_prompt(picture, [moss_workflow()])
    assert found.prompt == "a lighthouse at dusk" and found.batch == []
