"""Settings shared by every test."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _keep_the_real_model_lists(tmp_path, monkeypatch):
    """Tests run against stand-in engines; what those offer must never be
    written over the model lists the real ComfyUI last gave, which the Model
    and LoRA dropdowns fall back on while it is closed."""
    from app.comfy import objectinfo

    monkeypatch.setattr(objectinfo, "_model_lists_path",
                        lambda: tmp_path / objectinfo.MODEL_LISTS_FILE)
