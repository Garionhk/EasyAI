"""Flux 3 Klein image edit and MOSS-Image: the workflows added in 1.2.0.

Run with:  python -m pytest tests/test_flux3_moss.py -q

Each test pins one thing these two workflows needed that nothing before them
did:

* Flux 3 hands its picture to two auto-growing groups - the edit node and the
  batch its prompt upsampler looks at - so both have to grow together.
* Both pick the prompt helper through a ComfySwitchNode, wired the opposite
  way round in each, so the helper switch has to set the switch.
* Flux 3's export keeps an empty latent that feeds nothing; the size controls
  must not be bound to it.
* MOSS ships with a LoRA loader at strength 0: an optional LoRA.
* Both read layout boxes (see tests/test_boxes.py for the boxes themselves).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from app.boxes import Box
from app.comfy.objectinfo import Capabilities, check
from app.ratios import guess_engine, solve_for
from app.workflows import grow
from app.workflows.loader import load_workflow
from app.workflows.manifest import Manifest, autodetect
from app.workflows.patch import GenerationRequest, apply as patch_apply

IMAGE = ROOT / "workflows" / "image"
FLUX = IMAGE / "Flux 3 Klein - Image Edit - GarionHK.json"
MOSS = IMAGE / "Moss Image - Text to Image - GarionHK.json"


def graph_of(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def engine() -> Capabilities:
    """What ComfyUI 0.42 says about the two auto-growing groups."""
    def group(maximum, required):
        template = {"input": {"required" if required else "optional":
                              {"image": ["IMAGE", {}]}},
                    "prefix": "image", "min": 1, "max": maximum}
        return ["COMFY_AUTOGROW_V3", {"template": template}]

    raw = {
        "ReferenceMultiLatent": {"input": {
            "required": {"positive": ["CONDITIONING", {}]},
            "optional": {"images": group(100, False)}}},
        "BatchImagesNode": {"input": {"required": {"images": group(50, True)}}},
    }
    return Capabilities(node_types=set(raw), raw=raw, available=True)


# --- Flux 3 Klein: both picture groups grow ----------------------------------
def test_the_edit_node_and_the_upsamplers_batch_grow_together():
    specs = grow.plan(graph_of(FLUX), engine())
    assert len(specs) == 1
    spec = specs[0]
    assert (spec["node"], spec["loader"]) == ("17", "11")
    assert spec["names"] == [f"image{i}" for i in range(10)]
    assert spec["also"] == [{"node": "50", "group": "images", "names": spec["names"]}]


def test_each_added_picture_feeds_both_groups():
    graph = graph_of(FLUX)
    grow.apply(graph, grow.plan(graph, engine()))
    for i in range(1, 10):
        loader = grow.new_node_id("17", f"image{i}")
        assert graph["17"]["inputs"][f"images.image{i}"] == [loader, 0]
        assert graph["50"]["inputs"][f"images.image{i}"] == [loader, 0]


def test_a_left_out_picture_leaves_both_groups():
    workflow = load_workflow(FLUX, "image", False, None)
    request = GenerationRequest(prompt="x", files={"image_in": __file__},
                                uploaded={"image_in": "a.png"}, boxes=[])
    graph, _ = patch_apply(workflow.graph, workflow.manifest, request)
    for node in ("17", "50"):
        members = [k for k in graph[node]["inputs"] if k.startswith("images.")]
        assert members == ["images.image0"]


def test_the_shipped_manifest_grows_without_the_engine():
    """With ComfyUI closed the slots come from the manifest's record."""
    workflow = load_workflow(FLUX, "image", False, None)
    assert len(workflow.manifest.file_slots("image")) == 10
    assert "images.image9" in workflow.graph["50"]["inputs"]


# --- the size controls -------------------------------------------------------
def test_an_unconnected_latent_gets_no_size_controls():
    graph = graph_of(FLUX)
    graph["44"] = {"class_type": "EmptyFlux2LatentImage",
                   "inputs": {"width": 1024, "height": 1024, "batch_size": 1}}
    manifest = autodetect(graph, "image", "flux3", engine())
    assert not manifest.has("width") and not manifest.has("height")


def test_the_edit_size_follows_the_megapixel_control():
    manifest = autodetect(graph_of(FLUX), "image", "flux3", engine())
    binding = manifest.get("megapixels")
    assert (binding.node, binding.input) == ("17", "resolution.megapixels")
    graph, report = patch_apply(graph_of(FLUX), manifest,
                                GenerationRequest(prompt="x", megapixels=1.5))
    assert graph["17"]["inputs"]["resolution.megapixels"] == 1.5
    assert report.megapixels == 1.5


def test_the_engines_are_recognised():
    assert guess_engine(graph_of(FLUX)) == "flux3-klein"
    assert guess_engine(graph_of(MOSS)) == "moss"


# --- the prompt helper through a switch --------------------------------------
@pytest.mark.parametrize("path,helper,switch,helper_side", [
    (FLUX, "47", "51", False),     # Flux 3: on_false is the upsampler
    (MOSS, "18:11", "18:16", True),  # MOSS: on_true is the enhancer
])
def test_the_helper_switch_sets_the_switch(path, helper, switch, helper_side):
    manifest = Manifest.load(path.with_name(path.stem + ".manifest.json"))
    graph = graph_of(path)
    graph[switch]["inputs"]["switch"] = not helper_side      # shipped "off"

    on, report = patch_apply(graph, manifest, GenerationRequest(prompt="x", enhance=True))
    assert on[switch]["inputs"]["switch"] is helper_side
    assert helper in on and report.enhancer is True

    off, report = patch_apply(graph, manifest, GenerationRequest(prompt="x", enhance=False))
    assert helper not in off, "the helper's language model should not load"
    readers = [v for v in off[switch]["inputs"].values() if isinstance(v, list)]
    assert all(v[0] != helper for v in readers)
    assert report.enhancer is False


def test_the_shipped_switch_settings():
    """Flux 3 ships with its upsampler on, MOSS with its enhancer off."""
    assert graph_of(FLUX)["51"]["inputs"]["switch"] is False
    assert graph_of(MOSS)["18:16"]["inputs"]["switch"] is False


# --- MOSS-Image: the optional LoRA -------------------------------------------
def test_a_lora_at_strength_zero_is_offered_as_optional():
    manifest = autodetect(graph_of(MOSS), "image", "moss")
    assert manifest.get("lora").node == "18:26"
    assert manifest.get("lora_strength").input == "strength_model"


def test_a_working_lora_is_not_offered():
    graph = graph_of(MOSS)
    graph["18:26"]["inputs"]["strength_model"] = 1.0
    assert not autodetect(graph, "image", "moss").has("lora")


def test_no_lora_routes_the_model_around_the_loader():
    manifest = Manifest.load(MOSS.with_name(MOSS.stem + ".manifest.json"))
    graph, report = patch_apply(graph_of(MOSS), manifest,
                                GenerationRequest(prompt="x", lora=""))
    assert "18:26" not in graph
    assert graph["18:24"]["inputs"]["on_false"] == ["18:1", 0]
    assert graph["18:15"]["inputs"]["model"] == ["18:1", 0]
    assert "lora:none" in report.applied


def test_a_chosen_lora_is_loaded_at_its_strength():
    manifest = Manifest.load(MOSS.with_name(MOSS.stem + ".manifest.json"))
    graph, _ = patch_apply(graph_of(MOSS), manifest, GenerationRequest(
        prompt="x", lora="Moss\\mine.safetensors", lora_strength=0.7))
    assert graph["18:26"]["inputs"]["lora_name"] == "Moss\\mine.safetensors"
    assert graph["18:26"]["inputs"]["strength_model"] == 0.7


def test_the_parked_lora_is_not_reported_missing():
    workflow = load_workflow(MOSS, "image", False, None)
    lora_class = workflow.graph["18:26"]["class_type"]
    raw = {node["class_type"]: {"input": {"required": {
        name: [[value]] for name, value in node["inputs"].items() if isinstance(value, str)}}}
        for node in workflow.graph.values()}
    raw[lora_class]["input"]["required"]["lora_name"] = [["Moss\\other.safetensors"]]
    caps = Capabilities(node_types=set(raw), raw=raw, available=True)
    check(workflow, caps)
    assert not any("LaylaML" in m for m in workflow.missing_models)


# --- layout boxes in the shipped workflows ------------------------------------
BOXES = [Box(0.0, 0.0, 1.0, 0.2, "text", "a title", "SALE"),
         Box(0.1, 0.3, 0.4, 0.6, "character", "a knight"),
         Box(0.6, 0.3, 0.3, 0.6, "object", "a dog")]


def test_moss_boxes_share_the_frames_pixel_grid():
    manifest = Manifest.load(MOSS.with_name(MOSS.stem + ".manifest.json"))
    graph, report = patch_apply(graph_of(MOSS), manifest, GenerationRequest(
        prompt="x", ratio="3:2", megapixels=1.0, boxes=BOXES),
        ratio_options=["1:1 (Square)", "3:2 (Photo)"])
    width, height = solve_for("3:2", 1.0, 16)
    assert (report.width, report.height) == (width, height)
    state = graph["20"]["inputs"]["editor_state"]["__value__"]
    assert state[0]["width"] == width          # the title spans the frame
    assert graph["21"]["inputs"]["character_boxes"] == "2"
    assert graph["21"]["inputs"]["width"] == ["19", 0]    # still the chooser's


def test_moss_without_boxes_is_a_plain_prompt():
    manifest = Manifest.load(MOSS.with_name(MOSS.stem + ".manifest.json"))
    graph, _ = patch_apply(graph_of(MOSS), manifest,
                           GenerationRequest(prompt="a fox", boxes=[]))
    assert "20" not in graph and "21" not in graph
    assert graph["18:16"]["inputs"]["on_false"] == ["18:17", 0]
    assert graph["18:11"]["inputs"]["prompt"] == ["18:17", 0]
    assert graph["18:17"]["inputs"]["value"] == "a fox"


def test_flux_without_boxes_keeps_the_words_in_place():
    """The layout node holds the prompt itself; it becomes a plain text node."""
    manifest = Manifest.load(FLUX.with_name(FLUX.stem + ".manifest.json"))
    graph, _ = patch_apply(graph_of(FLUX), manifest,
                           GenerationRequest(prompt="make it snow", boxes=[]))
    assert graph["49"]["class_type"] == "PrimitiveStringMultiline"
    assert graph["49"]["inputs"]["value"] == "make it snow"
    assert "33" not in graph
    assert graph["34"]["inputs"]["source"] == ["49", 0]


def test_flux_boxes_are_drawn_on_the_picture(tmp_path):
    from PIL import Image

    picture = tmp_path / "photo.png"
    Image.new("RGB", (1360, 760)).save(picture)
    manifest = Manifest.load(FLUX.with_name(FLUX.stem + ".manifest.json"))
    graph, _ = patch_apply(graph_of(FLUX), manifest, GenerationRequest(
        prompt="swap <obj_2>", files={"image_in": picture},
        uploaded={"image_in": "photo.png"}, boxes=BOXES))
    canvas = graph["33"]["inputs"]
    assert (canvas["width"], canvas["height"]) == (1360, 768)    # 760 -> 16s
    assert len(canvas["editor_state"]["__value__"]) == 3
    assert canvas["last_incoming"] == {"__value__": []}
    assert graph["49"]["inputs"]["prompt"] == "swap <obj_2>"


def test_the_shipped_workflows_carry_nothing_personal():
    flux, moss = graph_of(FLUX), graph_of(MOSS)
    assert flux["33"]["inputs"]["editor_state"] == {"__value__": []}
    assert "ImageCompare" not in {n["class_type"] for n in flux.values()}
    assert "owl" not in json.dumps(moss).lower()
    assert flux["1"]["inputs"]["unet_name"] == \
        "Flux 3 Klein\\flux3_image_klein_sd_int8_convrot.safetensors"


# --- the Moss model, with ComfyUI closed --------------------------------------
def test_model_lists_are_remembered_for_when_the_engine_is_closed(tmp_path):
    from app.comfy.objectinfo import remember_model_lists, remembered_options

    raw = {"UNETLoader": {"input": {"required": {"unet_name": [[
        r"Moss\moss_image_sft_int8_convrot.safetensors",
        r"Moss\moss_image_base_int8_convrot.safetensors"]]}}}}
    store = tmp_path / "engine-models.json"
    remember_model_lists(Capabilities(node_types=set(raw), raw=raw, available=True), store)
    assert remembered_options("UNETLoader", "unet_name", store) == \
        raw["UNETLoader"]["input"]["required"]["unet_name"][0]
    assert remembered_options("UNETLoader", "nothing", store) == []
    assert remembered_options("UNETLoader", "unet_name", tmp_path / "absent.json") == []


def test_a_chosen_moss_model_is_written_into_the_loader():
    manifest = Manifest.load(MOSS.with_name(MOSS.stem + ".manifest.json"))
    graph, _ = patch_apply(graph_of(MOSS), manifest, GenerationRequest(
        prompt="x", model=r"Moss\moss_image_base_int8_convrot.safetensors"))
    assert graph["18:1"]["inputs"]["unet_name"] == r"Moss\moss_image_base_int8_convrot.safetensors"


# --- the main model, found the same way for every workflow --------------------
def _models_of(path: Path, mode: str):
    from app.workflows.manifest import _find_main_model, _iter_nodes
    graph = graph_of(path)
    found, _ = _find_main_model(graph, [n for n, _ in _iter_nodes(graph)], mode)
    return [(b.node, b.input) for b in found]


def test_ltx_2_3_changes_its_checkpoint_in_every_loader_together():
    path = ROOT / "workflows" / "video" / "LTX 2.3 Video Upscale - GarionHK.json"
    assert _models_of(path, "video") == [
        ("672:423", "ckpt_name"), ("672:435", "ckpt_name"), ("672:445", "ckpt_name")]


def test_an_upscaler_is_never_the_main_model():
    path = ROOT / "workflows" / "video" / "video_ltx2_5_t2v - GarionHK.json"
    assert _models_of(path, "video") == [("405:384", "unet_name")]


def test_the_prompt_helpers_model_is_its_language_model():
    path = ROOT / "workflows" / "prompt-enhancer" / "Krea 2 Prompt Enhancer.json"
    assert _models_of(path, "prompt-enhancer") == [("8", "clip_name")]


def test_every_shipped_workflow_names_its_model():
    import glob
    for f in glob.glob(str(ROOT / "workflows" / "*" / "*.manifest.json")):
        assert Manifest.load(f).has("model"), f


# --- the LoRA list: the model's folder and its subfolders ----------------------
def test_loras_in_subfolders_of_the_family_are_offered():
    from types import SimpleNamespace

    from app.ui.tab_base import GenerateTab

    offered = [r"Anima\a.safetensors", r"Moss\b.safetensors",
               r"Moss\GarionHK_v3\c.safetensors", r"Moss\3ll3nchan_v3\d.safetensors",
               r"Mossy\not_this_one.safetensors"]
    stub = SimpleNamespace(
        _engine_options=lambda cls, field: offered,
        _model_options=lambda m, w: ([r"Moss\moss_image_sft_int8_convrot.safetensors"],
                                     r"Moss\moss_image_sft_int8_convrot.safetensors"),
        _family_of=GenerateTab._family_of)
    manifest = Manifest.load(MOSS.with_name(MOSS.stem + ".manifest.json"))
    workflow = SimpleNamespace(graph=graph_of(MOSS))
    assert GenerateTab._lora_options(stub, manifest, workflow) == [
        r"Moss\b.safetensors", r"Moss\3ll3nchan_v3\d.safetensors",
        r"Moss\GarionHK_v3\c.safetensors"]
