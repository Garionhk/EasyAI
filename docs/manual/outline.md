# EasyAI — Manual Outline

## Overview

EasyAI is a Windows desktop front end for ComfyUI (Stable-Diffusion/video
node graphs), for people who want to generate images, video and prompts
without learning ComfyUI's node editor: pick a style, type what you want,
choose a shape, press Create. It is a PySide6 (Qt) application, version
**v1.1.0** (`app/__init__.py`, `VERSION_LABEL`, shown in every window title
and About/output).

EasyAI ships as **three separate programs**, sharing the `app/` package,
theme and translations:

- **EasyAI** (`EasyAI.py` / `EasyAI.bat`) — the app itself: Image, Video,
  Prompt Helper, Queue and Read a prompt tabs. This is what a "viewer" (the
  README's term for an end user) runs day to day.
- **EasyAI Setup** (`EasyAISetup.py` / `EasyAISetup.bat`) — installs
  ComfyUI, its add-ons and the models, or updates a ComfyUI the user already
  has. A first-time user with no ComfyUI runs this before EasyAI.
- **EasyAI Studio** (`EasyAIStudio.py` / `EasyAI Studio.bat`) — the
  publishing tool for whoever maintains the workflows (adds new workflows,
  rebuilds the install catalogue, checks download links, keeps translations
  current). Not needed by a viewer.

All three are documented below as in-scope, since the current README
presents them as "EasyAI comes as three programs" and gives each its own
section.

## Requirements & Installation

- **OS**: Windows only (documented requirement: "Windows and an NVIDIA
  graphics card"). `EasyAI.bat`, `EasyAISetup.bat`, `EasyAI Studio.bat` are
  Windows launchers.
- **GPU**: NVIDIA graphics card required per README; workflows were built on
  a 16 GB card. No explicit minimum VRAM figure is stated elsewhere in code
  — see Needs Confirmation.
- **Python**: 3.10 or newer, only needed when running from source.
  `EasyAI.bat` checks the version explicitly and links the Python download if
  missing/too old.
- **Python packages** (`requirements.txt`):
  - `PySide6>=6.6`
  - `requests>=2.31`
  - `websocket-client>=1.7`
  - `Pillow>=10.0`
  - `py7zr>=1.0` — EasyAI Setup only, unpacks the ComfyUI portable `.7z`
    when 7-Zip is not already installed.
- **External dependency**: a working **ComfyUI** install. EasyAI does not
  bundle it — either point Settings at an existing install, or run
  **EasyAI Setup** to download one (ComfyUI 0.37.0, pinned).
- **Download sizes** (per README, via EasyAI Setup): roughly 76 GB for the
  Image group, 138 GB for Video, 5 GB for Prompt Helper.
- **Running from source**:
  ```bash
  pip install -r requirements.txt
  python EasyAI.py
  ```
  `EasyAI.bat` does the same by double-click: finds Python, installs the
  libraries the first time (console shown only for that step), then always
  launches windowless (`pythonw`). `EasyAISetup.bat` and `EasyAI Studio.bat`
  start the other two programs the same way.
- **Prebuilt release**: `EasyAI.exe` and `EasyAI Setup.exe` from the GitHub
  Releases page need nothing else installed (no Python required). Settings,
  workflows and results are kept beside the `.exe`, so it must be run from a
  writable folder (not Program Files). Built via `Build EXE.bat` /
  `*.spec` files / `build_common.py`.
- **First run of EasyAI** (`first_run_done` not yet set): shows a "Welcome
  to EasyAI" message box (`EasyAI.py: main()`), guesses a ComfyUI folder,
  guesses a matching start-file if the guessed one isn't found, and opens
  **Settings** automatically so the user can confirm/correct it (Test now,
  then Save) before anything else happens.
- **Tests** (developer-facing): `python -m pytest tests/ -q` — 861 tests, no
  ComfyUI needed. Separate smoke/E2E scripts (`tests/smoke_comfy.py`,
  `tests/smoke_workflows.py`, `tests/make_starter_workflow.py`,
  `tests/e2e_generate.py`) need a running ComfyUI and are run by hand.

## Screens

Order below follows what a first-time user encounters: launch → first-run
welcome → Settings → startup splash → main window/tabs (Image, Video, Prompt
Helper, Queue, Read a prompt) → workflow setup/rename dialogs → menu-driven
dialogs → then the two companion programs, EasyAI Setup and EasyAI Studio.

### First-run Welcome (message box)

- Purpose: One-time notice, before any generation is possible, telling the
  user EasyAI has guessed a ComfyUI folder and is about to open Settings so
  they can confirm it.
- Controls: Single **OK** button (`QMessageBox.information`).
- Notes: Shown only when `settings.json` has no `first_run_done` key, or it
  is `False` (`EasyAI.py: main()`). EasyAI guesses the ComfyUI directory
  (`app/comfy/launcher.py: default_comfyui_dir()`) and also pre-selects a
  start-file guess if the current one isn't among the folder's launchers.
  Immediately followed by opening the Settings dialog; `first_run_done` is
  set `True` and saved regardless of whether the user changes anything.
- Screenshot: the message box itself, on first launch (requires a
  fresh/reset `settings.json`).

### Settings

- Purpose: Configure where ComfyUI lives and how it's started, where
  workflows/results are stored, generation defaults, and the app language.
  The ComfyUI section self-tests live.
- Reached via: `File → Settings…` (`Ctrl+,`), or automatically on first run.
- Controls (three group boxes, `app/ui/settings_dialog.py`):
  - **The AI engine (ComfyUI)**
    - "ComfyUI folder" — text field + **Browse…** (folder picker).
    - "Start file" — editable combo box, populated with every launcher file
      found in the chosen folder, re-scanned as the folder changes.
    - "Address" — text field, `host:port` of the ComfyUI server. Default
      `127.0.0.1:8188`.
    - "Start ComfyUI automatically when EasyAI opens" — checkbox.
    - "Close ComfyUI when EasyAI closes" — checkbox; tooltip clarifies it
      only ever closes a ComfyUI **EasyAI itself started**.
    - Live status line ("ComfyUI is answering at …" / a validation problem)
      + **Test now** button; re-probes as the folder/launcher/address change.
  - **Folders**
    - "Workflows" — path + Browse. Default: relative `workflows` folder
      beside EasyAI (kept relative so a moved/copied EasyAI keeps using the
      folders beside it; only becomes an absolute path if the user browses
      outside EasyAI's own folder — `app/config.py: set_folder`).
    - "Results" — path + Browse. Default: relative `output` folder, same
      rule.
  - **Creating**
    - "Shape to start with" — dropdown of ratio strings from
      `app/ratios.py: RATIO_ORDER`.
    - "Give up after" — spin box, 60–14400 s, step 60 (job timeout).
    - "Wait for the engine up to" — spin box, 30–1800 s, step 30 (launch
      timeout).
    - **Language** — dropdown: English, 繁體中文 (zh-Hant), 简体中文
      (zh-Hans) (`i18n.available()`). Tooltip: "Changes the whole window as
      soon as you press Save."
  - **Save** / **Cancel** (standard dialog buttons).
- Notes: Saving applies the chosen language immediately (no restart) by
  retranslating the whole main window (`MainWindow.retranslate()`), and
  remembers the language *outside* EasyAI's own settings.json (in
  `app/i18n.py`'s own store) so EasyAI Setup and Studio open in the same
  language independently. If the ComfyUI folder, start file, server address,
  or workflow folder changed, the client cache is cleared, a new launcher is
  built (adopting any ComfyUI process the old launcher had started, so it
  can still be stopped), folders are re-created, and the engine is
  re-checked. Closing Settings always reloads all workflow lists.
- Screenshot: the full Settings dialog, ideally with the ComfyUI status line
  showing a live "answering" state.

### Startup Splash ("Starting the AI engine…")

- Purpose: Modal progress dialog shown while EasyAI looks for/starts
  ComfyUI, so a beginner never sees a frozen window.
- Controls: Indeterminate progress bar, status text line, **Carry on
  without it** button.
- Notes: Skipped entirely if ComfyUI is already answering when EasyAI
  starts. Otherwise `EngineWorker` polls `/system_stats` and, if "Start
  ComfyUI automatically" is on, launches the configured start file with no
  console window, polling until it responds or the launch timeout is hit.
  "Carry on without it" cancels the wait and opens the main window anyway.
  If the engine still fails to start, a warning dialog explains the app is
  still usable for browsing but not for creating.
- Screenshot: the splash mid-launch (timing-sensitive; may need a slowed
  ComfyUI start to capture reliably).

### Main Window (shell)

- Purpose: The application shell — an engine status strip, five tabs (Image,
  Video, Prompt Helper, Queue, Read a prompt), menu bar and status bar.
- Controls:
  - Menu bar: **File**, **AI engine**, **Help**.
  - Engine status strip (above the tabs): `engine live · 127.0.0.1:8188` /
    `engine down · …` pill, plus a workflow-count readout ("N workflows
    loaded").
  - Tab bar, in order: 🖼 **Image**, 🎬 **Video**, ✨ **Prompt Helper**,
    📋 **Queue**, 🔎 **Read a prompt**.
  - Status bar: VRAM readout (`VRAM used / total GB`, only once the engine
    is alive and a device is reported), a coloured dot + "AI engine:
    running/not running" text, and a transient status message area.
- Keyboard shortcuts:
  - `Ctrl+Enter` / `Ctrl+Return` — runs the Create button of whichever tab
    is in front (only if enabled/visible).
  - `Ctrl+,` — Settings.
  - `F5` — "AI engine → Check it is running".
  - `Ctrl+R` — "AI engine → Reload workflows".
- Menus:
  - **File**: Open the workflows folder · Open the results folder ·
    Settings… (`Ctrl+,`) · Quit.
  - **AI engine**: Check it is running (`F5`) · Start it now · Reload
    workflows (`Ctrl+R`).
  - **Help**: How do I add a workflow? · About EasyAI.
- Notes: Window geometry remembered between sessions (`window_geometry`).
  On close, EasyAI checks for outstanding queue items first (see **Queue**
  below) before any shutdown happens; running jobs are then cancelled, the
  engine-start worker stopped, and — if "Close ComfyUI when EasyAI closes"
  is on — any ComfyUI process EasyAI started is force-killed via a Windows
  Job Object safety net that also triggers if EasyAI crashes or is killed
  from Task Manager.
- Screenshot: the whole window on first successful launch, engine live,
  Image tab in front, with at least one workflow loaded.

### Image tab / Video tab / Prompt Helper tab (the "Generate" screen)

These three tabs share one implementation, `GenerateTab` in
`app/ui/tab_base.py`; only labels/visibility differ per mode
(`app/modes.py`). Documented once, differences called out.

- Purpose: Pick a workflow ("style"), describe what to make, attach any
  needed files, adjust size/detail/length/model, and generate a result.
- Layout: three columns in a `QSplitter` — workflow list (left) · prompt +
  inputs + options + Create (middle) · live preview + results gallery
  (right; Prompt Helper replaces the picture preview with a text result box
  + history list).
- **Middle column scrolls; Create does not.** The prompt, negative-prompt
  box, drop zones, model picker, shape/detail/length row and switches all
  sit inside a `ColumnScroll` (`app/ui/widgets.py`), so a workflow that
  needs a negative prompt, a model choice, up to ten reference pictures,
  shape, detail and length at once still fits. Below the scroll area, in a
  fixed footer, sit: **Create**, **Stop**, the "CTRL + ENTER to run" hint,
  the progress bar, and the status/hint line — always visible regardless of
  scroll position (`GenerateTab._build_middle`).
- Controls (left column — "Choose a style"):
  - Scrollable list of workflow cards (`WorkflowList`), each with a coloured
    status dot (green/amber/red) and a short status line (`READY · <ENGINE>`,
    `CHECK SET UP`, `MISSING ADD-ON`, `MISSING MODEL`, `NOT CHECKED`,
    `NEEDS RE-EXPORT`).
  - **Refresh** button — rescans the workflow folder and re-runs the
    pre-flight check against the live ComfyUI.
  - **Set up…** button — opens the Manifest Editor for the selected
    workflow.
  - Right-click context menu on a workflow: **Rename…** (`F2`), **Set
    up…**, **Show the file**.
  - Double-click a workflow → same as Rename.
  - Empty-state message when no workflows exist, naming the exact folder to
    drop exported API `.json` files into, then press Refresh.
- Controls (middle column, top to bottom):
  - Prompt label + live character counter, multi-line prompt box (hint text
    differs per mode). Disabled with an explanatory placeholder if the
    workflow takes no prompt at all.
  - **Negative prompt box** (`_show_negative`) — labelled **NEGATIVE
    PROMPT**, shown only for workflows whose manifest binds a `negative`
    input, and pre-filled with that workflow's own text (e.g. Anima/LTX ship
    long built-in negatives). Leaving it untouched sends the workflow's own
    text; editing changes it; emptying it sends no negative prompt at all.
    Hidden entirely for workflows without one.
  - Drop zones (0–14, dynamic, scrollable, `DropZone` widgets in a nested
    scroll area) — one labelled click-or-drag file box per file input the
    selected workflow's manifest exposes: up to 10 picture slots plus 2
    audio and 2 video slots. Shows only 3 at a time before scrolling, so the
    prompt and Create stay reachable even for a 10-picture workflow. Each
    shows a thumbnail/icon, filename, and a Remove control once a file is
    chosen. Label text and "(optional)" suffix come from the manifest. A
    hint line ("Attach only the ones you want — leave the rest empty.")
    appears only when some slots are optional.
    - **Reference groups that grow (Qwen Image 2.1)** — for a workflow whose
      encoder takes an auto-growing image group (`images.image_1` …
      `image_16`) and wires exactly one picture, EasyAI adds up to 10 slots
      labelled **Reference 1**–**Reference 10** at load time
      (`app/workflows/grow.py`). The slots are revealed **one at a time**:
      filling one shows the next; emptying one in the middle shifts the
      rest up, so filled slots are always a gap-free run starting at
      Reference 1 (`tab_base._reveal_next`). A group whose author wired
      several pictures deliberately (e.g. MiniMax H3, three wired) is left
      as-is and not grown.
  - **Model picker** (`ModelPicker`) — appears only when the selected
    workflow's model folder holds more than one file of the same family
    (same subfolder); shows a **MODEL** heading + dropdown, with the
    workflow's own model marked "(in the workflow)". Choice remembered per
    workflow name (`model_choice` setting); the workflow file itself is
    never rewritten. Hidden when the family has only one model.
  - Shape / Detail / Length / batch row:
    - **Shape** picker (`RatioPicker`) — row of ratio chips (from
      `app/ratios.py`), filtered to whatever the workflow's own size-chooser
      node supports if it has one, with the real pixel size shown below in
      monospace. Hidden on Prompt Helper. Greyed out with a reason tooltip
      when the workflow takes its size from a supplied picture instead.
    - **Detail** picker (`MegapixelPicker`) — slider, 0.5–2.0 MP (a
      workflow's own lower built-in value can extend the floor below 0.5),
      shown only for workflows exposing a `megapixels` binding or
      recomputable width/height. Live "N.N MP" label plus a hint/warning
      line; warn threshold differs per tab (1.5 Image / 1.0 Video, since
      video pays the pixel cost per frame).
    - **Length** picker (`DurationPicker`, Video only) — spin box in
      seconds (2–15 default range, configurable), shows the computed frame
      count underneath, amber caution above the warn threshold (default 8 s)
      about graphics-memory exhaustion.
    - **How many** spin box (batch count), 1–16, shown only when the
      workflow's manifest exposes a batch/count binding.
  - **"Let the AI improve my wording first"** switch — shown only when the
    workflow has a detected prompt-enhancer node and the mode does not
    itself produce text (never shown on Prompt Helper, where the whole
    workflow is the enhancer). Defaults on, remembered
    (`use_prompt_enhancer`). Turning it off is real graph surgery: EasyAI
    rewires everything that read from the enhancer node to whatever fed it,
    then deletes the node, so the language model never loads.
  - **"Repeat the same result, so I can compare changes"** switch (seed
    lock) — shown only when the workflow exposes a seed binding. Reuses a
    seed remembered per-tab (`locked_seeds`) instead of randomising.
  - *(fixed footer, does not scroll)* **Create <Mode>** button — "Create
    Image", "Create Video", or "Improve my prompt" on Prompt Helper. Video's
    button has a tooltip warning that video takes much longer.
  - **Stop** button — replaces/accompanies Create while this tab's item is
    running; cancels via the queue (only this tab's running item — other
    queued items are unaffected).
  - "CTRL + ENTER to run" hint label.
  - Progress bar (0–100%) and status/hint line, updated with live progress;
    after completion shows elapsed time, output size, file count, seed, and
    any notes (e.g. a shape the workflow could not honour) in amber.
- Controls (right column):
  - **Image/Video**: live preview pane (ComfyUI's in-progress preview
    frames, then the finished result). **Video previews show the finished
    video's first frame**, with size and length, in both the preview pane
    and the Results gallery thumbnails, so several takes can be told apart
    at a glance; double-click still opens the video in the OS player.
  - **Results** gallery underneath — thumbnails of everything generated
    this session (newest first), **Open folder** button, double-click opens
    in the OS default viewer/player.
  - **Prompt Helper tab** (replaces preview+gallery): "THE IMPROVED PROMPT"
    heading, read-only result text box, **Copy** button, a "Send to
    <Image/Video>" dropdown + **Go** button (drops the text into the chosen
    tab and switches to it), and a history list of past results
    (`output/prompt-enhancer/*.txt`) that reloads a past result when
    clicked. Every result is also saved as a `.txt` file.
- Notes / default-state and error behaviour:
  - Selecting a workflow whose manifest is "ambiguous" (low-confidence
    auto-detect) shows "EasyAI had to guess how this workflow works. Press
    'Set up…' to check it." in the status line.
  - A workflow missing a required custom node or model file is greyed out
    and unselectable; its status text names the exact add-on or model
    filename to install.
  - Create with the engine not running → warning dialog directing to
    Settings → Start engine.
  - Create with a required file input empty → "Almost there" info dialog
    listing what's missing, rather than sending a broken job.
  - Every press of Create adds an item to the shared queue rather than
    running immediately (see **Queue** tab below); the tab still tracks and
    displays only the items it added.
  - Seeds randomised every run unless "repeat the same result" is on;
    remembered separately per tab.
- Screenshots: Image tab with a workflow selected and a result showing;
  Video tab showing the Length control and a first-frame video preview; the
  negative-prompt box expanded; a workflow with several Reference N slots
  revealed; the Model picker visible; Prompt Helper tab with a result and
  history list.

### Queue tab

- Purpose: Shows everything queued or running across all three Create tabs
  (one GPU, so items run one at a time, in order), plus everything made this
  session.
- Reached via: 📋 **Queue** tab, always present (not a Mode, added directly
  by `MainWindow`, `app/ui/queue_tab.py`).
- Controls:
  - Heading "QUEUE" + a live count ("1 running · N waiting").
  - **Clear finished** button.
  - The queue list: one row per item, each showing a state mark (⏳ waiting,
    ▶ running, ✓ done, ! failed, ✕ cancelled), which tab/mode it belongs to,
    the workflow name, a one-line summary, a per-item progress bar (visible
    only while running), and a **✕** button to cancel that item (hidden once
    finished).
  - Empty-state hint: "Nothing queued. Press Create on any tab and it will
    appear here."
  - **"Close EasyAI when the queue is empty"** switch — session-only
    (`close_when_queue_empty` is reset to its default at every startup, see
    `app/config.py: SESSION_ONLY`); pairs with "Close ComfyUI when EasyAI
    closes" to free the GPU too.
  - "EVERYTHING MADE THIS SESSION" heading + a `ResultGallery` of every file
    produced by any queue item this session.
- Notes:
  - Each queued item snapshots the prompt, shape, files and model as they
    were when Create was pressed, so editing the prompt for the next item
    cannot disturb one already waiting.
  - **Close-when-queue-empty behaviour**: when the queue empties and the
    switch is on, `MainWindow._on_queue_empty` → `_start_closing_countdown`
    shows a message box ("Everything in the queue is finished.") that counts
    down for **20 seconds** (`MainWindow.CLOSE_COUNTDOWN`), with **Stay
    open** (default button) and **Close now**. Choosing Stay open turns the
    switch back off (both in settings and on the Queue tab) so the next
    finish doesn't ask again.
  - **Exit guard while the queue is busy**: `MainWindow.closeEvent` checks
    `queue.unfinished()`; if anything is running/waiting and the window is
    not already closing "for good", it shows a "Still working" warning
    naming how many are running/waiting, with **Keep working** (default,
    switches to the Queue tab) and **Cancel everything and close**
    (destructive).
- Screenshot: Queue tab with at least one running item (progress bar
  visible) and one waiting item, plus the close-when-empty switch and the
  results gallery below it. Optionally the 20-second countdown dialog and
  the "Still working" exit-guard dialog.

### Read a prompt tab

- Purpose: Recover the prompt that made an existing picture or video, by
  reading the file itself — works with ComfyUI closed.
- Reached via: 🔎 **Read a prompt** tab, always present (`app/ui/read_tab.py`).
- Controls:
  - Left column: a **DropZone** labelled "Picture or video" (drag-drop or
    click-to-browse, accepts image and video extensions), and a
    `ResultGallery` of recent results across all three output folders
    (Image/Video/Prompt Helper), newest first.
  - Right column: a `PreviewPane` (shows the picture, or the video's frame);
    a source/attribution line under it; "THE PROMPT" heading; a read-only
    multi-line prompt box; **Copy**, **Use this in Image**, **Use this in
    Video** buttons (all disabled until a prompt has been read).
- Behaviour:
  - Dropping/selecting a file shows its preview immediately, then attempts
    to extract the embedded prompt (`app/prompts.py: read_prompt`), matching
    the file to one of the user's own workflows by filename, then by graph
    shape, so the *right* text box is picked when a graph has several (one
    Krea workflow has three text boxes, one being a system instruction for
    the prompt enhancer).
  - If matched to a known workflow: source line reads "from {workflow
    name}" in a neutral colour.
  - If not matched to any known workflow (a stranger's file): source line
    reads "best guess — this workflow is not one of yours", shown in the
    warning colour.
  - If the file carries no embedded prompt at all (re-saved, sent through a
    messaging app, or downloaded from most websites): "This file does not
    carry a prompt…" message, Copy/Use buttons stay disabled.
  - If the file no longer exists on disk: "That file is no longer there."
  - **Use this in Image / Use this in Video** sends the text to that tab's
    prompt box and switches the main window to it.
  - The tab reloads its workflow list and results gallery from disk every
    time it is shown (`showEvent` → `refresh()`), so new results and
    workflows appear without restarting EasyAI, and a file deleted in
    Explorer disappears from the list.
- Screenshots: Read a prompt tab with a picture dropped in and its prompt
  shown ("from <workflow>"); the same with a "best guess" result; the
  "does not carry a prompt" state.

### Set up… (Manifest Editor)

- Purpose: Correct EasyAI's automatic guesses about which ComfyUI nodes/
  inputs a workflow's prompt, negative prompt, shape, seed, length, batch,
  enhancer, output name, and file slots map to, without hand-editing JSON.
- Reached via: **Set up…** button on a tab, right-click → **Set up…** on a
  workflow, or when a workflow is flagged "CHECK SET UP".
- Controls (`app/ui/manifest_editor.py`):
  - Explanatory blurb, plus (if applicable) a warning line naming which
    bindings were guessed.
  - "This workflow" group: **Name shown in the list** field, **Model
    family** dropdown ("Work it out automatically" or a named engine from
    `app/ratios.py`) — decides which pixel-size table is used per shape.
  - One row per binding key — Prompt, Negative prompt, Width, Height, Shape
    chooser, Total pixels, Seed, **Prompt improver**, Length, How many,
    Output name, plus one row per file slot (labelled "Picture N" / "Sound
    N" / "Video N" with an editable display name). Each row: a node
    dropdown (every node id + class + title in the graph), an input
    dropdown (only that node's real inputs), and a "+ also set another
    node" link (needed e.g. when one value must be written to two nodes at
    once, like Flux 2's width feeding both a latent node and its scheduler).
  - Dialog buttons: **Save**, **Cancel**, **Guess again** (relabelled Reset
    — re-runs autodetection and discards edits in the open dialog, without
    saving until Save is pressed).
- Notes: Saving marks the manifest as no longer a guess and writes it to
  `<workflow>.manifest.json` next to the workflow file. The manifest is
  generated automatically the first time a workflow loads and is **never
  auto-regenerated** afterward, so manual corrections persist across
  re-exports of the workflow.
- Screenshot: the Manifest Editor open for a workflow with several file
  slots and at least one "guessed" warning visible.

### Rename dialog

- Purpose: Give a workflow a friendly display name.
- Reached via: `F2` on the selected workflow, double-click a workflow, or
  right-click → **Rename…**.
- Controls: Standard text-input dialog, pre-filled with the current name,
  OK/Cancel.
- Notes: Name is stored in the manifest, not the filename — re-exporting the
  same workflow from ComfyUI keeps the custom name; clearing it falls back
  to the filename.

### Help → How do I add a workflow?

- Purpose: In-app quick reference for the workflow-authoring steps (Export
  (API), save into the right folder, press Refresh).
- Controls: Info message box, OK to dismiss.
- Notes: Names the current workflow folder path live from settings.

### Help → About EasyAI

- Purpose: Version/credits and a reminder of where workflows and results
  are stored.
- Controls: Standard About dialog, OK to dismiss.
- Notes: Shows `VERSION_LABEL` ("v1.1.0") and the current workflow/output
  folder paths.

---

### EasyAI Setup (separate program: `EasyAISetup.py` / `EasyAISetup.bat`)

- Purpose: One scrollable screen, read top to bottom, that installs a
  pinned ComfyUI version plus the specific add-ons and model files EasyAI's
  bundled workflows need — or updates a ComfyUI the user already has to the
  tested version.
- Controls (`setup/ui.py: SetupWindow`), top to bottom:
  - Title + **Language** dropdown (top-right, so a user in the wrong
    language can still find it).
  - Explanatory blurb naming the ComfyUI version being installed.
  - **Install list row** — a mono label showing where the active install
    list came from ("Install list: built into EasyAI Setup" or a path to
    `setup-settings.json`), an **Open the install list** button (opens the
    JSON in the user's editor), a **Reload** button (re-reads it after
    editing), a status note (created / refreshed / unwritable / outdated —
    an outdated-and-edited file is kept as-is but flagged, listing what
    it's missing), and — only when the list is outdated and edited — a
    **Use this Setup's list instead** button (replaces it, keeping the old
    one as `setup-settings.old.json`).
  - **WHERE TO PUT IT** — folder path field (default
    `~/EasyAI-ComfyUI`) + **Browse…**, and a free-disk-space readout.
    Picking the portable ComfyUI folder itself in the browser resolves to
    its parent.
  - **Found-ComfyUI line** — appears once a folder is typed/chosen, saying
    one of: "ComfyUI {version} will be installed here" (empty folder); "✓
    ComfyUI {version} is already here, with EasyAI's add-ons at their
    tested versions" (nothing to do); or "Found ComfyUI {installed} here.
    EasyAI is tested with {version}." (older/newer/mismatched add-ons) —
    together with an **update switch**, e.g. "Update ComfyUI and EasyAI's
    add-ons to the tested versions ({version})" or, if the found ComfyUI is
    *newer* than the tested version, "Change ComfyUI from {installed} back
    to {version}…". The switch defaults on for an older install and off for
    a newer one (assumed to be a deliberate choice), but only the first time
    a given folder is scanned — the user's own toggle is never overridden
    afterward.
  - **WHAT TO INCLUDE** — one **group card** per install group (`GroupCard`:
    a Switch-styled toggle + model/add-on counts + "to copy in by hand"
    count where applicable + that group's own download size).
  - **"Download the models ComfyUI does not have yet"** switch — on by
    default; tooltip explains models already in ComfyUI (wherever it keeps
    them, via its own `folder_paths`) are never re-downloaded; turning it
    off installs/updates ComfyUI and add-ons only.
  - **Running total** line — e.g. "Image + Video — 12 of 17 models already
    in ComfyUI, 5 to download, 42 GB in all" (or "Nothing chosen" / "models
    not downloaded, N GB to download" depending on switches).
  - **Free-space** readout line.
  - **Warning lines** (amber) — insufficient disk space; models with no
    automatic download link (listed by name, to copy in by hand); models
    needing an account/licence (naming HuggingFace and/or Civitai
    specifically).
  - **Good-news line** (green) — "No account needed — N models that would
    normally ask you to sign in come straight from the EasyAI server", when
    applicable.
  - **HuggingFace key** / **Civitai key** — password-style fields, used
    only for the current run, never saved to disk; needed only for models
    behind a login the mirror can't cover.
  - **Install** button (primary); **Stop** button (shown mid-install,
    finishes the current file then stops); progress bar; current-file/
    speed/ETA label; scrolling log text box.
- Notes:
  - Clicking Install shows a confirmation dialog stating the exact download
    size, destination, and a bullet-point plan (install/move ComfyUI
    version, bring N add-ons up to date, download N models) before starting.
  - Every step is resumable — stopping, or losing the connection, and the
    next run "picks up where it left off," part-finished downloads
    included.
  - On success, either tells the user EasyAI is already configured to use
    the new ComfyUI, or (if EasyAI's settings couldn't be written) gives the
    exact folder path to paste into EasyAI's own Settings, and opens that
    folder in Explorer.
  - What gets installed is fixed by `setup/catalog.json`
    (`setup-settings.json` when present) — never "whatever is newest" — so
    a viewer can't end up with an untested combination.
  - The update path never runs `git checkout` blindly: it changes only
    files that differ between versions, refuses to overwrite the user's own
    edits, keeps the previous state as a git branch
    (`easyai-before-<date>`), and restores a replaced add-on automatically
    if its new version fails to install.
- Screenshot: the full Setup window with a folder chosen that has an older
  ComfyUI in it (so the found-ComfyUI line + update switch are both
  visible), at least one group card ticked, and the running total/warnings
  visible.

### EasyAI Studio (separate program: `EasyAIStudio.py` / `EasyAI Studio.bat`)

- Purpose: The publishing/maintainer tool — not for viewers. Adds new
  workflows to the shipped set, rebuilds the install catalogue, checks
  download links, and tracks translation coverage.
- Layout: a left-hand navigation rail (title "EasyAI Studio", subtitle
  "Publishing tools", a language dropdown at the bottom) and a stacked pages
  area on the right (`studio/ui.py: StudioWindow`) — a workbench, not a
  top-to-bottom sequence like Setup.
- Pages, in rail order:
  1. **Add a workflow** (`setup/authoring_ui.py: AuthoringView`)
     - Purpose: package an already-working workflow so EasyAI Setup can
       install its models for everyone else.
     - Controls: a workflow-file field + **Browse…** (only `.json`); a
       "Goes in" group dropdown (Image / Video / Prompt Helper — auto-
       guessed from the filename, e.g. containing "video"/"i2v" or
       "prompt"/"enhanc", but always left changeable); **Have a look**
       button; a read-only report box (node count, what EasyAI can drive,
       every model found with its folder/size/download-link status, marked
       "new" vs already-known, plus any add-ons needed); a warning line
       naming any models with no download link (with the fix: add them to
       `setup/url_overrides.json`); an **Add to EasyAI** button, enabled
       only once the analysis says the workflow can safely be added.
     - Notes: ComfyUI must be running for "Have a look", since only the
       live engine can say which folder each model belongs in and which
       nodes are add-ons vs. built in. Download links are read from
       ComfyUI's own bundled templates, including inside subgraphs. "Add to
       EasyAI" copies the workflow in and adds its models to the catalogue;
       if any model needs uploading to the maintainer's own mirror server,
       the dialog lists exactly what to upload and reminds to run
       `python tools/verify_urls.py` afterward.
  2. **Catalogue** (`studio/pages.py: CataloguePage`)
     - Purpose: rebuild `setup/catalog.json` (what EasyAI Setup installs)
       from this machine.
     - Controls: a summary line (ComfyUI version, model/add-on counts,
       total size, how many are mirrored/gated/have-no-link); **Update
       folders and mirrors** button (`make_catalog --mirrors-only`);
       **Rebuild everything** button (primary, full `make_catalog` run); a
       scrolling log.
     - Notes: ComfyUI must be running for a full rebuild.
  3. **Download links** (`studio/pages.py: LinksPage`)
     - Purpose: verify every model's download link still resolves and still
       serves the same file (not just a link that resolves to the wrong
       build).
     - Controls: a summary line (links to check / from the maintainer's own
       mirror / needing an account); **Check the original sources** button
       (primary, `verify_urls`); **Check my mirror** button
       (`verify_urls --mirror`); a scrolling log.
  4. **Languages** (`studio/pages.py: LanguagePage`)
     - Purpose: find text added to the programs since the translations were
       last updated, for zh-Hant and zh-Hans.
     - Controls: a summary line ("繁體中文 N/M · 简体中文 N/M" translated
       count, or "English only"); **What is missing?** button (`make_lang`,
       report-only); **Add new text to the language files** button
       (primary, `make_lang --write` — adds new keys with empty values,
       ready to translate); a scrolling log.
- Notes: All four pages share the same `ToolPage` shape — heading, blurb,
  a state-summary line, one or more buttons, and a log — because each wraps
  a console script in `tools/`.
- Screenshots: Studio window on the "Add a workflow" page with a report
  populated; the Catalogue, Download links and Languages pages each showing
  their summary line and an example log.

## Settings Reference

All keys live in `settings.json` at the app root (or beside the built
`.exe`), managed by `app/config.py`. Defaults shown below; unknown keys are
preserved and missing keys fall back silently, so upgrades never wipe an
existing file. `close_when_queue_empty` is special-cased to always reset to
its default (`False`) at the start of every run (`SESSION_ONLY`), regardless
of what was saved.

| Key | Default | Controls |
|---|---|---|
| `comfyui_dir` | `C:\AI ComfyUI - New Version\ComfyUI_windows_portable` | Folder where ComfyUI is installed. |
| `comfyui_launcher` | `run_nvidia_gpu.bat` | Which start file inside that folder starts ComfyUI. |
| `comfyui_server` | `127.0.0.1:8188` | Address EasyAI talks to ComfyUI on. |
| `auto_launch` | `true` | Whether EasyAI starts ComfyUI itself when nothing answers. |
| `launch_timeout` | `300` (seconds) | How long to wait for ComfyUI to come up before giving up. |
| `job_timeout` | `1800` (seconds) | How long a single generation may run before EasyAI gives up on it. |
| `stop_engine_on_exit` | `true` | If EasyAI started ComfyUI, close it again when EasyAI exits. |
| `close_when_queue_empty` | `false` | "Close EasyAI when the queue is empty" switch on the Queue tab. Session-only — always reset to `false` on startup, however it was last saved. |
| `workflow_dir` | `workflows` (relative to EasyAI's own folder) | Folder holding the per-mode workflow subfolders. |
| `output_dir` | `output` (relative to EasyAI's own folder) | Folder results are saved into (per-mode subfolders). |
| `default_ratio` | `2:3` | Shape chip pre-selected for a freshly picked workflow. |
| `lock_seed` | `false` | Whether "repeat the same result" starts ticked. |
| `locked_seeds` | `{}` | Last/locked seed per mode key, so locking one tab doesn't reuse another tab's seed. |
| `model_choice` | `{}` | Chosen alternate model per workflow name (Model picker), remembered without rewriting the workflow file. |
| `batch_count` | `1` | Default value of the "How many" spin box. |
| `use_prompt_enhancer` | `true` | Default state of "Let the AI improve my wording first". |
| `image_megapixels` | `1.0` | Remembered Detail value for the Image tab, used when a workflow states no budget of its own. |
| `video_megapixels` | `0.5` | Same, for Video. |
| `image_megapixels_warn` | `1.5` | Detail value above which the Image tab shows a memory-cost warning. |
| `video_megapixels_warn` | `1.0` | Same, for Video (lower — video pays the cost per frame). |
| `video_length_default` | `5` (seconds) | Length box's starting value. |
| `video_length_min` / `video_length_max` | `2` / `15` | Range of the Length spin box. |
| `video_length_warn` | `8` (seconds) | Above this, the Length control shows a memory-exhaustion caution. |
| `language` | `en` | Present as a default key; the actual current language is tracked separately by `app/i18n.py` (shared across EasyAI/Setup/Studio), not read directly out of this file — see Needs Confirmation for the exact storage location. |
| `theme` | `dark` | Present as a default key; no theme-selection control was found anywhere in the UI — see Needs Confirmation. |
| `window_geometry` | `""` | Serialized (hex) Qt window geometry, restored on next launch. |
| `first_run_done` | `false` | Whether the first-run welcome/Settings flow has already been shown. |

**Per-workflow manifest files** (`<workflow>.manifest.json`, one per
workflow, auto-generated once and never overwritten by re-scans) separately
store: display name, model family/engine override, and node/input bindings
for prompt, negative, width, height, ratio, megapixels, seed, enhancer,
length, batch, output, and each file slot — plus custom display labels for
file slots, which reference-group slots EasyAI added ("grown" groups, so
they persist with ComfyUI closed), and an `ambiguous`/`autodetected` flag
pair driving the "CHECK SET UP" status and the manifest editor's warning
banner.

**EasyAI Setup's `setup-settings.json`** (beside `EasyAISetup.exe`/`.py`,
written out the first time Setup runs, from then on read instead of the
built-in list) — editable install list:
- `folders.models` — where models go; empty means the models folder of the
  ComfyUI being installed, a full path (forward slashes only) puts every
  model there and updates ComfyUI's `extra_model_paths.yaml`.
- `folders.workflows` — where EasyAI's workflows are installed to.
- `models.<path>` — per model: `kind` (which ComfyUI models subfolder),
  `from` (download URL), `mirror` (tried first if set), `to` (optional
  dedicated subfolder).
- `groups.<key>.workflows` — per workflow: `file` (`built-in`, a relative/
  absolute path, or a web link) and `to` (destination subfolder or full
  path); a workflow's manifest and thumbnail travel with it.
- An old file that predates the current built-in list and was never
  user-edited is silently replaced; one that was edited is kept, with a
  warning naming what it's missing and an offer to switch, keeping the old
  copy as `setup-settings.old.json`.

## Known Quirks / Troubleshooting Signals

- **Workflow status dots/labels** in the left-hand list are the primary
  troubleshooting surface: `READY · <ENGINE>` (green), `CHECK SET UP` /
  `NOT CHECKED` (amber — manifest guessed, or engine not yet queried),
  `MISSING ADD-ON` / `MISSING MODEL` (red — a required custom node or model
  file is absent from the connected ComfyUI), `NEEDS RE-EXPORT` (red — the
  file is not a valid ComfyUI API export; a plain "Save" in ComfyUI's editor
  is explicitly *not* sufficient, only Workflow → Export (API) is).
- **Engine pill / status bar dot** (`engine live` vs `engine down`, plus a
  red/green dot and "AI engine: running/not running" text) is the first
  thing to check per the README's own framing.
- **VRAM readout** in the status bar only appears once the engine is alive
  and reports a device; meant to explain out-of-memory failures.
- **"ComfyUI closed straight away."** — shown if the launched start file
  exits immediately (bad install), with a suggestion to run it by hand to
  see the real error. (Needs Confirmation: exact wording not located in
  this pass — see below.)
- **"The AI engine did not start within N seconds."** — launch-timeout
  failure.
- **"EasyAI cannot reach ComfyUI"** warning when Create is pressed with no
  live connection.
- **"Almost there"** dialog when required files are missing at Create time.
- **"The workflow finished but produced no text."** (Prompt Helper) — the
  workflow needs a Preview Any / Show Text node on its output; otherwise
  there's nothing to read back. (Needs Confirmation: exact wording.)
- **"This file does not carry a prompt"** on Read a prompt — a picture that
  has been re-saved, sent through a messaging app, or downloaded from most
  websites has lost the information ComfyUI embeds.
- **"best guess"** label on Read a prompt (shown in the warning colour) when
  the file can't be matched to one of the user's own workflows.
- **Embedded "last run" prompt is deliberately distrusted** unless every
  loader/save node in it matches the visible graph — the copy ComfyUI
  embeds survives "Save As", so a first-and-last-frame workflow can be
  carrying the graph of the plain text-to-video workflow it was built from.
- **Optional file inputs**: a file slot is only treated as skippable when
  the connected ComfyUI's node metadata marks *every* input a loader feeds
  as optional; with no ComfyUI to ask, nothing is treated as optional.
  Running a reference workflow with **no** files attached is refused
  regardless ("Please attach at least one file to work from.").
- **Reference-group gap prevention**: emptying a middle Reference slot
  shifts the rest up rather than leaving a gap, because the node numbers
  pictures by position and a gap would silently renumber a later picture.
- **Detail (megapixels) floor**: a workflow authored below the normal
  0.5–2.0 range keeps its own lower floor rather than being silently
  rounded up.
- **Output size vs. requested size**: LTX-family engines floor both
  dimensions to a multiple of 64 after the chosen size is computed
  (`app/ratios.py: output_grid`), so the actual file can differ slightly
  from the shape chip's label.
- **Prompt-enhancer toggle** is real graph surgery (rewire + delete the
  enhancer node), not a client-side flag.
- **`close_when_queue_empty`** only ever applies for the current session —
  it is deliberately forgotten at every startup so a viewer doesn't get an
  unexplained auto-close on some future night when they forgot it was set.
- **Queue exit guard**: closing the main window with queued/running items
  present blocks the close (unless already confirmed) and offers to keep
  working or cancel everything.
- **`stop_engine_on_exit`** only ever closes a ComfyUI process EasyAI
  itself launched; one started manually, or on another machine, is left
  alone.
- **EasyAI Setup's update path is conservative by design**: it never force-
  checks-out a linked/external `models` folder, keeps a git-branch backup of
  the prior state, and restores a replaced add-on automatically if the new
  version fails.
- **First run guess** of the ComfyUI folder may be wrong; the welcome
  dialog exists specifically so the user checks/corrects it via "Test now"
  before relying on it.

## Needs Confirmation

- **Exact user-facing error text** for a handful of messages referenced in
  the README and code comments but not located verbatim in the files read
  this pass: "ComfyUI closed straight away.", "The workflow finished but
  produced no text.", "The AI engine finished but produced no files.", and
  the precise NEEDS RE-EXPORT explanation text (all likely live in
  `app/workflows/loader.py` / `app/jobs.py`, not fully read).
- **Where the current language selection is actually persisted.** The
  Settings dialog and `app/config.py` both carry a `language` default key,
  but `app/i18n.py` is described in code comments as keeping its own
  separate store (shared across EasyAI/Setup/Studio) so all three programs
  agree without reading each other's files — the exact file/location for
  that store was not opened this pass.
- **Theme**: `settings.json` has a `theme` default of `"dark"` and
  `app/ui/theme.py` exists, but no dark/light toggle control was found
  anywhere in Settings, Setup, or Studio — worth confirming whether theme
  switching is genuinely user-facing anywhere, or purely fixed/internal.
- **Minimum VRAM / hardware spec**: README states "an NVIDIA graphics card"
  built/tested on 16 GB, but no explicit minimum-VRAM gate or CPU-only mode
  was found in the settings/launcher code beyond VRAM-based UI warnings —
  confirm the practical minimum with the developer before publishing a
  number.
- **EasyAI Setup: the exact wording and full flow of the licence/key
  screen** for gated models (HuggingFace/Civitai key fields) — read at a
  surface level (the fields and warning text), but the deeper behaviour
  around licence-text saving into the install folder was described only in
  the README, not verified against `setup/steps.py` in this pass.
- **Screenshot targets**: this outline was produced from source/README
  reading only; no screenshots have been captured yet (that is the next
  pipeline stage — `docs/manual/_capture.py` reads this file to know which
  screens to capture). Each Screens entry above ends with a suggested
  screenshot target to guide that stage.
