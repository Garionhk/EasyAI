"""Aspect ratio -> pixel dimensions, per model family.

Two mechanisms, because image and video models want different things:

* **Image engines** use a solver: hold the pixel budget (megapixels) roughly
  constant, preserve the ratio as exactly as possible, and snap both sides to
  the engine's required multiple. This is the same idea as the
  `ResolutionSelector` custom node (ratio + megapixels + multiple) already used
  in the MiniMax workflow.

* **Video engines** use an explicit ladder. Video models are trained on a short
  list of resolutions and arbitrary sizes cost quality and VRAM, so guessing is
  worse than looking the answer up.

Adding a model is one entry in ENGINES (plus a LADDERS entry if it's video).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

# Ratios offered in the UI, in display order. Key is the label shown to the user.
RATIOS: dict[str, tuple[int, int]] = {
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "3:2": (3, 2),
    "2:3": (2, 3),
    "16:9": (16, 9),
    "9:16": (9, 16),
    "21:9": (21, 9),
    "9:21": (9, 21),
}

RATIO_ORDER = tuple(RATIOS.keys())


@dataclass(frozen=True)
class Engine:
    """Resolution rules for one model family."""
    key: str
    label: str
    megapixels: float
    multiple: int
    min_side: int
    max_side: int
    is_video: bool = False
    #: Exact sizes that win over the solver, for ratios with a canonical answer.
    overrides: dict[str, tuple[int, int]] | None = None
    #: Some pipelines quietly round the size down before generating. LTX floors
    #: both sides to a multiple of 64 - measured: a chooser asking for
    #: 1376x768 produced 1344x768, and 960x544 produced 960x512. MiniMax does
    #: not, so this is per engine rather than a blanket rule for video.
    output_grid: int | None = None


# --- Image engines --------------------------------------------------------
# Flux-class, Krea and Z-Image are all ~1 megapixel models that want sides
# divisible by 16. The only override is the square, where 1024x1024 is the
# canonical training size and the solver would otherwise land on 1008x1008.
_SQUARE_1024 = {"1:1": (1024, 1024)}

ENGINES: dict[str, Engine] = {
    "flux2": Engine(
        "flux2", "Flux 2", megapixels=1.0, multiple=16,
        min_side=512, max_side=2048, overrides=_SQUARE_1024,
    ),
    "flux2-klein": Engine(
        "flux2-klein", "Flux 2 Klein", megapixels=1.0, multiple=16,
        min_side=512, max_side=2048, overrides=_SQUARE_1024,
    ),
    # Flux 3 Klein edits: the output takes its size from the reference
    # pictures, resized to the budget the edit node is given.
    "flux3-klein": Engine(
        "flux3-klein", "Flux 3 Klein", megapixels=1.0, multiple=16,
        min_side=512, max_side=2048, overrides=_SQUARE_1024,
    ),
    # MOSS-Image is trained at 1 to 2 megapixels and the shipped workflow uses
    # 2, so that is the default budget. Sides only need to be multiples of 16.
    "moss": Engine(
        "moss", "MOSS-Image", megapixels=2.0, multiple=16,
        min_side=512, max_side=2560,
    ),
    "krea2": Engine(
        "krea2", "Krea 2", megapixels=1.0, multiple=16,
        min_side=512, max_side=2048, overrides=_SQUARE_1024,
    ),
    "z-image-turbo": Engine(
        "z-image-turbo", "Z-Image Turbo", megapixels=1.0, multiple=16,
        min_side=512, max_side=1536, overrides=_SQUARE_1024,
    ),
    "qwen-image": Engine(
        "qwen-image", "Qwen Image", megapixels=1.0, multiple=16,
        min_side=512, max_side=2048, overrides=_SQUARE_1024,
    ),
    "flux1": Engine(
        "flux1", "Flux 1", megapixels=1.0, multiple=16,
        min_side=512, max_side=2048, overrides=_SQUARE_1024,
    ),
    "sdxl": Engine(
        "sdxl", "SDXL", megapixels=1.0, multiple=64,
        min_side=512, max_side=1536, overrides=_SQUARE_1024,
    ),

    # --- Video engines ----------------------------------------------------
    "ltx2.5": Engine(
        "ltx2.5", "LTX 2.5", megapixels=0.85, multiple=32,
        min_side=480, max_side=1600, is_video=True, output_grid=64,
    ),
    "ltx2": Engine(
        "ltx2", "LTX 2", megapixels=0.85, multiple=32,
        min_side=480, max_side=1600, is_video=True, output_grid=64,
    ),
    "minimax-h3": Engine(
        "minimax-h3", "MiniMax H3", megapixels=1.05, multiple=32,
        min_side=480, max_side=1600, is_video=True,
    ),
    "wan2.2": Engine(
        "wan2.2", "Wan 2.2", megapixels=0.5, multiple=32,
        min_side=384, max_side=1280, is_video=True,
    ),

    # Fallback when a manifest names no engine, or names one we don't know.
    "_default": Engine(
        "_default", "Generic", megapixels=1.0, multiple=64,
        min_side=512, max_side=2048, overrides=_SQUARE_1024,
    ),
}


# Video ladders. Every value is divisible by 32. These are the sizes the model
# families are actually trained/tested at - edit here if a workflow prefers
# something else, and the UI picks it up with no code change.
LADDERS: dict[str, dict[str, tuple[int, int]]] = {
    "ltx2.5": {
        "1:1":  (960, 960),
        "4:3":  (1152, 864),
        "3:4":  (864, 1152),
        "3:2":  (1216, 800),
        "2:3":  (800, 1216),
        "16:9": (1216, 704),
        "9:16": (704, 1216),
        "21:9": (1536, 640),
        "9:21": (640, 1536),
    },
    "minimax-h3": {
        "1:1":  (1024, 1024),
        "4:3":  (1152, 864),
        "3:4":  (864, 1152),
        "3:2":  (1248, 832),
        "2:3":  (832, 1248),
        "16:9": (1344, 768),   # the MiniMaxH3ReferenceToVideo node default
        "9:16": (768, 1344),
        "21:9": (1568, 672),
        "9:21": (672, 1568),
    },
    "wan2.2": {
        "1:1":  (704, 704),
        "4:3":  (832, 608),
        "3:4":  (608, 832),
        "3:2":  (864, 576),
        "2:3":  (576, 864),
        "16:9": (832, 480),
        "9:16": (480, 832),
        "21:9": (1120, 480),
        "9:21": (480, 1120),
    },
}
LADDERS["ltx2"] = LADDERS["ltx2.5"]


def get_engine(key: str | None) -> Engine:
    """Look up an engine, falling back to the generic profile."""
    if not key:
        return ENGINES["_default"]
    return ENGINES.get(str(key).strip().lower(), ENGINES["_default"])


def _snap(value: float, multiple: int, lo: int, hi: int) -> int:
    snapped = int(round(value / multiple)) * multiple
    snapped = max(lo, min(hi, snapped))
    # Clamping can knock us off the multiple, so re-snap inward.
    if snapped % multiple:
        snapped = (snapped // multiple) * multiple
    return max(multiple, snapped)


def solve(ratio: str, engine: Engine) -> tuple[int, int]:
    """Best (width, height) for a ratio under an engine's rules.

    Scans every candidate width on the engine's grid, derives the height that
    best preserves the ratio, and scores the pair on how far it drifts from the
    exact ratio and from the target pixel budget. Ratio accuracy is weighted
    heavily so that ratios with an exact grid solution (2:3, 3:4, 9:16) get it.
    """
    rw, rh = RATIOS[ratio]
    target_ratio = rw / rh
    target_area = engine.megapixels * 1_000_000
    m = engine.multiple

    best: tuple[float, int, int] | None = None
    lo = max(m, (engine.min_side // m) * m)
    hi = (engine.max_side // m) * m

    for w in range(lo, hi + 1, m):
        h = _snap(w / target_ratio, m, engine.min_side, engine.max_side)
        if not (engine.min_side <= h <= engine.max_side):
            continue
        ratio_err = abs(math.log((w / h) / target_ratio))
        area_err = abs(math.log((w * h) / target_area))
        score = 20.0 * ratio_err + area_err
        # Prefer the larger candidate on a tie so results are deterministic.
        if best is None or score < best[0] - 1e-12:
            best = (score, w, h)

    if best is None:                       # pathological engine bounds
        side = _snap(math.sqrt(target_area), m, engine.min_side, engine.max_side)
        return side, side
    return best[1], best[2]


#: The range the control offers. A workflow built outside it keeps its own
#: value - several video workflows here sit at 0.4, and rounding them up would
#: change a result their author tuned.
MEGAPIXEL_MIN = 0.5
MEGAPIXEL_MAX = 2.0
MEGAPIXEL_STEP = 0.1

#: Absolute limits, enforced when writing into a workflow. Wider than the
#: offered range on purpose: this is a guard against nonsense, not a policy.
MEGAPIXEL_FLOOR = 0.05
MEGAPIXEL_CEILING = 8.0


def clamp_megapixels(value: float) -> float:
    """Keep a budget within sane limits without overriding a deliberate choice."""
    return round(min(MEGAPIXEL_CEILING, max(MEGAPIXEL_FLOOR, float(value))), 2)


def resolve(ratio: str, engine_key: str | None,
            megapixels: float | None = None) -> tuple[int, int]:
    """Public entry point: ratio label + engine key -> (width, height).

    Order of precedence: video ladder, engine override, solver.

    ``megapixels`` overrides the engine's own pixel budget. The ladder and the
    override table are skipped in that case - both are fixed sizes chosen for
    the default budget, so honouring them would silently ignore the request.
    """
    if ratio not in RATIOS:
        ratio = "1:1"
    engine = get_engine(engine_key)

    if megapixels is not None:
        return solve(ratio, _rebudget(engine, megapixels))

    ladder = LADDERS.get(engine.key)
    if ladder and ratio in ladder:
        return ladder[ratio]
    if engine.overrides and ratio in engine.overrides:
        return engine.overrides[ratio]
    return solve(ratio, engine)


def _rebudget(engine: Engine, megapixels: float) -> Engine:
    """The same engine with a different pixel budget.

    The side limits move with it: a 2 megapixel 21:9 frame is wider than the
    default cap allows, and leaving the cap alone would quietly hand back a
    much smaller picture than was asked for.
    """
    megapixels = clamp_megapixels(megapixels)
    growth = math.sqrt(megapixels / engine.megapixels) if engine.megapixels else 1.0
    return replace(
        engine,
        megapixels=megapixels,
        max_side=max(engine.max_side, int(engine.max_side * growth)),
    )


#: A "megapixel" to a size-chooser node means 1024 x 1024, not 1,000,000.
#: Measured against ComfyUI's ResolutionSelector: 1:1 at 1.0 comes back as
#: exactly 1024x1024, and every other ratio matches this budget too.
PIXELS_PER_MEGAPIXEL = 1024 * 1024


def solve_for(ratio: str, megapixels: float, multiple: int) -> tuple[int, int]:
    """Reproduce what a size-chooser node will calculate.

    Nodes like ``ResolutionSelector`` hold their own megapixel target and
    rounding step, and they - not EasyAI - decide the final pixels. Working it
    out the same way lets the shape chooser show the size the file will actually
    have instead of a plausible-looking guess.

    Verified against the live node across three ratios, four budgets and three
    rounding steps; see tests/test_megapixels.py.
    """
    if ratio not in RATIOS:
        ratio = "1:1"
    rw, rh = RATIOS[ratio]
    multiple = max(1, int(multiple or 1))
    scale = math.sqrt(max(0.01, megapixels) * PIXELS_PER_MEGAPIXEL / (rw * rh))
    width = max(multiple, int(round(rw * scale / multiple)) * multiple)
    height = max(multiple, int(round(rh * scale / multiple)) * multiple)
    return width, height


def snap_to_output_grid(size: tuple[int, int], engine_key: str | None) -> tuple[int, int]:
    """Apply any rounding the pipeline will do after the size is chosen.

    Without this the shape chooser promises a size the file will not have: an
    LTX workflow asked for 1376x768 and produced 1344x768.
    """
    grid = get_engine(engine_key).output_grid
    if not grid:
        return size
    return (max(grid, (size[0] // grid) * grid),
            max(grid, (size[1] // grid) * grid))


def read_node_profile(node: dict | None) -> tuple[float, int] | None:
    """(megapixels, multiple) from a size-chooser node, if it states them."""
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs") or {}
    megapixels = inputs.get("megapixels")
    multiple = inputs.get("multiple")
    if isinstance(megapixels, (int, float)) and not isinstance(megapixels, bool) \
            and isinstance(multiple, (int, float)) and not isinstance(multiple, bool):
        return float(megapixels), int(multiple)
    return None


def describe(ratio: str, engine_key: str | None) -> str:
    """Label for the ratio picker, e.g. '2:3  -  832 x 1248'."""
    w, h = resolve(ratio, engine_key)
    return f"{ratio}   —   {w} × {h}"


#: Model families, most specific first. Order decides ties.
_ENGINE_NEEDLES = (
    ("minimax", "minimax-h3"),
    ("ltx-2.5", "ltx2.5"), ("ltx2.5", "ltx2.5"), ("ltx_2.5", "ltx2.5"),
    ("ltx", "ltx2"),
    ("wan2.2", "wan2.2"), ("wan22", "wan2.2"), ("wan", "wan2.2"),
    ("moss", "moss"),
    ("flux3", "flux3-klein"), ("flux-3", "flux3-klein"), ("flux 3", "flux3-klein"),
    ("klein", "flux2-klein"),
    ("flux-2", "flux2"), ("flux2", "flux2"),
    ("krea", "krea2"),
    ("z_image", "z-image-turbo"), ("z-image", "z-image-turbo"),
    ("zimage", "z-image-turbo"),
    ("qwen", "qwen-image"),
    ("flux", "flux1"),
    ("sdxl", "sdxl"),
)

#: Anything that looks like a weights file rather than prose.
_MODEL_FILE = re.compile(r"\.(safetensors|gguf|ckpt|pt|pth|bin)\b", re.I)


def _mentions(blob: str, needle: str) -> bool:
    """Substring match that will not fire in the middle of a longer word.

    Without the guard, "wan" matches inside "PreviewAny", so any workflow with
    a preview node looked like a Wan video model and got its pixel sizes.
    """
    return re.search(r"(?<![a-z0-9])" + re.escape(needle), blob) is not None


def guess_engine(graph: dict) -> str | None:
    """Infer an engine key from the model filenames and node types in a graph.

    Best-effort only: it seeds the 'engine' field when a manifest is generated,
    and the user can correct it under Set up….
    """
    model_names: list[str] = []
    other_text: list[str] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        for value in (node.get("inputs") or {}).values():
            if isinstance(value, str):
                (model_names if _MODEL_FILE.search(value) else other_text).append(value.lower())

    classes = " ".join(str(n.get("class_type", "")).lower()
                       for n in graph.values() if isinstance(n, dict))

    # A model filename is the strongest signal, then node types, then any
    # other text lying around (which is mostly prompts, so it is least
    # trustworthy - a prompt mentioning "flux" says nothing about the model).
    for blob in (" ".join(model_names), classes, " ".join(other_text)):
        if not blob:
            continue
        for needle, key in _ENGINE_NEEDLES:
            if _mentions(blob, needle):
                return key
    return None
