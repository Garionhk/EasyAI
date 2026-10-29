r"""A moved or copied EasyAI saves into its own output folder.

Run with:  python -m pytest tests/test_moved_folders.py -q

Written after Qwen Image 2.1 results seemed to vanish. They had been saved -
into C:\Users\eddy\Desktop\download\EasyAI - V1.0.1\output\image, the
folder EasyAI had been moved away from, because settings.json held that full
path and EasyAI kept following it from D:\Desktop File\download2.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import app.config as config
from app.config import Config


@pytest.fixture
def here(tmp_path, monkeypatch) -> Path:
    """This copy of EasyAI's folder."""
    folder = tmp_path / "download2" / "EasyAI - V1.1.0"
    folder.mkdir(parents=True)
    (folder / "EasyAI.exe").write_bytes(b"")
    monkeypatch.setattr(config, "ROOT", folder)
    return folder


def settings(folder: Path, **values) -> Path:
    path = folder / "settings.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_a_fresh_easyai_saves_beside_itself(here):
    cfg = Config(here / "settings.json")
    assert cfg.output_dir("image") == here / "output" / "image"
    assert cfg.workflow_dir() == here / "workflows"


def test_the_setting_is_stored_relative_so_the_folder_can_move(here):
    cfg = Config(here / "settings.json")
    cfg.save()
    stored = json.loads((here / "settings.json").read_text(encoding="utf-8"))
    assert stored["output_dir"] == "output"


def test_the_reported_case_the_old_folders_output_is_left_behind(here, tmp_path):
    old = tmp_path / "download" / "EasyAI - V1.0.1"
    (old / "output").mkdir(parents=True)
    (old / "EasyAI.exe").write_bytes(b"")
    cfg = Config(settings(here, output_dir=str(old / "output")))
    assert cfg.output_dir("image") == here / "output" / "image"


def test_an_old_folder_that_no_longer_exists_is_left_behind(here, tmp_path):
    cfg = Config(settings(here, output_dir=str(tmp_path / "gone" / "EasyAI" / "output")))
    assert cfg.output_dir() == here / "output"


def test_a_folder_chosen_in_settings_is_kept(here, tmp_path):
    r"""D:\Renders is someone's choice, not a leftover."""
    renders = tmp_path / "Renders"
    renders.mkdir()
    cfg = Config(settings(here, output_dir=str(renders)))
    assert cfg.output_dir() == renders


def test_an_output_folder_elsewhere_that_is_not_easyais_is_kept(here, tmp_path):
    """Named "output" but not inside an EasyAI folder - still a choice."""
    mine = tmp_path / "Projects" / "output"
    mine.mkdir(parents=True)
    cfg = Config(settings(here, output_dir=str(mine)))
    assert cfg.output_dir() == mine


def test_setups_workflow_folder_is_kept(here, tmp_path):
    """EasyAI Setup points EasyAI at <install>/EasyAI-workflows on purpose."""
    installed = tmp_path / "test" / "EasyAI-workflows"
    installed.mkdir(parents=True)
    cfg = Config(settings(here, workflow_dir=str(installed)))
    assert cfg.workflow_dir() == installed


def test_choosing_a_folder_inside_easyai_stores_it_relative(here):
    cfg = Config(here / "settings.json")
    cfg.set_folder("output_dir", here / "Results")
    assert cfg.get("output_dir") == "Results"
    cfg.set_folder("output_dir", "E:/Elsewhere")
    assert cfg.output_dir() == Path("E:/Elsewhere")


def test_the_exact_layout_found_on_this_machine(here, tmp_path):
    """The old folder no longer holds EasyAI.exe - moving EasyAI took it -
    only the output folder EasyAI kept re-creating, beside other programs."""
    old = tmp_path / "download" / "EasyAI - V1.0.1"
    for sub in ("output/image", "output/video", "output/prompt-enhancer",
                "EasyCanvas", "EasyMiniDirector"):
        (old / sub).mkdir(parents=True)
    cfg = Config(settings(here, output_dir=str(old / "output")))
    assert cfg.output_dir("image") == here / "output" / "image"
