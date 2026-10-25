"""Captures numbered screenshots of every EasyAI screen listed in outline.md,
for EasyAI v1.1.0.

Launches the real EasyAI.py, EasyAISetup.py and EasyAIStudio.py -- nothing is
synthesized. Window discovery / existence / screenshot cropping uses
pywinauto's win32 backend (Qt top-level windows are real HWNDs, reliably
enumerated by EnumWindows -- the UIA Desktop enumerator was found to silently
miss owned Qt dialogs). Clicking specific controls inside a window (buttons,
tabs, menu items, list rows) uses pywinauto's UIA backend wrapped around that
same HWND, because Qt exposes its widgets to UI Automation even though most
have no separate child HWND. The actual pixel capture is done with pyautogui
(Pillow-backed).

This script deliberately never starts ComfyUI and never presses Create: the
engine is left down for the whole session (auto_launch is off, and no
process is listening on the configured port), so the Startup Splash is
captured mid-"carry on without it", the engine pill reads "down" everywhere,
and the Queue/results stay in their fresh-install empty state. That is noted
per-screenshot in screenshots/index.md rather than faked.

Run with:  python docs/manual/_capture.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pyautogui
from pywinauto import Application, Desktop

ROOT = Path(__file__).resolve().parents[2]
SHOTS = Path(__file__).resolve().parent / "screenshots"
LOG_PATH = Path(__file__).resolve().parent / "_capture.log"
SETTINGS_PATH = ROOT / "settings.json"
LANG_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "EasyAI" / "language.json"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

pyautogui.FAILSAFE = False

desktop = Desktop(backend="win32")

RESULTS: list[tuple[str, bool, str]] = []   # (name, ok, note/error)


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    log(f"{'OK ' if ok else 'FAIL'} - {name} {('- ' + note) if note else ''}")


# --------------------------------------------------------------------- setup
def backup_language() -> str | None:
    try:
        return LANG_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return None


def force_english_language() -> None:
    try:
        LANG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LANG_PATH.write_text(json.dumps({"language": "en"}), encoding="utf-8")
    except OSError as e:
        log(f"could not force language.json to en: {e}")


def restore_language(previous: str | None) -> None:
    try:
        if previous is None:
            if LANG_PATH.exists():
                LANG_PATH.unlink()
        else:
            LANG_PATH.write_text(previous, encoding="utf-8")
    except OSError as e:
        log(f"could not restore language.json: {e}")


def backup_settings() -> str | None:
    try:
        return SETTINGS_PATH.read_text(encoding="utf-8")
    except OSError:
        return None


def restore_settings(previous: str | None) -> None:
    try:
        if previous is None:
            if SETTINGS_PATH.exists():
                SETTINGS_PATH.unlink()
        else:
            SETTINGS_PATH.write_text(previous, encoding="utf-8")
    except OSError as e:
        log(f"could not restore settings.json: {e}")


def write_fresh_settings() -> None:
    """A fresh-install settings.json, with first_run_done cleared so the
    first-run Welcome/Settings flow shows up, and auto_launch off so nothing
    here ever spawns a real ComfyUI process (this machine has none listening
    on 8188 for the whole capture session -- verified before running)."""
    data = {
        "comfyui_dir": r"C:\AI ComfyUI - New Version\ComfyUI_windows_portable",
        "comfyui_launcher": "run_nvidia_gpu.bat",
        "comfyui_server": "127.0.0.1:8188",
        "auto_launch": False,
        "launch_timeout": 30,
        "job_timeout": 1800,
        "stop_engine_on_exit": False,
        "close_when_queue_empty": False,
        "workflow_dir": str(ROOT / "workflows"),
        "output_dir": str(ROOT / "output"),
        "default_ratio": "2:3",
        "lock_seed": False,
        "locked_seeds": {},
        "model_choice": {},
        "batch_count": 1,
        "use_prompt_enhancer": True,
        "image_megapixels": 1.0,
        "video_megapixels": 0.5,
        "image_megapixels_warn": 1.5,
        "video_megapixels_warn": 1.0,
        "video_length_default": 5,
        "video_length_min": 2,
        "video_length_max": 15,
        "video_length_warn": 8,
        "language": "en",
        "theme": "dark",
        "window_geometry": "",
        "first_run_done": False,
    }
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ------------------------------------------------------------------- helpers
def wait_win(timeout=25, **kwargs):
    """Poll the win32 Desktop for a top-level window matching kwargs
    (title=, title_re=, class_name_re=...). Returns the win32 wrapper."""
    end = time.time() + timeout
    last_err = None
    while time.time() < end:
        try:
            w = desktop.window(**kwargs)
            if w.exists() and w.is_visible():
                return w
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.3)
    raise TimeoutError(f"window not found for {kwargs} ({last_err})")


def wait_win_for_pid(pid: int, timeout=25, **kwargs):
    """Like wait_win, but scoped to windows owned by a specific process --
    needed because generic titles like "Settings" can collide with an
    unrelated window already open on the desktop (e.g. the OS Settings app)."""
    end = time.time() + timeout
    last_err = None
    while time.time() < end:
        try:
            for w in desktop.windows(**kwargs):
                try:
                    if w.is_visible() and w.process_id() == pid:
                        return w
                except Exception:
                    continue
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.2)
    raise TimeoutError(f"window not found for {kwargs} pid={pid} ({last_err})")


def uia_of(win32_win):
    """Wrap a win32-discovered HWND with a UIA window, for control access."""
    hwnd = win32_win.handle
    app = Application(backend="uia").connect(handle=hwnd)
    return app.window(handle=hwnd)


def snap(win32_win, filename: str) -> Path:
    win32_win.set_focus()
    time.sleep(0.6)
    rect = win32_win.rectangle()
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    width, height = right - left, bottom - top
    SHOTS.mkdir(parents=True, exist_ok=True)
    out_path = SHOTS / filename
    img = pyautogui.screenshot(region=(left, top, width, height))
    img.save(str(out_path))
    return out_path


def click_in(uia_win, timeout=10, **kwargs) -> None:
    """Click a control, preferring the UIA Invoke pattern (a synthetic
    click_input can land on whatever is left behind if the control closes
    mid-click, e.g. clicking a dialog's OK button can click-through onto
    the window underneath)."""
    ctrl = uia_win.child_window(**kwargs)
    ctrl.wait("visible enabled", timeout=timeout)
    try:
        ctrl.invoke()
    except Exception:
        ctrl.click_input()


_watch_stop = threading.Event()


def _stray_dialog_watcher() -> None:
    """Runs for the life of the EasyAI process: the engine-start worker can
    report failure a couple of seconds *after* the startup splash is
    skipped, racing with whatever capture step runs next. Rather than trying
    to time that precisely, just keep sweeping it away in the background."""
    while not _watch_stop.is_set():
        try:
            dlg = desktop.window(title_re="The AI engine did not start.*")
            if dlg.exists() and dlg.is_visible():
                log(f"[watcher] dismissing stray dialog: {dlg.window_text()!r}")
                try:
                    uia_of(dlg).child_window(
                        title="OK", control_type="Button").invoke()
                except Exception as e:  # noqa: BLE001
                    log(f"[watcher] could not dismiss stray dialog: {e}")
        except Exception:
            pass
        _watch_stop.wait(0.5)


def start_watcher() -> None:
    _watch_stop.clear()
    t = threading.Thread(target=_stray_dialog_watcher, daemon=True)
    t.start()


def stop_watcher() -> None:
    _watch_stop.set()


def _visible_lists(uwin):
    """The UIA tree keeps other (hidden) tabs' widgets around, so filter to
    ones that are actually visible before picking "the workflow list"."""
    lists = [l for l in uwin.descendants(control_type="List") if l.is_visible()]
    return sorted(lists, key=lambda w: w.rectangle().left)


def _lists_with_retry(win32_win, timeout=10.0):
    """The UIA client-side cache can be empty for a bit after a tab switch
    or window focus change (observed even when the widget is definitely on
    screen already), so retry with a *fresh* UIA connection each attempt
    rather than trusting one cached tree walker."""
    end = time.time() + timeout
    lists = []
    while time.time() < end:
        try:
            uwin = uia_of(win32_win)
            lists = _visible_lists(uwin)
            if lists and any(l.children(control_type="ListItem") for l in lists):
                return lists
        except Exception:
            pass
        time.sleep(0.4)
    return lists


def select_list_item_by_position(win32_win, list_index: int, item_index: int) -> None:
    """Focus a QListWidget by picking the Nth visible "List" control found
    (left to right), then move to a given row with the keyboard. Used for
    the workflow list, whose rows are custom-drawn QListWidgetItems and so
    may not carry a reliable UIA Name of their own -- position + keyboard
    nav is the robust route, same reasoning as the Help-menu mnemonics
    below."""
    lists = _lists_with_retry(win32_win)
    if len(lists) <= list_index:
        raise RuntimeError(f"expected at least {list_index + 1} visible list(s), found {len(lists)}")
    target = lists[list_index]
    items = target.children(control_type="ListItem")
    if not items:
        raise RuntimeError("workflow list has no rows")
    items[0].click_input()
    time.sleep(0.3)
    for _ in range(item_index):
        target.type_keys("{DOWN}", pause=0.05)
        time.sleep(0.15)
    target.type_keys("{ENTER}", pause=0.05)
    time.sleep(0.6)


# --------------------------------------------------------------- EasyAI flow
_EASYAI_PID = [None]


def run_easyai() -> None:
    log_file = open(Path(__file__).resolve().parent / "_easyai_stdout.log", "w",
                     encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "EasyAI.py"], cwd=str(ROOT),
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    _EASYAI_PID[0] = proc.pid
    log(f"Launched EasyAI.py pid={proc.pid}")
    start_watcher()

    try:
        _capture_first_run_and_settings()
        main_win = _capture_splash_and_main()
        if main_win is not None:
            _capture_image_tab(main_win)
            _capture_video_tab(main_win)
            _capture_prompt_helper_tab(main_win)
            _capture_queue_tab(main_win)
            _capture_read_tab(main_win)
            _capture_manifest_editor(main_win)
            _capture_rename(main_win)
            _capture_help(main_win)
            _close_main_window(main_win)
    finally:
        stop_watcher()
        # Best-effort clean shutdown regardless of what failed above.
        try:
            if proc.poll() is None:
                proc.wait(timeout=15)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        log_file.close()


def _capture_first_run_and_settings() -> None:
    try:
        welcome = wait_win(title="Welcome to EasyAI", timeout=30)
        snap(welcome, "01_first-run-welcome.png")
        record("01 First-run Welcome", True)
        click_in(uia_of(welcome), title="OK", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("01 First-run Welcome", False, f"{type(e).__name__}: {e}")

    try:
        settings = wait_win_for_pid(_EASYAI_PID[0], title="Settings", timeout=15)
        snap(settings, "02_settings.png")
        record("02 Settings", True, "engine down (ComfyUI not running/started)")
        click_in(uia_of(settings), title="Save", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("02 Settings", False, f"{type(e).__name__}: {e}")


def _capture_splash_and_main():
    try:
        splash = wait_win(title="EasyAI", timeout=25)
        snap(splash, "03_startup-splash.png")
        record("03 Startup Splash", True)
        try:
            click_in(uia_of(splash), title="Carry on without it",
                     control_type="Button", timeout=5)
        except Exception:
            pass  # splash may already have finished (button not present yet)
    except Exception as e:  # noqa: BLE001
        record("03 Startup Splash", False, f"{type(e).__name__}: {e}")

    try:
        main_win = wait_win(title_re="EasyAI . simple AI creation.*", timeout=30)
        time.sleep(1.5)  # let the background watcher catch any stray warning
        main_win.set_focus()
        snap(main_win, "04_main-window.png")
        record("04 Main Window", True, "engine down; one workflow group loaded")
        # The "AI engine did not start" warning (auto_launch is off, so this
        # always fires) lands a couple of seconds after the main window
        # appears, racing with whatever capture step runs next; give the
        # background watcher a clear window to catch and dismiss it before
        # any tab clicks are attempted, or a click can land mid-modal and
        # raise a COM event-delivery error.
        time.sleep(3.0)
        return main_win
    except Exception as e:  # noqa: BLE001
        record("04 Main Window", False, f"{type(e).__name__}: {e}")
        return None


def _select_tab(uwin, label_re: str) -> None:
    tab_item = uwin.child_window(title_re=label_re, control_type="TabItem")
    tab_item.wait("visible enabled", timeout=10)
    try:
        tab_item.select()
    except Exception:
        tab_item.click_input()
    time.sleep(0.6)


def _retry(fn, attempts=3, delay=1.0):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(delay)
    raise last


def _capture_image_tab(win32_win) -> None:
    try:
        _retry(lambda: _select_tab(uia_of(win32_win), ".*Image.*"))
        snap(win32_win, "05_image-tab-default.png")
        record("05 Image tab (default workflow)", True,
               "no result yet -- Create was never pressed")
    except Exception as e:  # noqa: BLE001
        record("05 Image tab (default workflow)", False, f"{type(e).__name__}: {e}")

    # Select "Qwen Image 2.1 - Image Edit" (index 3 of 8 image workflows in
    # natural-sort filename order) to show the negative-prompt box and the
    # workflow's auto-growing reference-picture group (revealed one slot at
    # a time, per app/ui/tab_base.py:_reveal_next).
    try:
        _retry(lambda: select_list_item_by_position(win32_win, 0, 3))
        snap(win32_win, "06_image-tab-negative-and-slots.png")
        record("06 Image tab - negative prompt + first picture slot", True,
               "only 'Image 1' is shown at first -- it is a grown reference "
               "group revealed one slot at a time, not ten static slots")
    except Exception as e:  # noqa: BLE001
        record("06 Image tab - negative prompt + first picture slot",
               False, f"{type(e).__name__}: {e}")

    try:
        select_list_item_by_position(win32_win, 0, 0)   # back to the first workflow
    except Exception:
        pass


def _capture_video_tab(win32_win) -> None:
    uwin = uia_of(win32_win)
    try:
        _select_tab(uwin, ".*Video.*")
        snap(win32_win, "07_video-tab.png")
        record("07 Video tab", True,
               "no result yet -- Length control visible, no video preview")
    except Exception as e:  # noqa: BLE001
        record("07 Video tab", False, f"{type(e).__name__}: {e}")


def _capture_prompt_helper_tab(win32_win) -> None:
    uwin = uia_of(win32_win)
    try:
        _select_tab(uwin, ".*Prompt Helper.*")
        snap(win32_win, "08_prompt-helper-tab.png")
        record("08 Prompt Helper tab", True,
               "no result yet -- empty history list")
    except Exception as e:  # noqa: BLE001
        record("08 Prompt Helper tab", False, f"{type(e).__name__}: {e}")


def _capture_queue_tab(win32_win) -> None:
    uwin = uia_of(win32_win)
    try:
        _select_tab(uwin, ".*Queue.*")
        snap(win32_win, "09_queue-tab-empty.png")
        record("09 Queue tab", True,
               "empty state -- nothing queued, since Create was never "
               "pressed (no jobs were sent to ComfyUI for this capture)")
    except Exception as e:  # noqa: BLE001
        record("09 Queue tab", False, f"{type(e).__name__}: {e}")


def _capture_read_tab(win32_win) -> None:
    uwin = uia_of(win32_win)
    try:
        _select_tab(uwin, ".*Read a prompt.*")
        snap(win32_win, "10_read-a-prompt-tab-gallery.png")
        record("10 Read a prompt tab (results gallery)", True,
               "gallery populated from pre-existing files under output/, "
               "not from anything generated by this script")
    except Exception as e:  # noqa: BLE001
        record("10 Read a prompt tab (results gallery)", False,
               f"{type(e).__name__}: {e}")
        return

    # Click the first result thumbnail so the prompt/preview panes populate.
    try:
        lists = _lists_with_retry(win32_win)
        if not lists:
            raise RuntimeError("results gallery is empty")
        gallery_list = lists[0]
        items = gallery_list.children(control_type="ListItem")
        if not items:
            raise RuntimeError("results gallery is empty")
        items[0].click_input()
        time.sleep(0.8)
        snap(win32_win, "11_read-a-prompt-tab-result.png")
        record("11 Read a prompt tab (a result opened, prompt shown)", True)
    except Exception as e:  # noqa: BLE001
        record("11 Read a prompt tab (a result opened, prompt shown)", False,
               f"{type(e).__name__}: {e}")


def _capture_manifest_editor(win32_win) -> None:
    uwin = uia_of(win32_win)
    try:
        _select_tab(uwin, ".*Image.*")
        # Select "Qwen Image 2.1 - Image Edit" again so Set up... opens its
        # manifest (ten picture slots).
        select_list_item_by_position(win32_win, 0, 3)
        click_in(uwin, title_re="Set up.*", control_type="Button", timeout=10)
        editor = wait_win(title_re="Set up —.*", timeout=15)
        snap(editor, "12_manifest-editor.png")
        record("12 Manifest Editor (Set up…)", True,
               "no 'guessed binding' warning banner visible -- none of the "
               "shipped workflows currently have a non-empty 'ambiguous' "
               "list to trigger it")
        click_in(uia_of(editor), title="Cancel", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("12 Manifest Editor (Set up…)", False, f"{type(e).__name__}: {e}")


def _capture_rename(win32_win) -> None:
    try:
        win32_win.set_focus()
        time.sleep(0.3)
        win32_win.type_keys("{F2}")
        dlg = wait_win(title="Rename", timeout=10)
        snap(dlg, "13_rename-dialog.png")
        record("13 Rename dialog", True)
        click_in(uia_of(dlg), title="Cancel", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("13 Rename dialog", False, f"{type(e).__name__}: {e}")


def _open_help_item(win32_win, down_presses: int) -> None:
    """Open the Help menu and pick an item purely by keyboard.

    Qt's QMenu popups are real but titleless/ownerless-looking top-level
    windows that neither the win32 nor the UIA Desktop enumerators reliably
    surface as clickable targets, so mnemonics + arrow keys are the robust
    route: Alt+H opens "&Help", then Down N times + Enter picks item N.
    """
    win32_win.set_focus()
    time.sleep(0.3)
    win32_win.type_keys("%h", pause=0.05)
    time.sleep(0.4)
    for _ in range(down_presses):
        win32_win.type_keys("{DOWN}", pause=0.05)
        time.sleep(0.15)
    win32_win.type_keys("{ENTER}", pause=0.05)


def _capture_help(win32_win) -> None:
    try:
        _open_help_item(win32_win, 0)   # "How do I add a workflow?"
        dlg = wait_win(title="Adding a workflow", timeout=10)
        snap(dlg, "14_help-add-workflow.png")
        record("14 Help - How do I add a workflow?", True)
        click_in(uia_of(dlg), title="OK", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("14 Help - How do I add a workflow?", False, f"{type(e).__name__}: {e}")

    try:
        _open_help_item(win32_win, 1)   # "About EasyAI"
        dlg = wait_win(title="About EasyAI", timeout=10)
        snap(dlg, "15_help-about.png")
        record("15 Help - About EasyAI", True)
        click_in(uia_of(dlg), title="OK", control_type="Button")
    except Exception as e:  # noqa: BLE001
        record("15 Help - About EasyAI", False, f"{type(e).__name__}: {e}")


def _close_main_window(win32_win) -> None:
    try:
        win32_win.close()
    except Exception as e:  # noqa: BLE001
        log(f"main window close() raised: {e}")


# ---------------------------------------------------------------- Setup app
def run_easyai_setup() -> None:
    log_file = open(Path(__file__).resolve().parent / "_setup_stdout.log", "w",
                     encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "EasyAISetup.py"], cwd=str(ROOT),
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    log(f"Launched EasyAISetup.py pid={proc.pid}")
    try:
        win = wait_win(title_re="EasyAI Setup.*", timeout=25)
        snap(win, "16_easyai-setup.png")
        record("16 EasyAI Setup", True,
               "no ComfyUI folder chosen -- left at its own fresh-install "
               "default, so the found-ComfyUI/update-switch line is not "
               "shown (deliberately did not point it at the real C:\\AI "
               "install)")
        try:
            win.close()
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        record("16 EasyAI Setup", False, f"{type(e).__name__}: {e}")
    finally:
        try:
            if proc.poll() is None:
                proc.wait(timeout=10)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        log_file.close()


# --------------------------------------------------------------- Studio app
def run_easyai_studio() -> None:
    log_file = open(Path(__file__).resolve().parent / "_studio_stdout.log", "w",
                     encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "EasyAIStudio.py"], cwd=str(ROOT),
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    log(f"Launched EasyAIStudio.py pid={proc.pid}")
    try:
        win = wait_win(title_re="EasyAI Studio.*", timeout=25)
        uwin = uia_of(win)
        pages = [
            ("17_easyai-studio-add-workflow.png", "Add a workflow.*",
             "17 EasyAI Studio - Add a workflow page",
             "no workflow file chosen -- report box empty; ComfyUI is not "
             "running so 'Have a look' was not pressed"),
            ("18_easyai-studio-catalogue.png", "Catalogue.*",
             "18 EasyAI Studio - Catalogue page",
             "neither rebuild button was pressed (both need a live ComfyUI "
             "and would rewrite setup/catalog.json)"),
            ("19_easyai-studio-download-links.png", "Download links.*",
             "19 EasyAI Studio - Download links page",
             "neither check button was pressed (both call out to the "
             "network)"),
            ("20_easyai-studio-languages.png", "Languages.*",
             "20 EasyAI Studio - Languages page",
             "neither button was pressed (both rewrite lang/ files)"),
        ]
        for filename, label_re, name, note in pages:
            try:
                item = uwin.child_window(title_re=f".*{label_re}", control_type="ListItem")
                item.wait("visible enabled", timeout=10)
                try:
                    item.select()
                except Exception:
                    item.click_input()
                time.sleep(0.6)
                snap(win, filename)
                record(name, True, note)
            except Exception as e:  # noqa: BLE001
                record(name, False, f"{type(e).__name__}: {e}")
        try:
            win.close()
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        record("17-20 EasyAI Studio pages", False, f"{type(e).__name__}: {e}")
    finally:
        try:
            if proc.poll() is None:
                proc.wait(timeout=10)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        log_file.close()


# ----------------------------------------------------------------------- go
def main() -> None:
    LOG_PATH.write_text("", encoding="utf-8")
    SHOTS.mkdir(parents=True, exist_ok=True)
    prev_settings = backup_settings()
    write_fresh_settings()
    log("settings.json backed up and reset to fresh first-run state")
    prev_lang = backup_language()
    force_english_language()
    log("language.json backed up and forced to en for the capture session")

    try:
        run_easyai()
        time.sleep(1.0)
        run_easyai_setup()
        time.sleep(1.0)
        run_easyai_studio()
    finally:
        restore_language(prev_lang)
        log("language.json restored")
        restore_settings(prev_settings)
        log("settings.json restored")

    log("---- summary ----")
    for name, ok, note in RESULTS:
        log(f"{'OK ' if ok else 'FAIL'} {name} {note}")


if __name__ == "__main__":
    main()
