"""Build setup/catalog.json - the lockfile EasyAISetup installs from.

    python tools/make_catalog.py

Everything is read from this machine, so the catalogue describes a setup that
demonstrably works rather than one assembled from guesses:

* **ComfyUI's exact commit** and the custom node versions are read from the
  install itself - each checkout's git commit, or a registry pack's
  pyproject.toml version. (ComfyUI-Manager's snapshots were used before, but
  they stop being written when Manager is not loaded and quietly go stale.)
* **Which models and nodes each group needs** comes from EasyAI's own workflow
  files, so the catalogue tracks the workflows automatically.
* **Download URLs** are harvested from ComfyUI's bundled workflow templates
  (their loader nodes carry ``properties.models[].url``) and ComfyUI-Manager's
  model registry, then topped up from setup/url_overrides.json for the files
  neither knows about.

One rule drives the node section: **do not trust /object_info's python_module.**
``comfyui-workflow-encrypt`` re-registers other packs' nodes and gets credited
with them, so a workflow needing ComfyUI-GGUF appears to need the encrypt shim
instead. The owner is found by scanning each pack's source for the class name.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import re
import sys
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.modes import MODE_ORDER, MODES  # noqa: E402

COMFY = Path(r"C:\AI ComfyUI - New Version\ComfyUI_windows_portable")
MODELS_ROOT = Path(r"C:\Models")
OUT = ROOT / "setup" / "catalog.json"
OVERRIDES = ROOT / "setup" / "url_overrides.json"
MIRRORS = ROOT / "setup" / "mirror_links.json"

#: Repositories that answer 401 without a token, confirmed by
#: tools/verify_urls.py rather than assumed. A file from one of these needs a
#: HuggingFace token *and* its licence accepted on the model's own page, so the
#: interface has to say so before the user starts a multi-hour download.
GATED_REPOS = (
    "huggingface.co/Lightricks/LTX-2.5/",
    # Civitai model 3147117 redirects to auth.civitai.com without a key, unlike
    # most Civitai files, which download anonymously.
    "civitai.com/api/download/models/3147117",
    # Flux 3 Klein: Black Forest Labs' own repository, behind their licence.
    "huggingface.co/black-forest-labs/flux-3-image-klein-comfy/",
)

#: The ComfyUI release EasyAI is tested with, and its exact commit. This is a
#: release, not whatever this machine happens to run: the machine that builds
#: the catalogue may be a few commits ahead (it was 19 past v0.42.0), and
#: pinning that would hand everyone a build nobody published.
COMFYUI_PIN = {
    "version": "0.42.1",
    "commit": "63be2f9b82ad0dac88a7ceaa28a9cf3154125a18",
}

#: The portable to install: the v0.42.1 release. Sizes and hashes are GitHub's
#: own (the release API's asset digests), so a truncated or substituted archive
#: is refused before seven minutes of unpacking. ComfyUI's checkout is then
#: pinned to the exact commit as well.
PORTABLE = {
    "release": "v0.42.1",
    "asset": "ComfyUI_windows_portable_nvidia.7z",
    "url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v0.42.1/"
           "ComfyUI_windows_portable_nvidia.7z",
    "bytes": 2018444114,
    "sha256": "496afd18790ec685d99ebba2edf200d7371a29fbdd3a992bb0f4f52f244a6874",
    "cuda": "13.0",
    "fallback": {
        "asset": "ComfyUI_windows_portable_nvidia_cu126.7z",
        "url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v0.42.1/"
               "ComfyUI_windows_portable_nvidia_cu126.7z",
        "bytes": 1910153206,
        "sha256": "0506abc6563ea5928a052fe0bc0b32c2a063098558bd593fe39345a27f1e47fc",
        "cuda": "12.6",
        "why": "for drivers too old for CUDA 13",
    },
}

MODEL_FIELDS = (
    "ckpt_name", "unet_name", "vae_name", "clip_name", "clip_name1", "clip_name2",
    "lora_name", "control_net_name", "model_name", "style_model_name", "gguf_name",
    "upscale_model_name", "text_encoder_name", "audio_encoder_name",
)

#: Which models folder each loader input feeds, so a downloaded file lands
#: where ComfyUI will look for it.
FIELD_DIR = {
    "ckpt_name": "checkpoints", "unet_name": "unet", "gguf_name": "unet",
    "vae_name": "vae", "clip_name": "clip", "clip_name1": "clip",
    "clip_name2": "clip", "text_encoder_name": "text_encoders",
    "lora_name": "loras", "control_net_name": "controlnet",
    "style_model_name": "style_models", "upscale_model_name": "upscale_models",
    "audio_encoder_name": "audio_encoders", "model_name": "checkpoints",
}


# --- reading this machine --------------------------------------------------
def _git_out(folder: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(folder), *args],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _pyproject(folder: Path) -> dict:
    """[project] name and version from a pack's pyproject.toml."""
    path = folder / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        import tomllib
        return (tomllib.loads(path.read_text(encoding="utf-8")).get("project") or {})
    except (ValueError, OSError):
        return {}


def live_snapshot() -> dict:
    """What this machine really runs, in ComfyUI-Manager's snapshot shape.

    Manager's own snapshots only appear while Manager is loaded, and on
    ComfyUI 0.37 it is not unless ComfyUI is started with --enable-manager. The
    newest one here dates from before the update to 0.37.0, so reading it
    paired "0.37.0" with 0.33.0's commit and add-on versions nobody had tested
    against it. The live install is the only record that cannot go stale.
    """
    comfy = COMFY / "ComfyUI"
    head = _git_out(comfy, "rev-parse", "HEAD")
    if not head:
        raise SystemExit(f"Could not read ComfyUI's commit from {comfy}")
    snapshot = {"comfyui": head, "git_custom_nodes": {}, "cnr_custom_nodes": {}}

    for folder in sorted((comfy / "custom_nodes").iterdir()):
        if not folder.is_dir() or folder.name.startswith(".") or folder.name.endswith(".disabled"):
            continue
        if (folder / ".git").exists():
            url = _git_out(folder, "remote", "get-url", "origin")
            commit = _git_out(folder, "rev-parse", "HEAD")
            if url and commit:
                snapshot["git_custom_nodes"][url] = {"hash": commit, "disabled": False}
            continue
        project = _pyproject(folder)
        if project.get("version"):
            version = str(project["version"])
            snapshot["cnr_custom_nodes"][folder.name] = version
            name = str(project.get("name") or "")
            if name and name.lower() != folder.name.lower():
                snapshot["cnr_custom_nodes"][name] = version
    print(f"live install : ComfyUI {head[:10]}, "
          f"{len(snapshot['git_custom_nodes'])} git + "
          f"{len(snapshot['cnr_custom_nodes'])} registry packs")
    return snapshot


def newest_snapshot() -> dict:
    snaps = sorted((COMFY / "ComfyUI/user/__manager/snapshots").glob("*.json"))
    if not snaps:
        raise SystemExit("No ComfyUI-Manager snapshot found - open Manager once to create one.")
    print(f"snapshot     : {snaps[-1].name}")
    return json.loads(snaps[-1].read_text(encoding="utf-8"))


def api_graphs() -> dict[str, list[tuple[Path, dict]]]:
    """EasyAI's API-format workflows, grouped by mode."""
    out: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    for mode in MODE_ORDER:
        for path in sorted((ROOT / "workflows" / mode).glob("*.json")):
            if path.name.endswith(".manifest.json"):
                continue
            try:
                graph = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(graph, dict) and graph and all(
                    isinstance(v, dict) and "class_type" in v for v in graph.values()):
                out[mode].append((path, graph))
    return out


def optional_inputs(path: Path) -> set[tuple[str, str]]:
    """(node, input) pairs a workflow's manifest marks as the user's choice.

    An optional LoRA names whatever file its author parked there - a personal
    LoRA nobody else has. EasyAI's "None" takes that loader out of the run, so
    the file is not part of what Setup installs.
    """
    manifest = path.with_name(path.stem + ".manifest.json")
    try:
        bindings = json.loads(manifest.read_text(encoding="utf-8")).get("bindings") or {}
    except (OSError, json.JSONDecodeError):
        return set()
    found = set()
    raw = bindings.get("lora")
    for entry in raw if isinstance(raw, list) else [raw]:
        if isinstance(entry, dict) and entry.get("node"):
            found.add((str(entry["node"]), str(entry.get("input") or "lora_name")))
    return found


def core_classes() -> set[str]:
    """Class names ComfyUI provides itself.

    Taken from the running server, where ``comfy_extras.*`` and ``nodes`` are
    trustworthy even though the custom_nodes attributions are not.
    """
    from app.comfy.client import ComfyClient
    from app.config import Config

    try:
        info = ComfyClient(Config().server).object_info()
    except Exception as e:
        raise SystemExit(f"ComfyUI must be running to build the catalogue: {e}")
    return {name for name, spec in info.items()
            if str(spec.get("python_module", "")).startswith(("comfy_extras", "nodes"))}


def owner_of(class_names: set[str]) -> dict[str, str]:
    """class name -> the custom node folder whose source declares it."""
    owners: dict[str, str] = {}
    # ComfyUI skips a folder named *.disabled, so it cannot own anything - and
    # one kept as a backup, such as a fork parked beside the official pack,
    # declares exactly the same classes.
    for folder in sorted(p for p in (COMFY / "ComfyUI/custom_nodes").iterdir()
                         if p.is_dir() and not p.name.startswith((".", "__"))
                         and not p.name.endswith(".disabled")):
        blob = ""
        for py in folder.rglob("*.py"):
            try:
                blob += py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
        for name in class_names:
            if name not in owners and re.search(r'["\']' + re.escape(name) + r'["\']\s*:', blob):
                owners[name] = folder.name
    return owners


# --- download URLs ---------------------------------------------------------
def _template_nodes(raw: dict):
    """Every node in a saved workflow, including those inside subgraphs.

    Newer ComfyUI templates wrap their loaders in a subgraph, so the model
    links sit under ``definitions.subgraphs[].nodes`` rather than the
    top-level ``nodes``. Reading only the top level missed 635 links across
    166 bundled templates - Qwen Image 2.1's three models among them.
    """
    stack = [raw]
    while stack:
        graph = stack.pop()
        for node in graph.get("nodes") or []:
            if isinstance(node, dict):
                yield node
        for sub in (graph.get("definitions") or {}).get("subgraphs") or []:
            if isinstance(sub, dict):
                stack.append(sub)


#: Where an official copy of a model lives. Reading every template means the
#: same filename turns up in several - flux2-vae.safetensors is also in an
#: unrelated 3D project's repository - and the first one found used to win.
_OFFICIAL = ("huggingface.co/Comfy-Org/",)


def _offer(found: dict[str, str], name: str, url: str) -> None:
    """Record a link, letting an official one replace an unofficial one."""
    current = found.get(name)
    if current is None:
        found[name] = url
    elif (not any(o in current for o in _OFFICIAL)
          and any(o in url for o in _OFFICIAL)):
        found[name] = url


def previous_links() -> dict[str, str]:
    """Links already in the catalogue, which were checked when they went in.

    A regenerate must not swap a verified link for whichever template happens
    to be read first. Only setup/url_overrides.json can replace one.
    """
    if not OUT.is_file():
        return {}
    try:
        models = json.loads(OUT.read_text(encoding="utf-8")).get("models", {})
    except (json.JSONDecodeError, OSError):
        return {}
    return {name: e["url"] for name, e in models.items() if e.get("url")}


def previous_entries() -> dict[str, dict]:
    """The catalogue's current model entries, for what this machine cannot see.

    A model that has been moved off this machine still has a size and a folder
    worth keeping - forgetting them would make Setup promise a 0-byte download.
    """
    if not OUT.is_file():
        return {}
    try:
        return json.loads(OUT.read_text(encoding="utf-8")).get("models", {})
    except (json.JSONDecodeError, OSError):
        return {}


def override_links() -> dict[str, str]:
    if not OVERRIDES.is_file():
        return {}
    return {Path(k).name.lower(): v
            for k, v in json.loads(OVERRIDES.read_text(encoding="utf-8")).items() if v}


def harvest_urls() -> dict[str, str]:
    """filename -> url, from ComfyUI's own templates and Manager's registry."""
    found: dict[str, str] = {}
    roots = [
        COMFY / "python_embeded/Lib/site-packages/comfyui_workflow_templates_json/templates",
        COMFY / "ComfyUI/user/default/workflows",
        Path(r"E:\ComfyUI Workflow"),
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            for node in _template_nodes(raw):
                for m in (node.get("properties") or {}).get("models") or []:
                    if isinstance(m, dict) and m.get("url") and m.get("name"):
                        _offer(found, Path(m["name"]).name.lower(), m["url"])

    registry = COMFY / "ComfyUI/user/__manager/cache/4245046894_model-list.json"
    if registry.is_file():
        try:
            for m in json.loads(registry.read_text(encoding="utf-8")).get("models", []):
                if m.get("url") and m.get("filename"):
                    _offer(found, Path(m["filename"]).name.lower(), m["url"])
        except (json.JSONDecodeError, OSError):
            pass

    if OVERRIDES.is_file():
        for name, url in json.loads(OVERRIDES.read_text(encoding="utf-8")).items():
            if url:
                found[Path(name).name.lower()] = url
    return found


def mirror_links() -> dict[str, str]:
    """filename -> our own copy, for the models that sit behind a sign-in."""
    if not MIRRORS.is_file():
        return {}
    raw = json.loads(MIRRORS.read_text(encoding="utf-8"))
    return {Path(k).name.lower(): v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, str)}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def registry_download_url(node_id: str, version: str) -> str | None:
    """Ask the ComfyUI registry where a pinned node version lives.

    The zip sits on a CDN under a path that includes the publisher, which is
    not derivable from the node id, so it has to be looked up. Baking the
    answer into the catalogue means the installer needs one fewer live service
    to be up - it falls back to this same call only if the recorded link dies.
    """
    try:
        r = requests.get(f"https://api.comfy.org/nodes/{node_id}/install",
                         params={"version": version}, timeout=30)
        if r.status_code == 200:
            return r.json().get("downloadUrl")
        print(f"  !! registry has no {node_id} {version} (HTTP {r.status_code})")
    except (requests.RequestException, ValueError) as e:
        print(f"  !! could not resolve {node_id} {version}: {e}")
    return None


def apply_mirrors(models: dict) -> tuple[list[str], list[str]]:
    """Fold mirror_links.json into model entries, hashing what we mirror.

    Returns (mirrored, still needing a key).
    """
    mirrors = mirror_links()
    mirrored, unmirrored = [], []
    for name, entry in models.items():
        mirror = mirrors.get(Path(name).name.lower())
        if mirror:
            entry["mirror"] = mirror
            mirrored.append(name)
            # Our own server is the weakest link in the chain, so mirrored
            # files carry a hash. Everything else comes from a CDN where the
            # byte size has proved sufficient.
            path = local_model(name)
            if path and not entry.get("sha256"):
                print(f"  hashing {Path(name).name} ...", end="", flush=True)
                entry["sha256"] = file_sha256(path)
                print(" done")
        else:
            entry.pop("mirror", None)
            entry.pop("sha256", None)
            if entry.get("gated"):
                unmirrored.append(name)
    return mirrored, unmirrored


def update_mirrors_only() -> int:
    """Refresh everything this machine alone can settle: folders, sizes,
    hashes and mirror links.

    None of it needs ComfyUI running - only the node-ownership scan does - so
    re-uploading a file or correcting a folder stays a quick operation.
    """
    if not OUT.is_file():
        print(f"No {OUT.name} yet - run without --mirrors-only first.")
        return 1
    catalog = json.loads(OUT.read_text(encoding="utf-8"))

    moved = []
    for name, entry in catalog["models"].items():
        path = local_model(name)
        if not path:
            continue
        entry["bytes"] = path.stat().st_size
        was = entry.get("dir", "")
        now = model_dir(name, path, "")
        if now and now != was:
            entry["dir"] = now
            moved.append((name, was, now))

    mirrored, unmirrored = apply_mirrors(catalog["models"])
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")

    size = sum(catalog["models"][n].get("bytes", 0) for n in mirrored)
    print(f"\nupdated {OUT.relative_to(ROOT)}")
    if moved:
        print(f"  {len(moved)} models moved to the folder ComfyUI actually reads:")
        for name, was, now in moved:
            print(f"       {Path(name).name[:50]:<52} {was}  ->  {now}")
    print(f"  {len(mirrored)} models mirrored ({size / 1024**3:.1f} GB) "
          f"- these need no account")
    if unmirrored:
        print(f"  !! {len(unmirrored)} still need an account key:")
        for n in unmirrored:
            print(f"       {n}")
    else:
        print("  no model needs an account key")
    print(f"\nNow check them:  python tools/verify_urls.py --mirror")
    return 0


def local_model(name: str) -> Path | None:
    """Find a referenced model on this machine, to read its size and folder.

    The workflow writes a name like "Flux Krea 2\\qwen_image_vae.safetensors",
    which is a subfolder plus a filename. Several such subfolders can hold a
    file of the same name - qwen_image_vae.safetensors exists under both Anima
    and Flux Krea 2 - so a match on the filename alone picks whichever the
    walk reached first and silently attributes the wrong copy.
    """
    wanted = Path(name)
    direct = MODELS_ROOT / name
    if direct.is_file():
        return direct

    matches = list(MODELS_ROOT.rglob(wanted.name))
    if not matches:
        return None
    tail = wanted.parts
    exact = [m for m in matches if m.parts[-len(tail):] == tail]
    return exact[0] if exact else matches[0]


def model_dir(name: str, path: Path | None, field: str) -> str:
    """Which folder under models/ this file belongs in.

    Taken from where it actually sits on a working machine, because the input
    field name cannot tell you: UNETLoader reads models/diffusion_models while
    UnetLoaderGGUF reads models/unet, and both fill in a field called
    "unet_name". The same goes for CLIPLoader, which may read clip or
    text_encoders. Guessing from the field puts files where ComfyUI will never
    look for them.

    Falls back to the field-name guess only when the file is not installed
    here, so the catalogue can still be built on a partial machine.
    """
    if path:
        try:
            relative = path.relative_to(MODELS_ROOT)
        except ValueError:
            relative = None
        if relative is not None:
            trailing = len(Path(name).parts)
            folder = relative.parts[:-trailing]
            if folder:
                return "/".join(folder)
    return FIELD_DIR.get(field, "checkpoints")


# --- building --------------------------------------------------------------
def main() -> int:
    if "--mirrors-only" in sys.argv:
        return update_mirrors_only()

    snapshot = live_snapshot()
    graphs = api_graphs()
    core = core_classes()
    kept = previous_links()
    before = previous_entries()
    overrides = override_links()
    urls = harvest_urls()
    print(f"url sources  : {len(urls)} filenames")

    models: dict[str, dict] = {}
    nodes: dict[str, dict] = {}
    groups: dict[str, dict] = {}
    needed_classes: set[str] = set()

    # Pass one: what each group refers to.
    per_group_models: dict[str, list[str]] = {}
    per_group_classes: dict[str, set[str]] = {}
    for mode in MODE_ORDER:
        names, classes = [], set()
        for path, graph in graphs.get(mode, []):
            skip = optional_inputs(path)
            for node_id, node in graph.items():
                classes.add(node["class_type"])
                for field, value in (node.get("inputs") or {}).items():
                    if (str(node_id), field) in skip:
                        continue
                    if field in MODEL_FIELDS and isinstance(value, str) and value.strip():
                        if value not in names:
                            names.append(value)
                        # The field is only a fallback; the folder is settled
                        # below from where the file really sits.
                        models.setdefault(value, {"field": field})
        per_group_models[mode] = names
        per_group_classes[mode] = classes
        needed_classes |= classes - core

    owners = owner_of(needed_classes)
    unknown = sorted(needed_classes - set(owners))

    # Pass two: node entries, pinned from the snapshot.
    git_by_folder = {u.rstrip("/").split("/")[-1].lower(): (u, v["hash"])
                     for u, v in snapshot.get("git_custom_nodes", {}).items()}
    cnr_by_id = {k.lower(): v for k, v in snapshot.get("cnr_custom_nodes", {}).items()}

    for folder in sorted(set(owners.values())):
        low = folder.lower()
        # Checked out from git on this machine wins over a registry entry of
        # the same name - ComfyUI-GGUF has been both.
        if low in git_by_folder:
            url, commit = git_by_folder[low]
            nodes[folder] = {"source": "git", "url": url, "commit": commit}
        elif low in cnr_by_id:
            version = cnr_by_id[low]
            nodes[folder] = {"source": "cnr", "id": folder, "version": version,
                             "url": registry_download_url(folder, version)}
        else:
            nodes[folder] = {"source": "unknown"}

    # Pass three: model details.
    missing_url, missing_file, guessed = [], [], []
    for name, entry in models.items():
        path = local_model(name)
        entry["dir"] = model_dir(name, path, entry.pop("field", ""))
        if path:
            entry["bytes"] = path.stat().st_size
        elif before.get(name, {}).get("bytes"):
            entry["dir"] = before[name].get("dir") or entry["dir"]
            entry["bytes"] = before[name]["bytes"]
        else:
            missing_file.append(name)
            guessed.append(name)
        key = Path(name).name.lower()
        url = overrides.get(key) or kept.get(name) or urls.get(key)
        if url:
            entry["url"] = url
            if any(repo in url for repo in GATED_REPOS):
                entry["gated"] = True
        else:
            missing_url.append(name)
            entry["url"] = None

    mirrored, unmirrored = apply_mirrors(models)

    for mode in MODE_ORDER:
        packs = sorted({owners[c] for c in per_group_classes[mode] - core if c in owners})
        size = sum(models[n].get("bytes", 0) for n in per_group_models[mode])
        groups[mode] = {
            "label": MODES[mode].label,
            "workflows": [p.name for p, _ in graphs.get(mode, [])],
            "nodes": packs,
            "models": per_group_models[mode],
            "bytes": size,
        }
        print(f"  {mode:<16} {len(graphs.get(mode, [])):>2} workflows  "
              f"{len(per_group_models[mode]):>2} models  {size / 1024**3:>6.1f} GB  "
              f"{len(packs)} packs")

    catalog = {
        "generated": "tools/make_catalog.py",
        "comfyui": {
            "version": COMFYUI_PIN["version"],
            "commit": COMFYUI_PIN["commit"],
            "portable": PORTABLE,
        },
        "groups": groups,
        "nodes": nodes,
        "models": models,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nwrote {OUT.relative_to(ROOT)}")
    print(f"  ComfyUI {catalog['comfyui']['version']} @ {catalog['comfyui']['commit'][:10]}")
    print(f"  {len(nodes)} node packs, {len(models)} models, "
          f"{sum(m.get('bytes', 0) for m in models.values()) / 1024**3:.1f} GB total")
    if unknown:
        print(f"  !! {len(unknown)} classes with no owner: {unknown}")
    if missing_file:
        print(f"  !! {len(missing_file)} models not found on this machine, so their "
              f"folder is a guess from the input field: {missing_file}")
    if missing_url:
        print(f"  !! {len(missing_url)} models still need a URL "
              f"- add them to {OVERRIDES.name}:")
        for n in missing_url:
            print(f"       {n}")
    if mirrored:
        size = sum(models[n].get("bytes", 0) for n in mirrored)
        print(f"  {len(mirrored)} models mirrored ({size / 1024**3:.1f} GB) "
              f"- these need no account")
    if unmirrored:
        print(f"  !! {len(unmirrored)} models still need an account key "
              f"- add a link to {MIRRORS.name} to remove that:")
        for n in unmirrored:
            print(f"       {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
