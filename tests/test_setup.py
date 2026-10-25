"""Checks for the installer - the catalogue, the downloader and the steps.

Run with:  python -m pytest tests/test_setup.py -q

Nothing here touches the network. The downloader is exercised against a fake
HTTP layer, because the behaviour that matters - resuming, refusing a
wrong-sized file, never renaming an unfinished one - is precisely what is
impossible to test against a real 27 GB download.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from setup import download as dl
from setup.catalog import Catalog, human_bytes
from setup.steps import Installer

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "setup" / "catalog.json"

pytestmark = pytest.mark.skipif(not CATALOG.is_file(),
                                reason="catalog.json not generated on this machine")


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog()


# --- the catalogue ---------------------------------------------------------
def test_every_group_resolves_to_real_entries(catalog):
    """A group naming a model or pack that is not defined would fail mid-install."""
    for key, group in catalog.groups.items():
        for name in group.models:
            assert name in catalog.models, f"{key} names unknown model {name}"
        for name in group.nodes:
            assert name in catalog.nodes, f"{key} names unknown pack {name}"


def test_group_sizes_match_their_models(catalog):
    for key, group in catalog.groups.items():
        expected = sum(catalog.models[n].bytes for n in group.models)
        assert group.bytes == expected, f"{key} size is stale - regenerate the catalogue"


def test_selection_deduplicates(catalog):
    """Two groups sharing a model must download it once, not twice."""
    everything = list(catalog.groups)
    names = [m.name for m in catalog.models_for(everything)]
    assert len(names) == len(set(names))


def test_download_total_includes_the_portable(catalog):
    portable = catalog.comfyui["portable"]["bytes"]
    models = sum(m.bytes for m in catalog.models_for(["image"]) if m.installable)
    assert catalog.download_bytes(["image"]) == models + portable
    assert catalog.download_bytes(["image"], include_base=False) == models


def test_nothing_selected_costs_only_the_portable(catalog):
    assert catalog.download_bytes([], include_base=False) == 0


def test_node_packs_all_have_a_source(catalog):
    for name, pack in catalog.nodes.items():
        assert pack.source in ("git", "cnr"), f"{name} has no way to be installed"
        if pack.source == "git":
            assert pack.url and pack.commit, f"{name} is not pinned"
        else:
            assert pack.id and pack.version, f"{name} is not pinned"


def test_gated_means_confirmed_not_merely_hosted(catalog):
    """Marking every HuggingFace file as gated would train users to ignore it."""
    everything = list(catalog.groups)
    hosted = [m for m in catalog.models_for(everything) if m.token_host == "HuggingFace"]
    flagged = [m for m in catalog.models_for(everything) if m.gated]
    assert flagged, "expected the LTX 2.5 files to carry the gated flag"
    assert len(flagged) < len(hosted), "gating should be the exception, not the rule"


def test_catalog_gated_counts_only_what_still_needs_a_key(catalog):
    """The flag records where a file came from; the method records what the
    user must still go and do. Mirroring changes the second, never the first."""
    everything = list(catalog.groups)
    flagged = {m.name for m in catalog.models_for(everything) if m.gated}
    asking = {m.name for m in catalog.gated(everything)}
    mirrored = {m.name for m in catalog.mirrored(everything)}
    assert asking == flagged - mirrored


def test_blocked_models_are_reported_not_silently_dropped(catalog):
    everything = list(catalog.groups)
    blocked = catalog.blocked(everything)
    for model in blocked:
        assert not model.url
        assert model.bytes > 0, f"{model.name} has no size either - regenerate"


def test_model_folders_are_ones_comfyui_reads(catalog):
    """A model in the wrong folder downloads perfectly and is never found.

    The folder cannot be guessed from the workflow's input field: UNETLoader
    reads models/diffusion_models while UnetLoaderGGUF reads models/unet, and
    both call the field "unet_name". These are the folder names ComfyUI
    actually registers.
    """
    known = {
        "checkpoints", "clip", "clip_vision", "controlnet", "diffusion_models",
        "embeddings", "gligen", "hypernetworks", "latent_upscale_models",
        "loras", "model_patches", "audio_encoders", "photomaker",
        "style_models", "text_encoders", "unet", "upscale_models", "vae",
        "vae_approx",
    }
    for model in catalog.models.values():
        top = model.folder.replace("\\", "/").split("/")[0]
        assert top in known, f"{model.filename} would go to models/{model.folder}"


def test_the_big_diffusion_models_are_not_filed_as_unet(catalog):
    """The specific mistake that shipped: guessed from the field, not observed."""
    for filename in ("ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
                     "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
                     "z_image_turbo_bf16.safetensors",
                     "krea2_turbo_int8_convrot.safetensors"):
        model = next(m for m in catalog.models.values() if m.filename == filename)
        assert model.folder != "unet", f"{filename} back in models/unet"
        assert model.folder != "checkpoints", f"{filename} back in models/checkpoints"


def test_model_paths_keep_their_subfolder(catalog):
    """Workflows name models as "Flux 2\\x.gguf"; the install must preserve that."""
    nested = [m for m in catalog.models.values() if "\\" in m.name or "/" in m.name]
    assert nested, "expected at least one model in a subfolder"
    for model in nested:
        assert model.relative_path.parent != Path(model.folder)


def test_human_bytes_reads_naturally():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(5 * 1024 ** 3) == "5.0 GB"
    assert human_bytes(2 * 1024 ** 4) == "2.0 TB"


# --- the downloader --------------------------------------------------------
class FakeResponse:
    """Just enough of requests.Response for the downloader."""

    def __init__(self, body: bytes, status: int = 200, headers=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise dl.requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves a fixed payload, honouring Range the way a real server does."""

    def __init__(self, payload: bytes, honour_range: bool = True):
        self.payload = payload
        self.honour_range = honour_range
        self.headers = {}
        self.requests = []

    def get(self, url, headers=None, stream=False, timeout=None):
        self.requests.append(headers or {})
        start = 0
        if headers and "Range" in headers and self.honour_range:
            start = int(headers["Range"].split("=")[1].split("-")[0])
            return FakeResponse(self.payload[start:], 206,
                                {"Content-Length": str(len(self.payload) - start)})
        return FakeResponse(self.payload)


@pytest.fixture
def payload() -> bytes:
    return bytes(range(256)) * 8192          # 2 MB, so it spans several chunks


def _serve(monkeypatch, session):
    monkeypatch.setattr(dl, "_session", lambda token="": session)
    return session


def test_a_plain_download_lands_at_the_right_size(tmp_path, monkeypatch, payload):
    _serve(monkeypatch, FakeSession(payload))
    target = tmp_path / "model.safetensors"
    dl.download("http://example/model", target, expected_bytes=len(payload))
    assert target.read_bytes() == payload
    assert not target.with_suffix(".safetensors.part").exists()


def test_it_resumes_from_a_part_file(tmp_path, monkeypatch, payload):
    session = _serve(monkeypatch, FakeSession(payload))
    target = tmp_path / "model.safetensors"
    part = tmp_path / "model.safetensors.part"
    part.write_bytes(payload[:600_000])          # a download killed part-way

    dl.download("http://example/model", target, expected_bytes=len(payload))

    assert target.read_bytes() == payload
    assert session.requests[0]["Range"] == "bytes=600000-", "did not ask to continue"


def test_a_server_ignoring_range_restarts_cleanly(tmp_path, monkeypatch, payload):
    """Appending a fresh full body onto a part file would corrupt it silently."""
    _serve(monkeypatch, FakeSession(payload, honour_range=False))
    target = tmp_path / "model.safetensors"
    (tmp_path / "model.safetensors.part").write_bytes(payload[:600_000])

    dl.download("http://example/model", target, expected_bytes=len(payload))
    assert target.read_bytes() == payload


def test_a_wrong_size_is_refused_and_the_bytes_kept(tmp_path, monkeypatch, payload):
    """The worst failure is a wrong file that looks installed - so it must not rename."""
    _serve(monkeypatch, FakeSession(payload))
    target = tmp_path / "model.safetensors"

    with pytest.raises(dl.DownloadError, match="wrong size"):
        dl.download("http://example/model", target, expected_bytes=len(payload) + 1)

    assert not target.exists()
    assert target.with_suffix(".safetensors.part").exists()


def test_a_bad_checksum_is_discarded(tmp_path, monkeypatch, payload):
    _serve(monkeypatch, FakeSession(payload))
    target = tmp_path / "model.safetensors"

    with pytest.raises(dl.DownloadError, match="checksum"):
        dl.download("http://example/model", target, sha256="00" * 32)

    assert not target.exists()
    assert not target.with_suffix(".safetensors.part").exists()


def test_a_good_checksum_passes(tmp_path, monkeypatch, payload):
    import hashlib

    _serve(monkeypatch, FakeSession(payload))
    target = tmp_path / "model.safetensors"
    dl.download("http://example/model", target,
                sha256=hashlib.sha256(payload).hexdigest())
    assert target.is_file()


def test_an_existing_correct_file_is_left_alone(tmp_path, monkeypatch, payload):
    session = _serve(monkeypatch, FakeSession(payload))
    target = tmp_path / "model.safetensors"
    target.write_bytes(payload)

    dl.download("http://example/model", target, expected_bytes=len(payload))
    assert session.requests == [], "re-downloaded a file that was already correct"


def test_cancelling_keeps_what_was_downloaded(tmp_path, monkeypatch, payload):
    _serve(monkeypatch, FakeSession(payload))
    target = tmp_path / "model.safetensors"
    seen = []

    def stop():
        seen.append(1)
        return len(seen) > 1            # let one chunk through, then stop

    with pytest.raises(dl.Cancelled):
        dl.download("http://example/model", target, should_stop=stop)

    part = target.with_suffix(".safetensors.part")
    assert not target.exists()
    assert part.is_file() and part.stat().st_size > 0, "nothing kept to resume from"


def test_needing_an_account_says_so(tmp_path, monkeypatch):
    class Gated(FakeSession):
        def get(self, url, headers=None, stream=False, timeout=None):
            return FakeResponse(b"", 401)

    _serve(monkeypatch, Gated(b""))
    with pytest.raises(dl.DownloadError, match="account"):
        dl.download("http://example/m", tmp_path / "m.safetensors")


def test_a_refused_licence_says_where_to_get_a_key(tmp_path, monkeypatch):
    class Refused(FakeSession):
        def get(self, url, headers=None, stream=False, timeout=None):
            return FakeResponse(b"", 403)

    _serve(monkeypatch, Refused(b""))
    with pytest.raises(dl.DownloadError, match="licence") as caught:
        dl.download("https://huggingface.co/x/resolve/main/m.safetensors",
                    tmp_path / "m.safetensors")
    assert "settings/tokens" in str(caught.value), "should say where to get the key"


def test_an_unknown_host_gets_an_honest_message(tmp_path, monkeypatch):
    """No account page is known for it, so do not invent one."""
    class Refused(FakeSession):
        def get(self, url, headers=None, stream=False, timeout=None):
            return FakeResponse(b"", 403)

    _serve(monkeypatch, Refused(b""))
    with pytest.raises(dl.DownloadError, match="link may have moved"):
        dl.download("https://example.test/m", tmp_path / "m.safetensors")


def test_a_redirect_to_a_login_page_is_not_saved_as_a_model(tmp_path, monkeypatch):
    """Civitai answers 200 and redirects to auth.civitai.com instead of 401.

    Taken at face value that writes a few KB of sign-in HTML into a file named
    like a LoRA, which then looks installed and fails at generation time.
    """
    class Redirected(FakeSession):
        def get(self, url, headers=None, stream=False, timeout=None):
            r = FakeResponse(b"<html>sign in</html>")
            r.url = "https://auth.civitai.com/login?returnUrl=%2Fapi%2Fdownload"
            return r

    _serve(monkeypatch, Redirected(b""))
    target = tmp_path / "KNP_000003000.safetensors"
    with pytest.raises(dl.DownloadError, match="Civitai"):
        dl.download("https://civitai.com/api/download/models/3147117", target)
    assert not target.exists()


def test_a_signed_cdn_link_is_not_mistaken_for_a_login(tmp_path, monkeypatch, payload):
    """Query strings on signed links carry all sorts of words - only host/path count."""
    class Signed(FakeSession):
        def get(self, url, headers=None, stream=False, timeout=None):
            r = super().get(url, headers, stream, timeout)
            r.url = ("https://cdn-lfs.hf.co/repos/ab/model.safetensors"
                     "?X-Amz-Credential=auth.example&x=/login")
            return r

    _serve(monkeypatch, Signed(payload))
    target = tmp_path / "model.safetensors"
    dl.download("https://huggingface.co/x/resolve/main/model.safetensors", target,
                expected_bytes=len(payload))
    assert target.read_bytes() == payload


def test_the_account_message_names_the_right_site(tmp_path, monkeypatch):
    class Refused(FakeSession):
        def get(self, url, headers=None, stream=False, timeout=None):
            return FakeResponse(b"", 401)

    _serve(monkeypatch, Refused(b""))
    with pytest.raises(dl.DownloadError, match="HuggingFace"):
        dl.download("https://huggingface.co/x/resolve/main/m.safetensors",
                    tmp_path / "m.safetensors")
    with pytest.raises(dl.DownloadError, match="Civitai"):
        dl.download("https://civitai.com/api/download/models/1",
                    tmp_path / "n.safetensors")


def test_each_host_gets_its_own_token_and_the_mirror_gets_none(tmp_path, catalog,
                                                               monkeypatch):
    """Keys must follow the URL being called, not the model they belong to.

    A mirrored model still records its HuggingFace origin. If the token were
    chosen from the model rather than the request, the user's HuggingFace
    credential would be handed to the mirror's server, which has no business
    seeing it.
    """
    sent = []

    def fake_download(url, target, expected_bytes=0, token="", **kw):
        sent.append((url.split("/")[2], token))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"")
        return target

    monkeypatch.setattr("setup.steps.download", fake_download)
    installer = Installer(catalog, tmp_path, list(catalog.groups),
                          hf_token="hf_key", civitai_token="cv_key")
    installer.install_models()

    for host, token in sent:
        if host == "huggingface.co":
            assert token == "hf_key"
        elif host == "civitai.com":
            assert token == "cv_key"
        else:
            assert token == "", f"a credential was sent to {host}"
    assert {h for h, _ in sent} >= {"huggingface.co", "civitai.com"}


def test_a_mirrored_model_is_fetched_from_the_mirror_first(tmp_path, catalog,
                                                           monkeypatch):
    tried = []

    def fake_download(url, target, **kw):
        tried.append(url)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"")
        return target

    monkeypatch.setattr("setup.steps.download", fake_download)
    mirrored = catalog.mirrored(list(catalog.groups))
    assert mirrored, "expected the catalogue to carry mirrored models"

    installer = Installer(catalog, tmp_path, list(catalog.groups))
    installer.install_models()

    for model in mirrored:
        assert model.mirror in tried, f"{model.filename} never tried the mirror"
        assert model.url not in tried, f"{model.filename} went to the origin anyway"


def test_when_the_mirror_fails_the_original_is_used(tmp_path, catalog, monkeypatch):
    """A revoked share link must degrade to 'needs a key', not to a dead end."""
    tried = []

    def fake_download(url, target, **kw):
        tried.append(url)
        if "focaltek" in url or "owncloud" in url:
            raise dl.DownloadError("share link revoked")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"")
        return target

    monkeypatch.setattr("setup.steps.download", fake_download)
    model = catalog.mirrored(["video"])[0]
    installer = Installer(catalog, tmp_path, ["video"])
    installer._fetch_model(model, tmp_path / model.filename)

    assert tried == [model.mirror, model.url]


def test_falling_back_discards_the_failed_source_part_file(tmp_path, catalog,
                                                           monkeypatch):
    """Two copies are the same length, so a half-and-half file passes the size
    check and only shows up as a corrupt model much later."""
    seen = []

    def fake_download(url, target, **kw):
        part = Path(target).with_suffix(Path(target).suffix + ".part")
        seen.append((url, part.exists() and part.stat().st_size or 0))
        if "owncloud" in url:
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(b"x" * 4096)         # a killed mirror download
            raise dl.DownloadError("server went away")
        Path(target).write_bytes(b"")
        return target

    monkeypatch.setattr("setup.steps.download", fake_download)
    model = catalog.mirrored(["video"])[0]
    installer = Installer(catalog, tmp_path, ["video"])
    installer._fetch_model(model, tmp_path / model.filename)

    assert len(seen) == 2
    assert seen[1][1] == 0, "the origin resumed from the mirror's part file"


def test_one_unobtainable_model_does_not_sink_the_whole_install(tmp_path, catalog,
                                                                monkeypatch):
    """A missing key on one file must not discard 100 GB that downloaded fine."""
    doomed = catalog.models_for(["video"])[3]

    def fake_download(url, target, **kw):
        if Path(target).name == doomed.filename:
            raise dl.DownloadError("needs an account")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"")
        return target

    monkeypatch.setattr("setup.steps.download", fake_download)
    installer = Installer(catalog, tmp_path, ["video"])
    installer.install_models()

    others = len(catalog.models_for(["video"])) - 1
    assert len(installer.report.done) == others, "gave up after the first failure"
    assert any(doomed.filename in f for f in installer.report.failed)


def test_when_every_source_fails_the_error_names_them_all(tmp_path, catalog,
                                                          monkeypatch):
    def fake_download(url, target, **kw):
        raise dl.DownloadError("nope")

    monkeypatch.setattr("setup.steps.download", fake_download)
    model = catalog.mirrored(["video"])[0]
    installer = Installer(catalog, tmp_path, ["video"])

    with pytest.raises(dl.DownloadError) as caught:
        installer._fetch_model(model, tmp_path / model.filename)
    message = str(caught.value)
    assert "mirror" in message and "original" in message


def test_a_model_without_a_mirror_is_unaffected(catalog):
    plain = [m for m in catalog.models.values() if not m.mirror and m.url]
    assert plain
    assert plain[0].sources == [("original", plain[0].url)]


def test_mirrored_models_carry_a_hash(catalog):
    """The mirror is the weakest link, so its files must be verifiable."""
    for model in catalog.mirrored(list(catalog.groups)):
        assert len(model.sha256) == 64, f"{model.filename} has no usable sha256"


def test_a_mirrored_install_writes_the_licences(tmp_path, catalog, monkeypatch):
    """Passing the licence on is the condition that permits redistributing."""
    monkeypatch.setattr("setup.steps.download",
                        lambda url, target, **kw: (
                            Path(target).parent.mkdir(parents=True, exist_ok=True),
                            Path(target).write_bytes(b""), target)[-1])
    installer = Installer(catalog, tmp_path, ["video"])
    installer.install_models()

    licences = tmp_path / "LICENSES"
    assert (licences / "LICENSE-LTX-2.x.md").is_file()
    text = (licences / "LICENSE-LTX-2.x.md").read_text(encoding="utf-8")
    assert "LTX-2.x Community License" in text


def test_no_licences_folder_when_nothing_is_mirrored(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr("setup.steps.download",
                        lambda url, target, **kw: (
                            Path(target).parent.mkdir(parents=True, exist_ok=True),
                            Path(target).write_bytes(b""), target)[-1])
    installer = Installer(catalog, tmp_path, ["prompt-enhancer"])
    installer.install_models()
    assert not (tmp_path / "LICENSES").exists()


def test_private_addresses_are_recognised():
    """A LAN link works for whoever uploaded it and for nobody else."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tools"))
    from verify_urls import is_private

    for url in ("http://192.168.0.36/owncloud/s/x/download", "http://10.0.0.5/x",
                "http://127.0.0.1:8080/x", "http://localhost/x",
                "http://172.16.4.4/x", "http://172.31.255.1/x"):
        assert is_private(url), url
    for url in ("https://focaltek.com/owncloud/index.php/s/x/download",
                "https://huggingface.co/a/resolve/main/b.safetensors",
                "http://172.32.4.4/x"):          # just outside the private range
        assert not is_private(url), url


def test_the_mirror_removes_the_need_for_a_key(catalog):
    """What the whole exercise is for: only Flux 3 Klein should need an account.

    Its files come from Black Forest Labs' own repository, behind their
    licence, and are not mirrored - redistributing them is not ours to do.
    Every other image model needs no account at all.
    """
    gated = catalog.gated(["image"])
    assert gated
    assert all("black-forest-labs/flux-3-image-klein-comfy/" in m.url for m in gated)
    assert catalog.gated(["prompt-enhancer"]) == []


def test_a_dropped_connection_is_retried(tmp_path, monkeypatch, payload):
    class Flaky(FakeSession):
        def __init__(self, body):
            super().__init__(body)
            self.attempts = 0

        def get(self, url, headers=None, stream=False, timeout=None):
            self.attempts += 1
            if self.attempts == 1:
                raise dl.requests.ConnectionError("dropped")
            return super().get(url, headers, stream, timeout)

    session = _serve(monkeypatch, Flaky(payload))
    monkeypatch.setattr(dl, "BACKOFF", 0)
    target = tmp_path / "model.safetensors"

    dl.download("http://example/model", target, expected_bytes=len(payload))
    assert session.attempts == 2
    assert target.read_bytes() == payload


def test_eta_is_phrased_for_a_human():
    assert dl.format_eta(None) == ""
    assert dl.format_eta(45) == "45s left"
    assert dl.format_eta(600) == "10 min left"
    assert dl.format_eta(7200) == "2.0 hours left"


def test_progress_percentage():
    p = dl.Progress(done=250, total=1000, speed=50)
    assert p.percent == 25
    assert p.eta_seconds == 15
    assert dl.Progress(done=10, total=0).percent == 0
    assert dl.Progress(done=10, total=10, speed=5).eta_seconds is None


# --- the steps -------------------------------------------------------------
def test_the_disk_check_refuses_an_impossible_install(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr("setup.steps.free_space", lambda p: 1024 ** 3)
    installer = Installer(catalog, tmp_path, ["video"])
    with pytest.raises(OSError, match="Not enough room"):
        installer.check_space()


def test_the_disk_check_passes_with_headroom(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr("setup.steps.free_space", lambda p: 500 * 1024 ** 3)
    Installer(catalog, tmp_path, ["prompt-enhancer"]).check_space()


def test_a_finished_install_skips_everything(tmp_path, catalog, monkeypatch):
    """The second run of a completed install must not re-download anything."""
    monkeypatch.setattr("setup.steps.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("setup.steps.download", _refuse_to_download)
    # run() ends by writing EasyAI's settings, which is the real file on this
    # machine. Without this the test suite quietly repoints the developer's own
    # ComfyUI and workflow folders at a pytest temp directory.
    monkeypatch.setattr("app.config.SETTINGS_PATH", tmp_path / "settings.json")

    installer = Installer(catalog, tmp_path, ["prompt-enhancer"])
    installer.python.parent.mkdir(parents=True, exist_ok=True)
    installer.python.write_bytes(b"")
    for model in catalog.models_for(["prompt-enhancer"]):
        if not model.installable:
            continue
        path = installer.models_dir / model.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:                    # sparse, so no real 4.9 GB
            f.truncate(model.bytes)

    report = installer.run()
    assert "comfyui" in report.skipped
    assert not [f for f in report.failed if "no link" not in f]


def _refuse_to_download(*args, **kwargs):
    raise AssertionError("downloaded something that was already present")


def test_the_state_file_records_what_happened(tmp_path, catalog):
    """The version recorded is the one really installed - which is not the
    catalogue's when the user chose to leave an existing ComfyUI alone."""
    comfy = tmp_path / "ComfyUI_windows_portable" / "ComfyUI"
    comfy.mkdir(parents=True)
    (comfy / "comfyui_version.py").write_text('__version__ = "0.33.0"\n', encoding="utf-8")

    installer = Installer(catalog, tmp_path, ["image"])
    installer.report.done.append("comfyui")
    installer.write_state()

    state = json.loads((tmp_path / "easyai-setup.json").read_text(encoding="utf-8"))
    assert state["groups"] == ["image"]
    assert state["comfyui"] == "0.33.0"
    assert state["tested_with"] == catalog.comfyui["version"]
    assert "comfyui" in state["installed"]


def test_workflows_are_copied_for_the_chosen_groups_only(tmp_path, catalog):
    installer = Installer(catalog, tmp_path, ["image"])
    installer.copy_workflows()

    copied = tmp_path / "EasyAI-workflows"
    assert (copied / "image").is_dir()
    assert not (copied / "video").exists()
    assert list((copied / "image").glob("*.json"))


def test_easyai_can_find_the_launchers_where_the_install_puts_them(tmp_path, catalog):
    """The folder Setup tells the user to paste must be the one EasyAI accepts.

    EasyAI looks for run_*.bat in the folder it is given. That is the portable
    folder, not the folder containing it, so Setup has to name the subfolder.
    """
    from app.comfy.launcher import ComfyLauncher

    installer = Installer(catalog, tmp_path, ["image"])
    installer.portable.mkdir(parents=True, exist_ok=True)
    (installer.portable / "run_nvidia_gpu.bat").write_text("", encoding="utf-8")

    assert ComfyLauncher(str(installer.portable), None, None).find_launchers()
    assert not ComfyLauncher(str(tmp_path), None, None).find_launchers()


def test_the_installer_writes_nothing_outside_its_target(tmp_path, catalog):
    """EasyAI must be untouched - that was the whole condition on this tool."""
    installer = Installer(catalog, tmp_path, ["image"])
    before = {p.stat().st_mtime_ns for p in ROOT.glob("*.py")}
    installer.copy_workflows()
    installer.write_state()
    assert {p.stat().st_mtime_ns for p in ROOT.glob("*.py")} == before
    assert installer.target == tmp_path


# --- pointing EasyAI at the install ----------------------------------------
def test_setup_writes_the_comfyui_path_into_easyai_settings(tmp_path, catalog,
                                                            monkeypatch):
    """A viewer should not finish a multi-hour install and then be asked to
    type a path in by hand - the one step most likely to be got wrong, because
    EasyAI needs the portable folder itself, not the folder holding it."""
    import app.config as config

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", settings)

    installer = Installer(catalog, tmp_path / "install", ["image"])
    installer.portable.mkdir(parents=True, exist_ok=True)
    installer.python.parent.mkdir(parents=True, exist_ok=True)
    installer.python.write_bytes(b"")
    (installer.portable / "run_nvidia_gpu.bat").write_text("", encoding="utf-8")

    installer.configure_easyai()

    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved["comfyui_dir"] == str(installer.portable)
    assert saved["comfyui_launcher"] == "run_nvidia_gpu.bat"
    assert saved["first_run_done"] is True
    assert "easyai-settings" in installer.report.done


def test_it_keeps_settings_the_user_already_chose(tmp_path, catalog, monkeypatch):
    """Merged, never replaced: a second install must not reset someone's
    preferences just because it now knows where ComfyUI is."""
    import app.config as config

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "comfyui_dir": r"C:\somewhere\old",
        "default_ratio": "9:16",
        "image_megapixels": 1.7,
        "language": "zh-Hant",
    }), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_PATH", settings)

    installer = Installer(catalog, tmp_path / "install", ["image"])
    installer.python.parent.mkdir(parents=True, exist_ok=True)
    installer.python.write_bytes(b"")
    installer.configure_easyai()

    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved["comfyui_dir"] == str(installer.portable)   # updated
    assert saved["default_ratio"] == "9:16"                  # kept
    assert saved["image_megapixels"] == 1.7                  # kept
    assert saved["language"] == "zh-Hant"                    # kept


def test_nothing_is_written_when_comfyui_is_not_there(tmp_path, catalog,
                                                      monkeypatch):
    """A failed or cancelled install must not point EasyAI at an empty folder."""
    import app.config as config

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", settings)

    installer = Installer(catalog, tmp_path / "install", ["image"])
    installer.configure_easyai()

    assert not settings.exists()
    assert "easyai-settings" not in installer.report.done


def test_the_workflows_folder_is_pointed_at_the_copied_ones(tmp_path, catalog,
                                                            monkeypatch):
    import app.config as config

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", settings)

    installer = Installer(catalog, tmp_path / "install", ["image"])
    installer.python.parent.mkdir(parents=True, exist_ok=True)
    installer.python.write_bytes(b"")
    copied = installer.target / "EasyAI-workflows" / "image"
    copied.mkdir(parents=True)
    (copied / "a.json").write_text("{}", encoding="utf-8")

    installer.configure_easyai()

    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved["workflow_dir"] == str(installer.target / "EasyAI-workflows")
