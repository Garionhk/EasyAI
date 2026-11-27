"""The install itself, as a sequence of resumable steps.

Every step checks whether its work is already done before doing it, so an
install interrupted at 80% carries on from 80% rather than starting again. The
state file written at the end records what succeeded, which is what makes a
second run cheap.

Everything is written inside the destination folder the user chose, with one
deliberate exception: the last step writes EasyAI's settings.json so the viewer
does not finish a multi-hour install and then have to type a path in by hand.
That file is merged, never replaced.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.i18n import t
from setup import existing
from setup.catalog import Catalog, Model, NodePack, human_bytes, token_host_for
from setup.download import Cancelled, DownloadError, download, free_space

from app.paths import resource_dir

#: The workflows and licences copied into a new install ship with the program.
ROOT = resource_dir()
STATE_FILE = "easyai-setup.json"

#: Where the extracted portable puts things.
COMFY_SUB = "ComfyUI_windows_portable"

_7ZIP_CANDIDATES = (
    Path(r"C:\Program Files\7-Zip\7z.exe"),
    Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
)

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

#: Moves a checkout to one exact commit. Run with the portable's own Python,
#: which carries pygit2 for ComfyUI's updater - so it works on a viewer's
#: machine with no git installed.
#:
#: It differs from ComfyUI's own updater in the two ways that matter here: it
#: goes to the pinned commit, never to "latest", and it checks out *safely*.
#: The official updater forces its way over local changes, and a forced
#: checkout can swap a models folder that the user has linked elsewhere for a
#: real, empty one - every model then looks missing. A safe checkout touches
#: only the files that differ between the two versions, and refuses rather
#: than overwrite anything the user changed. The current state is kept as a
#: branch first, so the move can always be undone.
_SWITCH = r"""
import datetime, sys
import pygit2
repo_path, commit, tag = sys.argv[1], sys.argv[2], sys.argv[3]
pygit2.option(pygit2.GIT_OPT_SET_OWNER_VALIDATION, 0)
repo = pygit2.Repository(repo_path)

def have(oid):
    try:
        return repo.get(oid) is not None
    except (KeyError, ValueError, pygit2.GitError):
        return False

try:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    repo.branches.local.create("easyai-before-" + stamp, repo.head.peel(pygit2.Commit))
except Exception:
    pass

if not have(commit):
    origin = repo.remotes["origin"]
    if tag:
        try:
            origin.fetch(["+refs/tags/%s:refs/tags/%s" % (tag, tag)])
        except pygit2.GitError:
            pass
    if not have(commit):
        origin.fetch()
if not have(commit):
    print("EASYAI-FAIL the pinned commit could not be fetched")
    sys.exit(2)

target = repo.get(commit)
try:
    repo.checkout_tree(target, strategy=pygit2.GIT_CHECKOUT_SAFE)
except pygit2.GitError as e:
    print("EASYAI-FAIL " + str(e))
    sys.exit(3)
repo.set_head(target.id)
print("EASYAI-OK " + str(repo.head.target))
"""


@dataclass
class Report:
    """What an install did, for the log and the state file."""
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class Installer:
    """Runs the steps for one selection of groups."""

    def __init__(self, catalog: Catalog, target: Path, groups: list[str],
                 hf_token: str = "", civitai_token: str = "",
                 on_step: Callable[[str], None] | None = None,
                 on_progress: Callable | None = None,
                 should_stop: Callable[[], bool] | None = None,
                 update_existing: bool = False, download_models: bool = True):
        self.catalog = catalog
        self.target = Path(target)
        self.groups = groups
        #: Move a ComfyUI that is already here, and EasyAI's add-ons in it, to
        #: the versions the catalogue pins. Off unless asked for: someone's
        #: working ComfyUI is theirs, and changing its version is their call.
        self.update_existing = update_existing
        #: Fetch the models ComfyUI cannot already see. Off means ComfyUI and
        #: the add-ons only.
        self.download_models = download_models
        #: Set when this run unpacked a new ComfyUI - that one is ours to pin.
        self._fresh = False
        #: Kept in memory for the run only - never written to disk, because a
        #: token in a config file outlives the reason it was needed.
        self.tokens = {"HuggingFace": hf_token, "Civitai": civitai_token}
        self._say = on_step or (lambda m: None)
        self._progress = on_progress
        self._stop = should_stop or (lambda: False)
        self.report = Report()

    # -- paths -------------------------------------------------------------
    @property
    def portable(self) -> Path:
        return self.target / COMFY_SUB

    @property
    def comfy(self) -> Path:
        return self.portable / "ComfyUI"

    @property
    def python(self) -> Path:
        return self.portable / "python_embeded" / "python.exe"

    @property
    def models_dir(self) -> Path:
        return self.comfy / "models"

    # -- the run -----------------------------------------------------------
    def run(self) -> Report:
        for step in (self.check_space, self.install_comfyui, self.pin_comfyui_commit,
                     self.install_nodes, self.install_models, self.copy_workflows,
                     self.configure_easyai):
            if self._stop():
                self.report.failed.append("cancelled")
                return self.report
            try:
                step()
            except Cancelled:
                self.report.failed.append("cancelled")
                return self.report
            except (DownloadError, OSError, subprocess.SubprocessError) as e:
                self.report.failed.append(f"{step.__name__}: {e}")
                self._say(t("FAILED  {problem}", problem=e))
                return self.report
        self.write_state()
        return self.report

    # -- 1. room to work ---------------------------------------------------
    def check_space(self) -> None:
        needed = existing.survey(self.catalog, self.target, self.groups) \
            .download_bytes(self.download_models)
        # The portable expands to about three times its archive, and models are
        # written once, so headroom of the download plus a margin is enough.
        needed = int(needed * 1.15)
        free = free_space(self.target)
        self._say(t("Space needed about {needed}, free {free}",
                    needed=human_bytes(needed), free=human_bytes(free)))
        if free and free < needed:
            raise OSError(t(
                "Not enough room on that drive.\n\n"
                "This selection needs about {needed} and {free} is free. Pick "
                "another folder, or untick a group.",
                needed=human_bytes(needed), free=human_bytes(free)))

    # -- 2. ComfyUI portable ----------------------------------------------
    def install_comfyui(self) -> None:
        if (self.python).is_file():
            self._say(t("ComfyUI is already here - not downloading it again"))
            self.report.skipped.append("comfyui")
            return

        spec = self.catalog.comfyui["portable"]
        archive = self.target / spec["asset"]
        self._say(t("Downloading ComfyUI {version} ({size})",
                    version=self.catalog.comfyui["version"],
                    size=human_bytes(spec["bytes"])))
        download(spec["url"], archive, expected_bytes=spec.get("bytes", 0),
                 sha256=spec.get("sha256", ""),
                 on_progress=self._progress, should_stop=self._stop)

        self._say(t("Unpacking ComfyUI - this takes a few minutes"))
        extract_7z(archive, self.target)
        if not self.python.is_file():
            raise OSError(t(
                "Unpacked, but {path} is missing - the archive may not be the "
                "Windows portable build.", path=self.python))
        archive.unlink(missing_ok=True)
        self._fresh = True
        self.report.done.append("comfyui")

    # -- 3. the pinned ComfyUI ---------------------------------------------
    def pin_comfyui_commit(self) -> None:
        """Put ComfyUI on the exact commit the catalogue was built from.

        A ComfyUI this run unpacked is always pinned - it is ours. One that was
        already here is moved only if the user asked, and then its Python
        packages are brought up to match, as ComfyUI's own updater does: new
        code on the old front-end package does not start properly.
        """
        commit = str(self.catalog.comfyui.get("commit") or "")
        version = str(self.catalog.comfyui.get("version") or "")
        if not commit:
            self.report.skipped.append("pin")
            return
        current = existing.git_head(self.comfy) or ""
        if current.startswith(commit[:12]):
            self._say(t("ComfyUI is already {version}", version=version))
            self.report.skipped.append("pin")
            return
        if not self._fresh and not self.update_existing:
            self._say(t(
                "ComfyUI {installed} is here - leaving it as it is. EasyAI is "
                "tested with {version}.",
                installed=existing.comfyui_version(self.comfy) or "?", version=version))
            self.report.skipped.append("pin")
            return
        if not (self.comfy / ".git").exists():
            raise OSError(t(
                "This ComfyUI was not installed from a git checkout, so it cannot "
                "be moved to {version}. Install into a new folder instead.",
                version=version))

        self._say(t("Moving ComfyUI to {version}", version=version))
        self._switch_repo(self.comfy, commit, tag=str(
            (self.catalog.comfyui.get("portable") or {}).get("release") or ""))
        self._pip_requirements(self.comfy, remember=self.portable / "update"
                               / "current_requirements.txt")
        self.report.done.append("pin")

    def _switch_repo(self, repo: Path, commit: str, tag: str = "") -> None:
        """Move one checkout to one commit, safely. See _SWITCH."""
        if self.python.is_file():
            result = subprocess.run(
                [str(self.python), "-s", "-c", _SWITCH, str(repo), commit, tag],
                capture_output=True, text=True, timeout=1800,
                creationflags=_NO_WINDOW)
            out = result.stdout or ""
            if "EASYAI-OK" in out:
                return
            failure = next((line[len("EASYAI-FAIL"):].strip()
                            for line in out.splitlines() if line.startswith("EASYAI-FAIL")), "")
            if failure:
                raise OSError(t(
                    "Could not move {folder} to the tested version: {problem}\n\n"
                    "If you changed files in it yourself, undo those changes and "
                    "run Setup again.", folder=repo.name, problem=failure))
            # No pygit2 in this Python: fall through to git itself.
        if tag:
            self._git(repo, "fetch", "origin", f"refs/tags/{tag}:refs/tags/{tag}", check=False)
        self._git(repo, "fetch", "origin", check=False)
        self._git(repo, "checkout", "-q", commit)

    # -- 4. custom nodes ---------------------------------------------------
    def install_nodes(self) -> None:
        packs = self.catalog.nodes_for(self.groups)
        if not packs:
            self._say(t("No custom nodes needed for this selection"))
            return
        folder = self.comfy / "custom_nodes"
        folder.mkdir(parents=True, exist_ok=True)

        for pack in packs:
            if self._stop():
                raise Cancelled("nodes")
            destination = folder / pack.name
            if destination.is_dir():
                state = existing.pack_state(destination)
                if existing.pack_matches(pack, state) or not self.update_existing:
                    self._say("  " + t("{name} already installed", name=pack.name))
                    self.report.skipped.append(pack.name)
                    continue
                self._say("  " + t("updating {name} to the tested version", name=pack.name))
                try:
                    self._update_pack(pack, destination, state)
                    self._pip_requirements(destination)
                    self.report.done.append(pack.name)
                except (subprocess.SubprocessError, OSError, DownloadError) as e:
                    self._say("  !! " + t("{name} failed: {problem}",
                                          name=pack.name, problem=e))
                    self.report.failed.append(f"{pack.name}: {e}")
                continue
            self._say("  " + t("installing {name}", name=pack.name))
            try:
                self._install_pack(pack, destination)
                self._pip_requirements(destination)
                self.report.done.append(pack.name)
            except (subprocess.SubprocessError, OSError, DownloadError) as e:
                # One awkward node pack should not sink the whole install.
                self._say("  !! " + t("{name} failed: {problem}",
                                      name=pack.name, problem=e))
                self.report.failed.append(f"{pack.name}: {e}")

    def _update_pack(self, pack: NodePack, destination: Path,
                     state: existing.PackState) -> None:
        """Bring an installed add-on to its pinned version.

        A git checkout of the same pack is moved in place. Anything else - a
        registry pack at another version, or one that has switched between
        registry and git - is replaced, with the old copy put back if the new
        one cannot be installed.
        """
        if pack.source == "git" and state.source == "git" and pack.commit:
            try:
                self._switch_repo(destination, pack.commit)
                return
            except (OSError, subprocess.SubprocessError):
                pass            # a fork, or history it cannot reach: replace it

        stamp = time.strftime("%Y%m%d-%H%M%S")
        # ".disabled" so ComfyUI skips it while it sits beside the new copy.
        aside = destination.with_name(f"{destination.name}.easyai-{stamp}.disabled")
        destination.rename(aside)
        try:
            self._install_pack(pack, destination)
        except BaseException:
            remove_tree(destination)
            aside.rename(destination)
            raise
        remove_tree(aside)

    def _install_pack(self, pack: NodePack, destination: Path) -> None:
        if pack.source == "git" and pack.url:
            self._clone_at_commit(pack, destination)
        elif pack.source == "cnr" and pack.id:
            url = pack.url or registry_url(pack.id, pack.version)
            if not url:
                raise OSError(f"the registry has no {pack.name} {pack.version}")
            archive = destination.parent / f"{pack.name}-{Path(url).name}"
            try:
                download(url, archive, on_progress=self._progress, should_stop=self._stop)
            except DownloadError:
                # The recorded CDN link has moved; ask the registry again.
                fresh = registry_url(pack.id, pack.version)
                if not fresh or fresh == url:
                    raise
                download(fresh, archive, on_progress=self._progress,
                         should_stop=self._stop)
            # These archives hold the pack's files at the root, so the folder
            # unpacked into is what becomes the custom_nodes entry.
            unpack(archive, destination)
            archive.unlink(missing_ok=True)
        else:
            raise OSError(f"no source recorded for {pack.name}")

    def _clone_at_commit(self, pack: NodePack, destination: Path) -> None:
        """Fetch just the one commit this pack is pinned to.

        A shallow fetch of the exact SHA takes a fraction of a full clone -
        ComfyUI_LayerStyle alone is 517 files with years of history behind it -
        and it cannot land on the wrong revision the way cloning a branch and
        hoping the commit is in it can. If the host refuses SHA fetches, fall
        back to a full clone.
        """
        # Twice, because a dropped connection mid-fetch is common and a second
        # try usually just works - before this, one hiccup left the add-on
        # uninstalled with nothing but a misleading "already exists" to show.
        first_problem = ""
        for _attempt in range(2):
            remove_tree(destination)
            destination.mkdir(parents=True, exist_ok=True)
            try:
                self._git(destination, "init", "-q")
                self._git(destination, "remote", "add", "origin", pack.url)
                self._git(destination, "fetch", "--depth", "1", "origin", pack.commit)
                self._git(destination, "checkout", "-q", "FETCH_HEAD")
                return
            except subprocess.SubprocessError as e:
                first_problem = first_problem or str(e)

        # Some hosts will not hand out a commit by its hash; a full clone
        # reaches it through the branch history instead.
        remove_tree(destination)
        try:
            self._git(destination.parent, "clone", pack.url, pack.name)
            self._git(destination, "checkout", pack.commit)
        except subprocess.SubprocessError as e:
            raise subprocess.SubprocessError(f"{e} (first try: {first_problem})") from e

    def _pip_requirements(self, folder: Path, remember: Path | None = None) -> None:
        req = folder / "requirements.txt"
        if not req.is_file() or not self.python.is_file():
            return
        self._say("    " + t("installing its Python packages"))
        result = subprocess.run(
            [str(self.python), "-s", "-m", "pip", "install", "-r", str(req)],
            capture_output=True, timeout=1800, creationflags=_NO_WINDOW)
        if remember is not None and result.returncode == 0:
            # Where ComfyUI's own updater records what it last installed, so
            # it does not reinstall the same packages next time it runs.
            try:
                remember.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(req, remember)
            except OSError:
                pass

    # -- 5. models ---------------------------------------------------------
    def install_models(self) -> None:
        if not self.download_models:
            self._say(t("Not downloading any models, as chosen"))
            self.report.skipped.append("models")
            return

        models = self.catalog.models_for(self.groups)
        usable = [m for m in models if m.installable]
        blocked = [m for m in models if not m.installable]
        # Everywhere ComfyUI looks, not just where Setup would put a file: a
        # text encoder in models/clip is found by ComfyUI just as well as one
        # in models/text_encoders, and must not be downloaded again.
        search = existing.model_search_paths(self.portable)
        present = {m.name: existing.find_model(m, self.models_dir, search)
                   or self._already_at_destination(m) for m in models}
        todo = [m for m in usable if not present[m.name]]
        self._say(t("{n} models to fetch, {size}",
                    n=len(todo), size=human_bytes(sum(m.bytes for m in todo))))

        for i, model in enumerate(usable, 1):
            if self._stop():
                raise Cancelled("models")
            destination = self.catalog.model_destination(model, self.models_dir, self.target)
            found = present[model.name]
            if found and not (found == destination and model.bytes
                              and found.stat().st_size != model.bytes):
                self._say(f"  [{i}/{len(usable)}] "
                          + t("{name} already here", name=model.filename))
                self.report.skipped.append(model.filename)
                continue
            self._say(f"  [{i}/{len(usable)}] {model.filename}  "
                      + human_bytes(model.bytes))
            try:
                self._fetch_model(model, destination)
                self.report.done.append(model.filename)
            except DownloadError as e:
                # One model that cannot be fetched - a missing key, a dead
                # link - must not throw away the other hundred gigabytes that
                # downloaded perfectly. Record it and carry on; the summary at
                # the end says what is missing and why.
                self._say("  !! " + t("{name} could not be downloaded",
                                      name=model.filename))
                self.report.failed.append(
                    f"{model.filename}: {getattr(e, 'reason', '') or _first_line(e)}")

        for model in blocked:
            if present.get(model.name):
                continue
            self._say("  !! " + t("no download link for {name} - copy it in by hand",
                                  name=model.name))
            self.report.failed.append(f"no link: {model.name}")

        self.register_model_folders(models)
        self.write_licences()

    def _already_at_destination(self, model: Model) -> Path | None:
        """The file where the install list puts it, before ComfyUI is told to
        look there - so a re-run does not fetch it again."""
        destination = self.catalog.model_destination(model, self.models_dir, self.target)
        if destination.is_file() and (not model.bytes
                                      or destination.stat().st_size == model.bytes):
            return destination
        return None

    # -- models kept outside ComfyUI's own folder -------------------------------
    YAML_START = "# >>> EasyAI Setup - written from setup-settings.json; edit that file instead"
    YAML_END = "# <<< EasyAI Setup"

    def register_model_folders(self, models: list[Model]) -> None:
        """Tell ComfyUI about model folders outside its own models folder.

        setup-settings.json can send models anywhere - another drive, a shared
        folder - but ComfyUI only looks where it is told. Its own mechanism for
        that is extra_model_paths.yaml, so Setup keeps a clearly marked block
        in it and rewrites only that block: anything else in the file is the
        user's and is left exactly as it was.
        """
        extra: dict[str, list[str]] = {}
        for model in models:
            folder = self.catalog.model_folder(model, self.models_dir, self.target)
            if folder == self.models_dir / model.folder:
                continue                    # ComfyUI's own folder: nothing to add
            paths = extra.setdefault(model.folder, [])
            if folder.as_posix() not in paths:
                paths.append(folder.as_posix())

        yaml = self.comfy / "extra_model_paths.yaml"
        try:
            text = yaml.read_text(encoding="utf-8") if yaml.is_file() else ""
        except OSError:
            text = ""
        start, end = text.find(self.YAML_START), text.find(self.YAML_END)
        if start != -1 and end != -1:
            text = text[:start].rstrip("\n") + text[end + len(self.YAML_END):]
            text = text.strip("\n") + ("\n" if text.strip() else "")

        if extra:
            block = [self.YAML_START, "easyai_setup:"]
            for kind, paths in sorted(extra.items()):
                block.append(f"    {kind}: |")
                block += [f"        {path}" for path in paths]
            block.append(self.YAML_END)
            text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + "\n".join(block) + "\n"

        old = yaml.read_text(encoding="utf-8") if yaml.is_file() else ""
        if text != old and (text.strip() or yaml.is_file()):
            yaml.parent.mkdir(parents=True, exist_ok=True)
            yaml.write_text(text, encoding="utf-8")
            if extra:
                self._say(t("ComfyUI told to look for models in {n} more folder(s)",
                            n=sum(len(p) for p in extra.values())))

    def _fetch_model(self, model: Model, destination: Path) -> None:
        """Try each source in turn, keeping the first that works.

        The mirror is tried first because it needs no account. If it has been
        moved, revoked or the server is down, the original is still there - so
        the worst case is the old "you need a key" message, never a dead end.
        """
        problems = []
        for label, url in model.sources:
            if problems:
                # Never resume one source's part file from another. The two
                # copies are the same length, so a half-and-half file would
                # pass the size check and only reveal itself as a corrupt model
                # much later. Starting over costs a download; getting this
                # wrong costs a mystery.
                part = destination.with_suffix(destination.suffix + ".part")
                part.unlink(missing_ok=True)
            try:
                download(url, destination, expected_bytes=model.bytes,
                         sha256=model.sha256 if label == "mirror" else "",
                         # Chosen from the URL being called, so the mirror never
                         # receives a HuggingFace or Civitai credential.
                         token=self.tokens.get(token_host_for(url), ""),
                         on_progress=self._progress, should_stop=self._stop)
                return
            except Cancelled:
                raise
            except DownloadError as e:
                problems.append(f"{label}: {e}")
                if label != model.sources[-1][0]:
                    self._say("      " + t(
                        "the mirror failed, trying the original source"))

        error = DownloadError(
            t("Could not get {name} from any source.", name=model.filename)
            + "\n\n" + "\n\n".join(problems))
        # The summary has room for one line per model. "From any source" says
        # nothing the user can act on; the last source's own headline does -
        # "needs a HuggingFace account", "finished at the wrong size".
        error.reason = _first_line(problems[-1].split(": ", 1)[-1]) if problems else ""
        raise error

    def write_licences(self) -> None:
        """Save the licences that come with anything installed from the mirror.

        The LTX-2.x Community License permits redistribution on the condition
        that recipients are given a copy of the Agreement (Section 3.2), so
        writing these is part of being allowed to host the files at all - not
        a courtesy.
        """
        mirrored = self.catalog.mirrored(self.groups)
        if not mirrored:
            return
        source = ROOT / "setup" / "licences"
        if not source.is_dir():
            return
        folder = self.target / "LICENSES"
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.glob("*")):
            if path.is_file():
                shutil.copy2(path, folder / path.name)
        self._say(t("Licences for the mirrored models written to {folder}",
                    folder=folder.name))

    # -- 6. the workflows --------------------------------------------------
    def copy_workflows(self) -> None:
        """Put each chosen group's workflows where EasyAI will find them.

        Each comes from where the install list says - built into Setup, a file
        on this machine, or a web link - and goes into its folder under the
        workflows folder. Its setup file and thumbnail travel with it, so a
        corrected prompt binding or a picture on the card is not lost.
        """
        root = self.catalog.workflows_root(self.target)
        copied = 0
        for key in self.groups:
            for item in self.catalog.groups[key].workflow_items:
                to = Path(item.to or key)
                destination = to if to.is_absolute() else root / to
                try:
                    self._fetch_workflow(key, item, destination)
                    copied += 1
                except (OSError, DownloadError) as e:
                    self._say("  !! " + t("workflow {name} could not be copied: {problem}",
                                          name=item.file, problem=e))
                    self.report.failed.append(f"workflow {item.file}: {e}")
        self._say(t("{n} workflows copied to {folder}", n=copied, folder=root))
        self.report.done.append("workflows")

    def _workflow_source(self, key: str, item) -> Path | str:
        """A local file, or the web link to fetch it from."""
        source = (item.source or "built-in").strip()
        if source.lower() == "built-in":
            return ROOT / "workflows" / key / item.file
        if source.lower().startswith(("http://", "https://")):
            return source
        path = Path(source)
        if not path.is_absolute():
            base = self.catalog.list_path.parent if self.catalog.list_path else ROOT
            path = base / path
        # A folder means "the file of this name inside it".
        return path / item.file if path.is_dir() else path

    def _fetch_workflow(self, key: str, item, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / item.file
        source = self._workflow_source(key, item)
        if isinstance(source, str):
            download(source, target, on_progress=self._progress, should_stop=self._stop)
            return
        if not source.is_file():
            raise OSError(t("not found: {path}", path=source))
        shutil.copy2(source, target)
        stem = Path(item.file).stem
        for sidecar in [source.with_name(stem + ".manifest.json")] + [
                source.with_name(stem + ext) for ext in (".png", ".jpg", ".jpeg", ".webp")]:
            if sidecar.is_file():
                shutil.copy2(sidecar, destination / sidecar.name)

    # -- 7. point EasyAI at what we just installed -------------------------
    def configure_easyai(self) -> None:
        """Write EasyAI's settings so it opens ready to use.

        Without this the viewer finishes a multi-hour install and is then told
        to open Settings and paste in a path - the one step most likely to be
        got wrong, because EasyAI wants the portable folder itself rather than
        the folder holding it.

        This is the only thing EasyAI Setup writes outside its own target
        folder, and it is deliberate: settings.json sits beside the .exe files,
        which is where EasyAI reads it from. Existing settings are merged, not
        replaced, so nothing else the user has chosen is lost.
        """
        from app.comfy.launcher import ComfyLauncher
        from app.config import SETTINGS_PATH, Config

        if not self.python.is_file():
            self._say(t("Skipping EasyAI setup - ComfyUI is not installed here"))
            return

        cfg = Config(SETTINGS_PATH)
        was = str(cfg.get("comfyui_dir") or "")

        cfg.set("comfyui_dir", str(self.portable))
        launchers = ComfyLauncher(self.portable, "", None).find_launchers()
        if launchers:
            cfg.set("comfyui_launcher", launchers[0])

        # The workflows were copied in beside the install; point EasyAI at that
        # copy rather than leaving it looking at an empty folder.
        workflows = self.catalog.workflows_root(self.target)
        if any(workflows.glob("*/*.json")):
            cfg.set("workflow_dir", str(workflows))

        # The welcome message exists to make someone set the ComfyUI folder.
        # That is now done, so showing it would ask for work already finished.
        cfg.set("first_run_done", True)
        cfg.save()

        self._say(t("EasyAI is now set to use this ComfyUI"))
        self._say(f"    {SETTINGS_PATH}")
        if was and was != str(self.portable):
            self._say(t("    it previously pointed at {old}", old=was))
        self.report.done.append("easyai-settings")

    # -- state -------------------------------------------------------------
    def write_state(self) -> None:
        # What is really here now, which is not the catalogue's version when
        # an existing ComfyUI was left as it was.
        state = {
            "comfyui": existing.comfyui_version(self.comfy),
            "commit": existing.git_head(self.comfy),
            "tested_with": self.catalog.comfyui.get("version"),
            "groups": self.groups,
            "installed": self.report.done,
            "skipped": self.report.skipped,
            "failed": self.report.failed,
        }
        (self.target / STATE_FILE).write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        self._say(t("Finished. {done} installed, {skipped} already present, "
                    "{failed} need attention.",
                    done=len(self.report.done), skipped=len(self.report.skipped),
                    failed=len(self.report.failed)))

    # -- helpers -----------------------------------------------------------
    def _git(self, cwd: Path, *args: str, check: bool = True) -> str:
        # core.longpaths: git's own files sit deep inside .git, and an install
        # folder a few levels down already passes Windows' 260-character limit
        # there - the clone then fails with "Filename too long".
        result = subprocess.run(["git", "-c", "core.longpaths=true", *args],
                                cwd=str(cwd), capture_output=True,
                                text=True, timeout=1800, creationflags=_NO_WINDOW)
        if check and result.returncode != 0:
            raise subprocess.SubprocessError(
                (result.stderr or result.stdout or "git failed").strip()[:300])
        return result.stdout


def remove_tree(path: Path) -> None:
    """Delete a folder, including one git has written into.

    Git marks its object files read-only, and on Windows shutil.rmtree gives
    up on those - with ignore_errors it gives up silently, leaving a folder
    that a clone then refuses to use, or that blocks putting an add-on back.
    """
    path = Path(path)
    if not path.exists():
        return

    def unlock(func, name, *_):
        try:
            os.chmod(name, stat.S_IWRITE)
            func(name)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=unlock)
    else:                                            # pragma: no cover
        shutil.rmtree(path, onerror=unlock)


def _first_line(error: Exception | str) -> str:
    """The headline of a multi-paragraph download error, for the summary list."""
    lines = str(error).strip().splitlines()
    return lines[0] if lines else ""


def unpack(archive: Path, destination: Path) -> None:
    """Unpack a downloaded node pack, going by content rather than by name.

    The registry serves comfyui_essentials as "node.tar.gz" with a
    Content-Type of application/gzip, and the file is a plain zip. Anything
    that trusts the extension gets it wrong, so the magic bytes decide.
    """
    import tarfile
    import zipfile

    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            z.extractall(destination)
        return
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            t.extractall(destination, filter="data")
        return
    raise OSError(t("{name} is not an archive this can open", name=archive.name))


def registry_url(node_id: str, version: str) -> str | None:
    """Where the registry currently serves this exact node version."""
    import requests

    try:
        r = requests.get(f"https://api.comfy.org/nodes/{node_id}/install",
                         params={"version": version}, timeout=30)
        if r.status_code == 200:
            return r.json().get("downloadUrl")
    except (requests.RequestException, ValueError):
        pass
    return None


def find_7zip() -> Path | None:
    found = shutil.which("7z") or shutil.which("7za")
    if found:
        return Path(found)
    for candidate in _7ZIP_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def extract_7z(archive: Path, destination: Path) -> None:
    """Unpack a .7z, preferring installed 7-Zip and falling back to py7zr.

    7-Zip is several times faster on a 2 GB archive, but it is not on every
    machine, so py7zr is the guarantee.
    """
    exe = find_7zip()
    if exe:
        result = subprocess.run([str(exe), "x", str(archive), f"-o{destination}", "-y"],
                                capture_output=True, text=True, creationflags=_NO_WINDOW)
        if result.returncode == 0:
            return
    try:
        import py7zr
    except ImportError:
        raise OSError(t(
            "Cannot unpack the ComfyUI archive.\n\n"
            "Either install 7-Zip from 7-zip.org, or run:\n"
            "    pip install py7zr"))
    with py7zr.SevenZipFile(archive, "r") as z:
        z.extractall(path=str(destination))
