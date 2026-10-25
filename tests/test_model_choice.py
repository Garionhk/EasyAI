"""Choosing a different model from the same family.

Run with:  python -m pytest tests/test_model_choice.py -q

The failure that matters is a quiet one: writing a model the engine never
offered produces a run that fails deep inside ComfyUI, naming a file the user
did not choose. So the rule under test throughout is that only an offered
value is ever written.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.workflows.manifest import autodetect
from app.workflows.patch import GenerationRequest, apply as patch_apply

ZI = "ZI\\z_image_turbo_bf16.safetensors"
ZI_OTHER = "ZI\\zImageTurboNSFW_10FP8.safetensors"
KLEIN = "Flux 2\\flux-2-klein-9b-Q8_0.gguf"


def graph(model: str = ZI, loader: str = "UNETLoader", field: str = "unet_name"):
    return {
        "1": {"class_type": loader, "inputs": {field: model}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }


# --- finding the model ------------------------------------------------------
def test_the_main_model_is_found_and_bound():
    manifest = autodetect(graph(), "image", "wf")
    binding = manifest.get("model")
    assert binding is not None
    assert binding.node == "1"
    assert binding.input == "unet_name"


def test_a_wired_model_input_is_not_offered():
    """A model computed by another node cannot be swapped for a filename."""
    wired = graph()
    wired["1"]["inputs"]["unet_name"] = ["9", 0]
    manifest = autodetect(wired, "image", "wf")
    assert not manifest.has("model")


@pytest.mark.parametrize("loader,field", [
    ("UNETLoader", "unet_name"),
    ("UnetLoaderGGUF", "unet_name"),
    ("CheckpointLoaderSimple", "ckpt_name"),
])
def test_every_kind_of_main_loader_is_recognised(loader, field):
    manifest = autodetect(graph(loader=loader, field=field), "image", "wf")
    assert manifest.has("model"), f"{loader} was not recognised"


# --- the family -------------------------------------------------------------
def family_of(name: str) -> str:
    from app.ui.tab_base import GenerateTab
    return GenerateTab._family_of(name)


def test_the_family_is_the_subfolder():
    assert family_of(ZI) == "ZI"
    assert family_of(KLEIN) == "Flux 2"
    assert family_of("Flux 2/klein.gguf") == "Flux 2"      # either slash


def test_a_loose_file_is_its_own_family():
    """Otherwise every loose model would be offered alongside every subfolder."""
    assert family_of("ae.safetensors") == ""
    assert family_of(ZI) != family_of("ae.safetensors")


def test_only_the_same_folder_counts_as_family():
    offered = [ZI, ZI_OTHER, KLEIN, "Anima\\anima_baseV10.safetensors"]
    siblings = [o for o in offered if family_of(o) == family_of(ZI)]
    assert siblings == [ZI, ZI_OTHER]


# --- the control ------------------------------------------------------------
@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_one_model_still_gets_a_control(qt_app):
    """Flux 2 Klein exactly: a lone GGUF in its folder. Every workflow shows
    its model and lets it be changed, so the dropdown is there regardless."""
    from app.ui.widgets import ModelPicker

    picker = ModelPicker()
    assert picker.set_options([KLEIN], KLEIN) is True
    assert not picker.isHidden()
    assert picker.current() == KLEIN


def test_several_models_give_a_control_with_the_original_marked(qt_app):
    from app.ui.widgets import ModelPicker

    picker = ModelPicker()
    assert picker.set_options([ZI, ZI_OTHER], ZI) is True
    labels = [picker.combo.itemText(i) for i in range(picker.combo.count())]
    assert any("(in the workflow)" in text for text in labels)
    assert picker.current() == ZI, "should start on the workflow's own model"


def test_a_remembered_choice_is_restored(qt_app):
    from app.ui.widgets import ModelPicker

    picker = ModelPicker()
    picker.set_options([ZI, ZI_OTHER], ZI, chosen=ZI_OTHER)
    assert picker.current() == ZI_OTHER


def test_a_remembered_model_that_has_gone_falls_back(qt_app):
    """Deleting a model must not leave a workflow pointing at nothing."""
    from app.ui.widgets import ModelPicker

    picker = ModelPicker()
    picker.set_options([ZI, ZI_OTHER], ZI, chosen="ZI\\deleted_model.safetensors")
    assert picker.current() == ZI


# --- patching ---------------------------------------------------------------
def test_the_chosen_model_reaches_the_right_input():
    manifest = autodetect(graph(), "image", "wf")
    patched, report = patch_apply(
        graph(), manifest, GenerationRequest(prompt="x", model=ZI_OTHER))
    assert patched["1"]["inputs"]["unet_name"] == ZI_OTHER
    assert "model" in report.applied


def test_no_choice_leaves_the_workflow_alone():
    manifest = autodetect(graph(), "image", "wf")
    patched, _ = patch_apply(graph(), manifest, GenerationRequest(prompt="x"))
    assert patched["1"]["inputs"]["unet_name"] == ZI


def test_a_workflow_without_a_model_binding_is_unaffected():
    wired = graph()
    wired["1"]["inputs"]["unet_name"] = ["9", 0]
    manifest = autodetect(wired, "image", "wf")
    patched, report = patch_apply(
        wired, manifest, GenerationRequest(prompt="x", model=ZI_OTHER))
    assert patched["1"]["inputs"]["unet_name"] == ["9", 0]
    assert "model" not in report.applied


def test_two_queued_runs_keep_their_own_models():
    """Each item snapshots its choice, so changing the dropdown to set up the
    next run cannot alter one already waiting."""
    manifest = autodetect(graph(), "image", "wf")
    first = GenerationRequest(prompt="x", model=ZI)
    second = GenerationRequest(prompt="x", model=ZI_OTHER)

    one, _ = patch_apply(graph(), manifest, first)
    two, _ = patch_apply(graph(), manifest, second)
    assert one["1"]["inputs"]["unet_name"] == ZI
    assert two["1"]["inputs"]["unet_name"] == ZI_OTHER


# --- the workflow file is never touched -------------------------------------
def test_choosing_a_model_never_rewrites_the_workflow(tmp_path):
    """The choice lives in settings, so the workflow can still be shared or
    re-added through EasyAI Studio exactly as exported."""
    path = tmp_path / "wf.json"
    original = graph()
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()

    manifest = autodetect(original, "image", "wf")
    patched, _ = patch_apply(original, manifest,
                             GenerationRequest(prompt="x", model=ZI_OTHER))

    assert patched["1"]["inputs"]["unet_name"] == ZI_OTHER
    assert original["1"]["inputs"]["unet_name"] == ZI, "patched in place"
    assert path.read_bytes() == before
