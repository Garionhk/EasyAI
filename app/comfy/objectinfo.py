"""Answers "will this workflow actually run here?" before the user clicks it.

This is the reason EasyAI exists. A workflow that names a custom node the user
never installed, or a model file they never downloaded, fails deep inside
ComfyUI and surfaces as a stack trace. Here the same problem is caught up front
and named: *"Needs add-on: ComfyUI-GGUF"*.

Everything is derived from ``GET /object_info``, which lists every node type the
running ComfyUI knows about, and - for loader nodes - the exact list of model
files it can see:

    object_info["UnetLoaderGGUF"]["input"]["required"]["unet_name"][0]
      -> ["Flux 2\\flux-2-klein-9b-Q8_0.gguf", "QWEN\\...", ...]
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.comfy.client import ComfyClient, ComfyError
from app.i18n import plural, t
from app.workflows.loader import Workflow

# class_type prefix/name -> the add-on that provides it. Used to turn
# "UnetLoaderGGUF is unknown" into "install ComfyUI-GGUF".
NODE_PACKS: dict[str, str] = {
    "UnetLoaderGGUF": "ComfyUI-GGUF",
    "CLIPLoaderGGUF": "ComfyUI-GGUF",
    "DualCLIPLoaderGGUF": "ComfyUI-GGUF",
    "VHS_": "ComfyUI-VideoHelperSuite",
    "LTXV": "ComfyUI-LTXVideo",
    "MiniMax": "comfyui-spectrum-minimax-h3",
    "Qwen3": "comfyui-qwen3-tts",
    "QwenVL": "ComfyUI-QwenVL",
    "SimplePromptBatcher": "comfyui-simple-prompt-batcher",
    "ResolutionSelector": "controlaltai-nodes",
    "LayerUtility": "ComfyUI_LayerStyle",
    "LayerMask": "ComfyUI_LayerStyle",
    "easy ": "comfyui-easy-use",
    "ImpactWildcard": "comfyui-impact-pack",
    "SeedVR2": "seedvr2_videoupscaler",
    "RMBG": "comfyui-rmbg",
    "Krea2": "comfyui-krea2edit",
    "Flux2Klein": "ComfyUI-Flux2Klein-Enhancer",
    "rgthree": "rgthree-comfy",
    "KJ": "comfyui-kjnodes",
    "ScaleImageToTotalPixels": "scale-image-to-total-pixels-advanced",
}

#: Loader inputs whose value is a filename picked from an enum. Checking these
#: catches "you never downloaded that model" before the job is queued.
MODEL_FIELDS = (
    "ckpt_name", "unet_name", "vae_name", "clip_name", "clip_name1", "clip_name2",
    "lora_name", "control_net_name", "model_name", "style_model_name",
    "gguf_name", "upscale_model_name", "text_encoder_name", "audio_encoder_name",
)


@dataclass
class Capabilities:
    """A snapshot of what the connected ComfyUI can do."""
    node_types: set[str] = field(default_factory=set)
    raw: dict = field(default_factory=dict)
    available: bool = False
    error: str = ""

    def has_node(self, class_type: str) -> bool:
        return class_type in self.node_types

    def enum_options(self, class_type: str, field_name: str) -> list[str] | None:
        """The list of values an input accepts, or None if it isn't a choice.

        ComfyUI writes these two ways. The old style puts the list first::

            "ckpt_name": [["a.safetensors", "b.safetensors"], {...}]

        The newer V3 style names the type and carries the list in the options::

            "aspect_ratio": ["COMBO", {"options": ["1:1 (Square)", ...]}]

        Only reading the first shape means V3 inputs look like free text, and
        the missing-model check quietly passes everything.
        """
        node = self.raw.get(class_type, {}).get("input", {})
        spec = node.get("required", {}).get(field_name)
        if spec is None:
            spec = node.get("optional", {}).get(field_name)
        if not isinstance(spec, list) or not spec:
            return None

        if isinstance(spec[0], list):
            return [str(x) for x in spec[0]]
        if len(spec) > 1 and isinstance(spec[1], dict):
            options = spec[1].get("options")
            if isinstance(options, list):
                return [str(x) for x in options]
        return None

    #: Kept as the old name because the pre-flight check reads like a question
    #: about models, not about enums.
    def model_options(self, class_type: str, field_name: str) -> list[str] | None:
        return self.enum_options(class_type, field_name)

    def autogrow(self, class_type: str, group: str) -> dict | None:
        """The members an auto-growing input group accepts, or None.

        ComfyUI describes these two ways. Qwen Image 2.1 lists every name::

            "images": ["COMFY_AUTOGROW_V3", {"template": {
                "names": ["image_1", ..., "image_16"], "min": 0}}]

        MiniMax H3 gives a prefix and a count instead::

            "ref_images": ["COMFY_AUTOGROW_V3", {"template": {
                "prefix": "ref_image_", "min": 0, "max": 9}}]

        Both come back as ``{"names": [...], "min": n}``, the names in the
        order the node reads them.
        """
        node = self.raw.get(class_type, {}).get("input", {})
        spec = (node.get("required") or {}).get(group)
        if spec is None:
            spec = (node.get("optional") or {}).get(group)
        if not (isinstance(spec, list) and len(spec) > 1
                and spec[0] == "COMFY_AUTOGROW_V3" and isinstance(spec[1], dict)):
            return None
        template = spec[1].get("template") or {}
        names = template.get("names")
        if not names and template.get("prefix") is not None:
            names = [f"{template['prefix']}{i}" for i in range(int(template.get("max") or 0))]
        if not names:
            return None
        return {"names": [str(n) for n in names], "min": int(template.get("min") or 0)}

    def is_optional_input(self, class_type: str, field_name: str) -> bool:
        """Can this input simply be left out of the graph?

        Auto-growing groups arrive as ``ref_images.ref_image_1`` - one entry per
        attached file, under a single optional group called ``ref_images`` - so
        the part before the dot is what to look up.

        A group can also sit among the *required* inputs while needing none of
        its members: Qwen Image 2.1's ``images`` says ``min: 0``, meaning it
        runs perfectly well with no reference picture at all. Only the first
        ``min`` members of such a group are really required.
        """
        node = self.raw.get(class_type, {}).get("input", {})
        optional = node.get("optional") or {}
        required = node.get("required") or {}

        group, _, member = field_name.partition(".")
        for name in (field_name, group):
            if name in optional:
                return True
            if name in required:
                grow = self.autogrow(class_type, group) if member else None
                if grow and member in grow["names"]:
                    return grow["names"].index(member) >= grow["min"]
                return False
        # Unknown input: assume it matters. Dropping something the node needs
        # fails the whole run, while keeping something it doesn't costs nothing.
        return False


def pack_for(class_type: str) -> str:
    """Best guess at which add-on provides a node type."""
    for needle, pack in NODE_PACKS.items():
        if class_type.startswith(needle) or needle in class_type:
            return pack
    return ""


def fetch(client: ComfyClient, refresh: bool = False) -> Capabilities:
    """Read /object_info once and wrap it."""
    try:
        raw = client.object_info(refresh=refresh)
    except ComfyError as e:
        return Capabilities(available=False, error=str(e))
    caps = Capabilities(node_types=set(raw.keys()), raw=raw, available=True)
    remember_model_lists(caps)
    return caps


# --- model lists kept for when ComfyUI is closed -----------------------------
#: The file lists ComfyUI last offered for each model input. The Model and
#: LoRA dropdowns come from the running engine, so with ComfyUI closed they
#: used to vanish - MOSS-Image's four diffusion models among them - right when
#: someone was choosing what to run next.
MODEL_LISTS_FILE = "engine-models.json"


def _model_lists_path():
    from app.paths import data_dir
    return data_dir() / MODEL_LISTS_FILE


def remember_model_lists(caps: Capabilities, path=None) -> None:
    """Write down every model list the engine offers. Best effort."""
    import json
    lists: dict[str, dict[str, list[str]]] = {}
    for class_type in caps.raw:
        for field_name in MODEL_FIELDS:
            options = caps.enum_options(class_type, field_name)
            if options:
                lists.setdefault(class_type, {})[field_name] = options
    if not lists:
        return
    target = path or _model_lists_path()
    try:
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(lists, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


def remembered_options(class_type: str, field_name: str, path=None) -> list[str]:
    """What the engine offered for this input last time it was running."""
    import json
    try:
        lists = json.loads((path or _model_lists_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = (lists.get(class_type) or {}).get(field_name) or []
    return [str(x) for x in found] if isinstance(found, list) else []


class FolderMap:
    """Which models folder each loader input reads from, asked of ComfyUI.

    A loader's option list is exactly one folder's listing, so comparing the
    two identifies the folder with no table to maintain and no chance of the
    unet / diffusion_models confusion that put nine models in folders ComfyUI
    never looks in.

    Both listings are fetched once and reused; /models/{folder} is a directory
    walk on the server and there are twenty-odd folders.
    """

    def __init__(self, client, caps: Capabilities):
        self.client = client
        self.caps = caps
        self._by_folder: dict[str, set[str]] = {}
        self._loaded = False

    def load(self) -> bool:
        if self._loaded:
            return bool(self._by_folder)
        for folder in self.client.model_folders():
            files = self.client.files_in_folder(folder)
            if files:
                self._by_folder[folder] = {_normalise(f) for f in files}
        self._loaded = True
        return bool(self._by_folder)

    def folder_for(self, class_type: str, field_name: str,
                   filename: str = "") -> str | None:
        """The folder a loader input feeds, or None if it cannot be settled."""
        self.load()
        if not self._by_folder:
            return None

        options = self.caps.enum_options(class_type, field_name)
        if options:
            wanted = {_normalise(o) for o in options}
            # An exact match is the loader's own folder. Several folders can
            # hold the same file, so the whole list has to agree, not one name.
            for folder, files in self._by_folder.items():
                if files == wanted:
                    return folder
            best, score = None, 0.0
            for folder, files in self._by_folder.items():
                if not files:
                    continue
                overlap = len(files & wanted) / len(wanted)
                if overlap > score:
                    best, score = folder, overlap
            if score >= 0.9:
                return best

        # No usable option list: fall back to whichever folder holds this file.
        if filename:
            target = _normalise(filename)
            holders = [f for f, files in self._by_folder.items() if target in files]
            if len(holders) == 1:
                return holders[0]
        return None


def _normalise(name: str) -> str:
    """Model paths differ only by slash direction between OSes and workflows."""
    return name.replace("\\", "/").strip().lower()


def check(workflow: Workflow, caps: Capabilities) -> Workflow:
    """Fill in ``missing_nodes`` / ``missing_models`` on a workflow, in place."""
    workflow.missing_nodes = []
    workflow.missing_models = []
    workflow.checked = False

    if not workflow.loadable or not caps.available:
        return workflow

    # An optional LoRA is the user's to pick, and "None" takes its loader out,
    # so the file the author parked there is not something to install.
    manifest = getattr(workflow, "manifest", None)
    optional = {(b.node, b.input) for b in manifest.all("lora")} if manifest else set()

    for node_id, node in workflow.graph.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        if not class_type:
            continue

        if not caps.has_node(class_type):
            pack = pack_for(class_type)
            workflow.missing_nodes.append(pack or class_type)
            continue  # can't check its models if the node itself is absent

        for field_name, value in (node.get("inputs") or {}).items():
            if field_name not in MODEL_FIELDS or not isinstance(value, str) or not value:
                continue
            if (str(node_id), field_name) in optional:
                continue
            options = caps.model_options(class_type, field_name)
            if options is None:
                continue
            if _normalise(value) not in {_normalise(o) for o in options}:
                workflow.missing_models.append(value)

    workflow.checked = True
    return workflow


def check_all(workflows: list[Workflow], caps: Capabilities) -> list[Workflow]:
    for wf in workflows:
        check(wf, caps)
    return workflows


def install_hint(workflow: Workflow) -> str:
    """Text for the 'what do I do about it' box on a blocked workflow card."""
    lines: list[str] = []

    if workflow.missing_nodes:
        packs = sorted(set(workflow.missing_nodes))
        lines.append(t("This workflow needs add-ons that are not installed:"))
        lines.extend(f"    • {p}" for p in packs)
        lines.append("")
        lines.append(t(
            "To install them: open ComfyUI → Manager → Custom Nodes Manager, "
            "search for each name above, click Install, then restart ComfyUI."))

    if workflow.missing_models:
        if lines:
            lines.append("")
        models = sorted(set(workflow.missing_models))
        lines.append(t("These model files are missing:"))
        lines.extend(f"    • {m}" for m in models)
        lines.append("")
        lines.append(t(
            "Download each one and put it in the matching folder inside your "
            "ComfyUI models directory, then restart ComfyUI."))

    return "\n".join(lines)


def summarise(workflows: list[Workflow]) -> str:
    """One line for the status bar, e.g. '6 ready, 2 need add-ons'."""
    ready = sum(1 for w in workflows if w.ready)
    blocked = sum(1 for w in workflows if w.loadable and not w.ready)
    broken = sum(1 for w in workflows if not w.loadable)

    parts = [t("{n} ready", n=ready)]
    if blocked:
        parts.append(plural(blocked, "{n} needs something installed",
                            "{n} need something installed"))
    if broken:
        parts.append(t("{n} not usable", n=broken))
    return ", ".join(parts)
