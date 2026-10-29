# EasyAI

**v1.2.1** · Windows · Apache 2.0

A simple desktop front end for ComfyUI, for people who just want to make
something without learning the node graph. Pick a style, type what you want,
choose a shape, press Create.

EasyAI comes as three programs:

| | |
|---|---|
| **EasyAI** | The app itself: Image, Video, Prompt Helper, Queue and Read a prompt tabs |
| **EasyAI Setup** | Installs ComfyUI, the add-ons and the models — or updates a ComfyUI you already have |
| **EasyAI Studio** | The publishing tool: adds new workflows and keeps the install list current |

Viewers need the first two. Studio is for whoever maintains the workflows.

🎥 Walkthrough: [EasyAI on Garion Lab](https://www.youtube.com/watch?v=lB-Q8yuMzv8)

---

## Getting started

1. Download **EasyAI.exe** and **EasyAI Setup.exe** from the
   [Releases page](https://github.com/Garionhk/EasyAI/releases). Neither needs
   Python or anything else installed.
2. Run **EasyAI Setup**, pick a folder and tick what you want — Image, Video,
   Prompt Helper. The window shows the download size before you commit.
3. When it finishes, open **EasyAI**. Setup has already told it where ComfyUI
   is.

Already have ComfyUI? Point Setup at its folder. It says which version it
found, offers to bring it to the version EasyAI is tested with, and downloads
only the models ComfyUI doesn't already have. See
[EasyAI Setup](#easyai-setup).

**You need** Windows and an NVIDIA graphics card. The workflows were built on
a 16 GB card. The model downloads are large — about 104 GB for Image, 138 GB
for Video and 5 GB for Prompt Helper.

### Running from source

```bash
pip install -r requirements.txt
python EasyAI.py
```

Python 3.10 or newer. `EasyAI.bat` does the same with a double-click: it finds
Python, installs the libraries the first time, and opens the app with no
console window. If Python is missing or too old it says so and links the
download. `EasyAISetup.bat` and `EasyAI Studio.bat` start the other two.

---

## What's new in v1.2.1

- **LoRA subfolders** — the LoRA list for MOSS-Image (and any workflow with an
  optional LoRA) now includes the subfolders of the model's LoRA folder, such
  as `Moss\GarionHK_v3\`.
- **The Model and LoRA lists work with ComfyUI closed again** — the saved copy
  they fall back on could be overwritten with an incomplete list.

## What's new in v1.2.0

- **Flux 3 Klein — Image Edit**, with up to **10 reference pictures**. The
  model's own prompt upsampler sees every picture too.
- **MOSS-Image — Text to Image**, with a negative prompt, shape and detail.
- **Layout boxes** — draw where things go and describe each one, for Flux 3
  Klein and MOSS-Image. See [Layout boxes](#layout-boxes).
- **An optional LoRA** for workflows that leave a slot for one — MOSS-Image
  does. Pick one of yours, or None.
- ComfyUI **0.42.1**. EasyAI Setup installs it, or updates an existing
  ComfyUI to it.
- The **Detail** warning now follows the workflow: a workflow built at 2 MP is
  not "bigger than it was built for" at 2 MP.

## What was new in v1.1.0

- **Qwen Image 2.1** — image edit with up to **10 reference pictures**, and
  text to image. Both install with the Image group.
- **Negative prompt box** for every workflow that has one, filled with the
  workflow's own text.
- **Read a prompt** tab — get the prompt back out of any picture or video you
  made.
- **Queue** — press Create as often as you like; everything runs in order.
- **Video previews** — finished videos show their first frame.
- **Choose a model** from the same family, when the folder holds several.
- **Scrolling options** — the middle column scrolls while Create stays put.
- **EasyAI Setup can update** an existing ComfyUI to the tested version, and
  never downloads a model ComfyUI already has.
- **An install list you can edit** — `setup-settings.json`, beside EasyAI
  Setup, says where every model and workflow comes from and where it goes.
- ComfyUI 0.37.0, with add-ons pinned to the versions it was tested with.
- **Chinese** — Traditional and Simplified, alongside English.

---

## The tabs

**Image**, **Video** and **Prompt Helper** each list their workflows on the
left. The middle holds the prompt and options, and the right shows the result.
**Ctrl + Enter** runs the tab you are on.

When a workflow asks for more than fits — a negative prompt, a model, ten
reference pictures, shape, detail and length — the options scroll. **Create**,
**Stop**, the progress bar and the status line stay pinned underneath, so the
button is never out of reach and the result of a run is always in view.

### Queue

Every press of Create adds an item, and items run one at a time, in order.
There is one graphics card, so nothing runs side by side. The **Queue** tab
shows what is running and waiting, with **✕** to cancel any of them, and
everything made this session.

Each item keeps the prompt, shape, pictures and model as they were when Create
was pressed. Changing the prompt to set up the next item cannot disturb one
already waiting.

EasyAI **won't close with work still queued**. It says what is outstanding and
offers *Keep working* or *Cancel everything and close*.

**Close EasyAI when the queue is empty** — set up an evening's work, tick it,
walk away. It applies to **this session only**, and counts down for 20 seconds
with a *Stay open* button before closing. That way a quick job finishing never
makes the window vanish while you're sitting there. If *Close ComfyUI when
EasyAI closes* is on (the default), the graphics card is freed too.

### Read a prompt

ComfyUI writes the graph it ran into every file it saves: a text chunk in a
PNG, a block of JSON in an MP4. Drop a picture or video onto this tab, or click
one of your results, and it shows the prompt that made it, with **Copy** and
**Use this in Image / Video**. It reads the file, not the engine, so it works
with ComfyUI closed.

A graph can hold several text boxes. One Krea workflow has three, one of them
the prompt enhancer's system instruction. So the file is first matched to the
workflow that made it — by filename, then by the shape of its graph — and that
workflow's saved setup decides which box is the prompt. Only a stranger's
workflow falls back to guessing, and it is labelled *best guess*.

A picture that has been re-saved, sent through a messaging app, or downloaded
from most websites has lost this information. The tab says so plainly.

### Video previews

Finished videos show their **first frame**, with size and length, in the
preview and the results lists, so several takes can be told apart at a glance.
Double-click still opens the video in your player.

---

## What it does about the "my nodes broke again" problem

Before a workflow can be selected, EasyAI compares it against the ComfyUI that
is actually running and says what is wrong in plain words:

| | |
|---|---|
| ✅ | Ready |
| ⚠ | EasyAI guessed how this workflow works — worth checking |
| ⛔ | Missing add-on: `ComfyUI-GGUF`, or a model file that isn't downloaded |

A blocked workflow is greyed out with the reason and instructions, instead of
failing halfway through with a stack trace.

It also starts ComfyUI for you (no console window), randomises the seed on every
run, and keeps output at a size the model was trained for.

## Adding a workflow

1. Open the workflow in ComfyUI and check it runs.
2. **Workflow → Export (API)** — a plain Save produces a file `/prompt` rejects.
3. Save it into the folder for its type:

```
workflows/image/    workflows/video/    workflows/prompt-enhancer/
```

4. Press **Refresh** in EasyAI.

Optional: put a picture beside it with the same name (`flux.json` → `flux.png`)
and it becomes the thumbnail.

That is enough for EasyAI itself. To make viewers' **EasyAI Setup** install the
models it needs, add it through [EasyAI Studio](#easyai-studio) instead.

## Naming a workflow

`video_ltx2_5_flf2v - GarionHK` means nothing to a beginner. Rename it to
**First and Last Picture** — select it and press **F2**, double-click it, or
right-click → **Rename…**

The name is stored in the manifest, not the filename, so:

- the exported workflow file is never touched
- re-exporting from ComfyUI keeps the name, even though node numbers change
- clearing the name falls back to the filename

Right-click also offers **Set up…** and **Show the file**.

If you drop in a plain editor file by mistake, EasyAI says so rather than
failing.

**Why an editor file is not enough.** ComfyUI does keep a copy of the last
prompt it ran inside the file, and EasyAI will use that copy when it genuinely
belongs to the workflow. In practice it usually doesn't: the copy survives
"save as", so a first-and-last-frame workflow can be carrying the graph of the
plain text-to-video one it was built from. Running that would quietly make the
wrong thing, so the embedded copy is checked against the workflow you can see —
every loader and save node has to line up — and rejected if it doesn't match.
Of the 200-odd workflows on this machine, not one embedded copy was current.
Export the API file; it is the only reliable route.

## How EasyAI knows what to change

An API workflow is just a bag of nodes — nothing marks "this is the prompt". So
each workflow gets a sidecar next to it:

```
workflows/image/flux2.json
workflows/image/flux2.manifest.json
```

It is generated automatically the first time a workflow is loaded, and **never
regenerated**, so anything you correct by hand stays corrected. Press
**Set up…** to edit it in the app.

Only these are ever touched — everything else in your workflow runs exactly as
you built it:

`prompt`, `negative`, `model`, `width`, `height`, `ratio`, `megapixels`,
`seed`, `length`, `batch`, `output`, and the file slots below.

Details worth knowing:

- **Prompt and negative are told apart by where they go**, not by titles. A
  sampler takes inputs literally named `positive` and `negative`, and the role
  is traced back up the chain. An encoder that feeds *both* — Qwen Image 2.1's
  `TextEncodeQwenImage21` takes `prompt` and `negative_prompt` together — is
  judged input by input rather than labelled negative as a whole.
- **Linked inputs are left alone.** If width is wired from `GetImageSize`,
  EasyAI will not overwrite it — it disables the shape chooser and says the size
  comes from your picture.
- **One key can drive several nodes.** Flux 2 needs the same width in both
  `EmptyFlux2LatentImage` and `Flux2Scheduler`; setting only one produces a
  mis-scheduled sample. The manifest holds a list, and the editor lets you add
  more targets.
- **Files get one slot each.** See below.

## Negative prompt

Workflows that have a negative prompt get a **NEGATIVE PROMPT** box under the
prompt, **filled with the workflow's own text**. Anima and the LTX workflows
ship long ones — "worst quality, low quality…", "pc game, console game…" — that
nobody could see before. Leave it alone and nothing changes. Edit it and your
text is used; empty it and the run has no negative prompt at all.

Workflows without one show no box.

## Choosing a model

When the folder a workflow's model sits in holds several of the same family —
a few Z-Image or Krea fine-tunes side by side — a **Model** dropdown appears,
with the workflow's own marked. The choice is remembered per workflow, and the
workflow file is never rewritten.

EasyAI never downloads these. Copy another model into the same folder, press
**Refresh**, and it appears. The list comes from the running ComfyUI, so it
only offers files the engine can load. A family with one model shows no
dropdown.

## Workflows that need pictures

Image-to-image, image-to-video and first-frame/last-frame all need the user to
supply pictures, and some need several. EasyAI gives each loader its own
labelled box: up to **10 pictures**, plus 2 sound and 2 video slots
(`image_in`, `image_in_2`, … `audio_in`, `video_in`).

The name on each box is taken from the workflow itself, in this order:

1. **The node's title** — `FIRST FRAME`, `Middle IMG`, `End IMG`, `Load Image3`
2. **The input it feeds** — `start_image` → "Start image", `image5` → "Image 5"
3. **Its position** — "Picture 1" … "Picture 10"

Order is worked out from meaning, not from node numbers. In a real LTX
first/last-frame workflow the LAST frame is node 47 and the FIRST is node 45 —
EasyAI still puts FIRST at the top. Numbered references (`Load Image1`…`5`) sort
numerically regardless of node id.

If a title is wrong or missing, rename the box in **Set up…** and it sticks.

A workflow that loads a picture will not run until one is chosen — the filename
saved inside the workflow points at *your* machine, not the viewer's, so EasyAI
asks for it by name rather than failing inside ComfyUI.

### Reference groups that grow — Qwen Image 2.1, Flux 3 Klein

Some nodes take their reference pictures as an *auto-growing group*.
Qwen Image 2.1's encoder accepts `images.image_1` up to `images.image_16`. An
exported workflow only wires the one picture its author used, which on its
own would give a single slot.

When a workflow wires **exactly one** picture into such a group, EasyAI adds
the rest when the workflow loads, up to **10 in total**: a copy of the loader
for each extra member, labelled **Reference 1** to **Reference 10**. The file
itself is never rewritten. What was added is recorded in the manifest, so the
same slots return with ComfyUI closed. A group whose author wired several
pictures — MiniMax H3's reference workflow wires three — is treated as a
deliberate choice and left alone.

The slots appear **one at a time**: filling one reveals the next, and emptying
one in the middle moves the rest up. The node numbers pictures by position, so
a gap would silently turn your third picture into `<image 2>` in the prompt.
This way *Reference 3* is always `<image 3>`.

A loader feeding **several** single-picture groups grows them together.
Flux 3 Klein's edit workflow hands its picture both to the edit node and to the
batch its prompt upsampler looks at; each added picture goes to both, so the
upsampler describes every picture the model sees. (Flux 3 counts from
`<ref_image_0>`, so *Reference 1* is `<ref_image_0>` there.)

### Files you can leave out

Some inputs are genuinely optional. `MiniMaxH3ReferenceToVideo` takes its
reference images, videos and audio in groups the node marks optional. A viewer
can attach one picture, or just a sound, and skip the rest. Those boxes are
labelled **(optional)**, with a line above them saying so.

EasyAI works this out from the running ComfyUI — an input is optional only when
the node says so, and only when *every* input a loader feeds is optional. An
auto-growing group may also say how many members it needs (`min`), and only
those first members are compulsory. Qwen Image 2.1 says `min: 0`, so all ten
references are optional. A first-frame picture wired into a required
`start_image` stays compulsory. With no ComfyUI to ask, nothing is treated as
optional, because wrongly dropping something breaks the run while wrongly
keeping it costs nothing.

Skipping a file **removes its input from the graph**, and the loader with it.
Merely leaving the value alone would run the workflow against the author's own
filename. Details that follow from that:

- A loader feeding several inputs takes them all with it. The video loader
  supplies both the footage and its soundtrack, so those go together.
- Numbered groups are closed up afterwards, **counting from wherever the group
  starts**. Attach MiniMax's first and third picture and they arrive as
  `ref_image_0` and `ref_image_1`; Qwen's start at `image_1`. The node reads
  the group by position, and `image_0` is not a name Qwen's node has.
- Leaving an optional slot empty is not reported as a problem. It is your
  choice, and the run honours it.

Running with **nothing** attached is refused with a single message:
*"Please attach at least one file to work from."* The nodes themselves allow it
(`min: 0`), but a reference workflow run with no references is almost never
what someone meant. This is a guardrail, not a limit. For text-only Qwen
Image 2.1, use **Qwen Image 2.1 - Text to Image**, which ships alongside the
image-edit workflow.

## Layout boxes

Flux 3 Klein and MOSS-Image can be told *where* things go. Under the prompt,
**Layout boxes → Draw boxes…** opens a canvas:

- **Drag** to draw a box. Click a box to pick it, drag it to move it, drag its
  corner to resize it.
- Give each box a **kind** and a **description** — *Object*, *Text* (with the
  exact words to render) or, for MOSS-Image, *Character*.
- Boxes are drawn from the back forwards: the background first, the things in
  front last. **Move back / Move forward** change the order.

For Flux 3 Klein the canvas shows **Reference 1**, the picture being edited, so
the boxes sit on what you are changing. Refer to a box in the prompt by its
number — *Change `<obj_1>` into a straw hat* — counting every box, text boxes
included.

**MOSS-Image keeps its boxes in the prompt.** MOSS reads a layout written as
JSON in the prompt itself — `{"summary": {...}, "items": [...],
"bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}`, the same form
ComfyUI's MOSS-Image Layout Prompt node writes. So:

- A prompt that already holds such a layout opens in the editor with every
  item drawn — its kind, caption, text and palette.
- Pressing **OK** writes the boxes back into the prompt. The summary — caption,
  meta, palette — stays exactly as it was.
- With **no prompt**, drawing boxes fills the prompt with the layout. With
  plain words, they become the layout's caption.
- **Clear** takes the boxes out again; a layout that only had a caption goes
  back to plain words.

**Reading one back.** The **Read a prompt** tab gives a MOSS-Image picture's
layout back too, and **Use this in Image** picks the MOSS workflow, the
picture's shape and its layout, so **Edit boxes…** opens with every box where
it was:

- A prompt fed through a prompt batcher (one prompt per line) is followed back
  to the lines. Each picture of the run gets its own line, found from its
  place among the run's files; a **Line** box steps through the others.
- A picture made with boxes drawn in ComfyUI's own canvas gets its words and
  boxes joined into a layout again.

The canvas has the size MOSS will make from the shape and detail you picked,
since the layout's pixels are measured on it. Change either after drawing and
the boxes are moved to match when you press Create. A layout written for a
bigger or differently shaped picture is cut to fit, and the editor says so.

With no boxes the layout nodes are taken out of the run and the prompt is used
as plain text, so a workflow with boxes works exactly like one without until
you draw some. Boxes are kept per workflow until EasyAI closes.

The prompt helper can rewrite a layout away, so a note appears when both are
on. MOSS-Image turns its helper off when you draw boxes, as ComfyUI's own
template does; Flux 3 Klein's upsampler is built to read the layout, so it
stays as you set it.

## Optional LoRA

A workflow can leave a slot for a LoRA of your own: a LoRA loader set to
strength **0**. EasyAI shows a **LoRA (optional)** dropdown for it, with a
strength. **None** — the default — takes the loader out of the run, so the
author's own file is never needed and nothing has to be installed. A LoRA at
any other strength is part of the workflow's recipe and is left alone.

The list holds the LoRAs in the folder named after the model's — `Moss\` for
MOSS-Image — since a LoRA only works with the family it was trained for. With
no such folder, every LoRA ComfyUI has is offered. The choice is remembered
per workflow.

## Prompt enhancer (LTX 2.5)

LTX 2.5 workflows run the prompt through a language model first
(`TextGenerateLTX2Prompt`), turning a short line into the long cinematic
description the model was trained on. It writes far better prompts, but it loads
another multi-gigabyte model and adds time.

When EasyAI finds an enhancer node it shows a tick box:
**"Let the AI improve my wording first"** — on by default, remembered between
runs.

Flux 3 Klein and MOSS-Image choose between the helper's words and yours with
a switch node, set opposite ways in the two. The tick box sets that switch, so
it means the same thing in both.

Switching it off is real rewiring, not a flag. An API workflow has no concept of
a muted node, so EasyAI points everything that read from the enhancer at
whatever fed the enhancer instead, then deletes the node — so the language model
never loads at all.

Recognised: `TextGenerateLTX2Prompt`, `TextGenerate`, the `ThinkingLLM` and
`AILab` Qwen-VL prompt enhancers, Plush's `Enhancer` / `AdvPromptEnhancer`, and
anything named like a prompt enhancer. Image enhancers (Topaz, HitPaw,
`LTXVEnhanceAVideoKJ`) are deliberately *not* matched. Bind or clear it by hand
under **Set up… → Prompt improver**.

## Prompt Helper

A tab for workflows that turn a rough idea into a long, detailed prompt. Put
their API files in `workflows/prompt-enhancer/`.

Type *"a fox in the snow"*, press **Improve my prompt**, and the result appears
on the right. From there: **Copy**, or pick a tab and press **Go** to drop it
straight into Image or Video and switch there.

Unlike Image and Video, the result here is *text*, not a file. ComfyUI reports
it in the history as `{"<node>": {"text": ["..."]}}`, so the workflow needs a
**Preview Any** or **Show Text** node on its output. Without one there is
nothing to read back, and EasyAI says so. Every result is also saved as a
`.txt` in `output/prompt-enhancer/`, and the list underneath reopens past ones.

The "Let the AI improve my wording first" switch is hidden here: on this tab
the workflow *is* the enhancer, so turning it off would leave nothing to run.

## Video length

Video tabs get a **Length** box in seconds, 2–15, defaulting to 5 and
remembered between runs. Under it: the frame count it works out to, and above
8 seconds an amber caution that long clips can exhaust graphics memory.

Workflows almost never expose a frame count directly — they calculate one:

```
LTX 2.5     frames = duration × frame_rate + 1     ComfyMathExpression
MiniMax H3  frames = max(5, round(duration × 24)) + …
```

So the number worth showing is the **Duration** primitive feeding that sum, not
the frame count coming out of it. EasyAI walks upstream from the frame-count
input to find it, telling Duration from Frame Rate by their node titles (both
are plain integers in the same expression). A workflow that really does take a
frame count is handled too — the box still reads seconds and converts.

`ImageFromBatch.length` is deliberately ignored; that means "take this many
images from a batch", not "make an N-frame video".

Change the range or the caution threshold with `video_length_min`,
`video_length_max` and `video_length_warn` in `settings.json`.

## Shapes and pixel sizes

Ratios: `1:1  4:3  3:4  3:2  2:3  16:9  9:16  21:9  9:21`

Image models get a solved size — ratio held as exactly as the model's grid
allows, at roughly the megapixel count it was trained for. Video models use a
fixed table instead, because arbitrary sizes cost quality and VRAM.

| ratio | Flux 2 / Krea 2 / Z-Image | LTX 2.5 | MiniMax H3 |
|---|---|---|---|
| 1:1 | 1024 × 1024 | 960 × 960 | 1024 × 1024 |
| 2:3 | 832 × 1248 | 800 × 1216 | 832 × 1248 |
| 3:4 | 864 × 1152 | 864 × 1152 | 864 × 1152 |
| 9:16 | 720 × 1280 | 704 × 1216 | 768 × 1344 |
| 9:21 | 672 × 1568 | 640 × 1536 | 672 × 1568 |

The chooser always shows the real pixel size, so nothing is hidden.

## Detail (megapixels)

**Image** and **Video** both have a second control next to the shape:
**Detail**, from 0.5 to 2.0 megapixels. The shape decides the proportions; this
decides how big. Change it and the shape list re-labels itself, so the pixel
counts on screen always match the file you get.

**It starts at whatever the workflow itself was built with**, not at a fixed
default — several video workflows here sit at 0.4 or 0.5, and quietly pushing
them to 1.0 would change a result their author tuned and could exhaust the
card. A workflow built below 0.5 keeps its own floor rather than being rounded
up. Only when a workflow states no budget does the remembered setting apply.

The caution threshold differs by tab: 1.5 for pictures, **1.0 for video**,
because video pays the pixel cost on every single frame — 2.0 megapixels across
15 seconds is a very different job from one still image.

It reaches the workflow two ways. A workflow with a `megapixels` input
(`ResolutionSelector`, `ImageScaleToTotalPixels`) is handed the number and does
its own arithmetic; one holding plain width and height gets recalculated values.
A workflow with *both* — Flux 2 keeps the latent size on the chooser and the
scheduler on literals — gets both, set to the same numbers, because if they
disagree the sampler is scheduled for a different picture than it draws.

**A megapixel means 1024 x 1024 to these nodes**, not 1,000,000, so 1:1 at 1.0
is exactly 1024x1024. That was measured against the live node rather than
assumed; `tests/test_megapixels.py` pins fourteen of its answers.

### What the pipeline does afterwards

Choosing the size is not always the end of it. **LTX floors both sides to a
multiple of 64** after the fact: ask for 1376x768 and the file comes out
1344x768; 960x544 becomes 960x512. MiniMax and the image models pass the
chosen size straight through.

That is per engine (`output_grid` in `app/ratios.py`), measured by generating
files and reading their real dimensions back, so the number on screen is the
number in the file. Five such measurements are pinned in the tests.

Some workflows pick their size through a node instead (`ResolutionSelector`).
Those accept a fixed list of labels — `2:3 (Portrait Photo)`,
`9:16 (Portrait Widescreen)` — and the descriptive half differs per entry, so
EasyAI reads the real list from ComfyUI rather than inventing a label. Shapes
the node does not offer are removed from the chooser instead of silently doing
nothing. `ResolutionSelector`, for instance, has no 9:21.

Such a node also decides the pixels itself, from its own megapixel target and
rounding step. EasyAI reads those two numbers out of the workflow and repeats
the calculation, so the size on screen is the size of the file you get — one of
these workflows is set to 0.9 MP and another to 0.5, and the table above would
have been wrong for both.

Adding a model family is one entry in `app/ratios.py`. The workflow's family is
detected from its node types and model filenames, and can be overridden in the
Set up… dialog.

## Settings

**File → Settings**, or `Ctrl+,`. The ComfyUI section tests itself — pick a
folder and it immediately reports whether it found a start file and whether a
server is answering.

EasyAI Setup fills the ComfyUI folder in for you. Turn off *Start ComfyUI
automatically* if you run it yourself or on another PC.

**Language** — English, 繁體中文 or 简体中文. EasyAI follows Windows'
language the first time and switches in place when you change it. Every
visible word is translated, error messages included. Setup and Studio have the
same choice.

---

## EasyAI Setup

One window, read top to bottom: where it goes, what to include, how big that
is, then Install.

**What it installs** is fixed by `setup/catalog.json`, never "whatever is
newest", so a viewer can't end up with a combination nobody has tested:

- **ComfyUI 0.42.1.** The official Windows portable (NVIDIA), checked against
  GitHub's SHA-256 before seven minutes of unpacking, then pinned to the exact
  commit.
- **Only the add-ons the chosen workflows need**, each at the version or
  commit it was tested with — 9 across all three groups, not the nearly 50
  installed on the machine they came from.
- **The models** for each ticked group, in the folders ComfyUI reads.

Every step is resumable. Stop it, or lose the connection, and the next run
carries on where it left off — part-finished downloads included.

### Updating a ComfyUI you already have

Point Setup at a folder holding a ComfyUI and it says what it found:

- *ComfyUI 0.42.1 will be installed here* — an empty folder.
- *✓ ComfyUI 0.42.1 is already here, with EasyAI's add-ons at their tested
  versions* — nothing to do.
- *Found ComfyUI 0.37.0 here. EasyAI is tested with 0.42.1.* — with a switch:
  **Update ComfyUI and EasyAI's add-ons to the tested versions**.

The switch is on for an older ComfyUI and off for a newer one, since someone
chose that version. Before anything starts, a summary lists exactly what will
happen — *Move ComfyUI from 0.37.0 to 0.42.1*, *Bring 1 add-on to its tested
version*, *Download 4 models*.

How the update behaves:

- **Only the pinned version, never "latest".** ComfyUI's own updater pulls the
  newest release; Setup moves to the one commit in the catalogue.
- **ComfyUI and its add-ons move together**, and ComfyUI's Python packages are
  updated to match, so the result is the combination that was tested.
- **No git needed.** It runs through the portable's own Python.
- **Safe with linked model folders.** It changes only the files that differ
  between the two versions and refuses rather than overwrite your own edits.
  A forced checkout, like the official updater's, can swap a `models` folder
  linked to another drive for an empty one.
- **Undoable.** The previous state is kept as a git branch
  (`easyai-before-<date>`), and a replaced add-on is restored if its new
  version fails to install.

### Models it already has are never downloaded again

**Download the models ComfyUI does not have yet** — on by default. Turn it off
to install or update ComfyUI and the add-ons only.

Setup asks ComfyUI's own `folder_paths` where it looks, through the portable's
Python and without starting the server. So a model is found wherever ComfyUI
would find it: text encoders in `models/text_encoders` *or* `models/clip`,
diffusion models in `models/unet` *or* `models/diffusion_models`, anything
`extra_model_paths.yaml` adds, and folders linked elsewhere. The total says it
plainly: *17 of 17 models already in ComfyUI, 0 to download*.

### Changing what is installed, and where — setup-settings.json

Setup's list of what to install is built into the program. So it also writes
that list out beside itself, as **`setup-settings.json`**, the first time it
runs — and from then on reads it. Edit it, press **Reload** in Setup (or
**Open the install list** to find it), and the next install follows it. No
rebuild needed.

```json
"folders": {
  "models": "",
  "workflows": "EasyAI-workflows"
},
"models": {
  "QWEN 2.1\\qwen_image_2.1_int8_convrot.safetensors": {
    "kind": "diffusion_models",
    "from": "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/…",
    "mirror": "",
    "to": ""
  }
},
"groups": {
  "image": {
    "workflows": [
      { "file": "image_qwen_image_2_1_t2i api - GarionHK.json",
        "from": "built-in", "to": "image" }
    ]
  }
}
```

- **`folders.models`** — where models go. Empty means the models folder of
  the ComfyUI being installed. A full path — `"D:/AI Models"` — puts every
  model there instead, and Setup tells ComfyUI to look there too.
- **`folders.workflows`** — where EasyAI's workflows go: a folder inside the
  install folder, or a full path. EasyAI is pointed at it.
- **Each model** — `from` is its download link, `mirror` is tried first when
  set, `kind` is the ComfyUI folder it belongs in, and `to` optionally gives
  that one model a folder of its own.
- **Each workflow** — `from` is `built-in`, a file path (a relative one is read
  from beside `setup-settings.json`) or a web link. `to` is its folder under
  the workflows folder, or a full path. A workflow's `.manifest.json` and
  thumbnail travel with it.
- **Each group** — which workflows, models and add-ons its tick box installs.

Write paths with forward slashes. A single backslash isn't valid in JSON, and
Setup says so rather than guessing. Delete the file to go back to the built-in
list.

**Models kept outside ComfyUI** only work if ComfyUI knows where to look, so
Setup keeps a marked block in ComfyUI's own `extra_model_paths.yaml` and
rewrites only that block. Anything else in the file is left exactly as it was.

**An old file beside a newer Setup** would quietly hide whatever the newer
Setup added — a new workflow and its models would never be offered. So the
file records which built-in list it came from:

- made from this Setup's list → used as it is;
- older, and never edited → replaced with the new list, silently;
- older, and edited → kept, since the edits are yours, with a warning naming
  what it's missing and a button to switch to the new list. Your edited copy
  is kept as `setup-settings.old.json`.

### Models behind a sign-in

A few models need a HuggingFace or Civitai account and a licence accepted on
the model's page. Where the licence allows it, those files are mirrored on the
EasyAI server, so viewers need no account.

**Flux 3 Klein is not mirrored.** Its four files come from Black Forest Labs'
own repository: accept the licence on
[black-forest-labs/flux-3-image-klein-comfy](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy)
and paste a HuggingFace key into Setup. Everything else in the Image group
needs no account. The licence text is saved into the
install folder, as the licence requires. The key boxes stay as a fallback for
when the mirror can't be reached. Keys are used for that run only and never
written to disk.

---

## EasyAI Studio

The publishing tool — for whoever maintains the workflows, not for viewers.

- **Add a workflow.** Pick an exported API workflow and its group, and Studio
  reports what EasyAI can drive, every model with its folder, size and download
  link, and any add-on it needs. **Add to EasyAI** copies it in and adds its
  models to the catalogue, so viewers' Setup installs them. Download links are
  found in ComfyUI's own bundled templates, *including those inside subgraphs*,
  where newer templates keep their loaders. Reading only the top level missed
  635 links across 166 templates.
- **Catalogue** — rebuild `setup/catalog.json` from this machine.
- **Download links** — check every link still works and still serves the same
  file.
- **Languages** — find text added since the translations were last updated.

### How the catalogue is built

`python tools/make_catalog.py` (or Studio's Catalogue page) builds the
catalogue from the machine the workflows run on, so it describes a setup that
demonstrably works:

- **ComfyUI's commit and every add-on's version come from the live install**
  — each checkout's git commit, or a registry pack's `pyproject.toml`.
  ComfyUI-Manager's snapshots used to be the source, but they stop being
  written when Manager isn't loaded, and the newest one here predated the
  update to 0.37.0. **ComfyUI itself is pinned to a release**
  (`COMFYUI_PIN` in `tools/make_catalog.py`), not to whatever commit the
  building machine runs — that one may be ahead of any published release. Folders named `*.disabled` are skipped, as ComfyUI skips
  them.
- **Which models and add-ons each group needs** comes from EasyAI's own
  workflow files. A test fails if a workflow in `workflows/` is missing from
  the catalogue, so a new one can't be left out of Setup unnoticed.
- **Download links are stable.** A link already in the catalogue is kept; only
  `setup/url_overrides.json` can replace one. Among newly found links, an
  official Comfy-Org copy wins over a same-named file in someone else's
  repository.

---

## Building the .exe files

```bash
"Build EXE.bat"
```

Builds all three programs into `dist\`, each a single file that needs nothing
installed. Settings, workflows and results are kept beside the `.exe`, so put
it in a folder you can write to — not Program Files.

Each build is described by its `.spec` file (`EasyAI.spec`,
`EasyAI Setup.spec`, `EasyAI Studio.spec`). These are source files, not
generated ones, so don't delete them. What gets left out, and why, is in
`build_common.py`. The machine this is built on carries torch, transformers and
the rest, and PyInstaller would otherwise pack in anything merely importable.
Among what it leaves out:

- numpy with its OpenBLAS — 8 MB
- cryptography — 3.4 MB
- Pillow's AVIF decoder — 4 MB
- Qt's QML, Quick and PDF libraries — 8 MB

Every exclusion was checked against what the programs actually load. Without
them, adding the Read tab's picture support would have grown EasyAI.exe by
about a third; with them it stays at roughly 57 MB, the size of v1.0.0.

---

## Look

Near-black surfaces with a single warm orange accent. Two typefaces: one for
words, one reserved for numbers you might read back — pixel sizes, megapixels,
the engine address — so figures line up and do not jitter as they change.

The design asks for **Manrope** and **JetBrains Mono**. Neither ships with
Windows, so EasyAI uses Segoe UI and Cascadia Mono unless it finds them: drop
the `.ttf` files into `app/ui/fonts/` and they are picked up at startup. Both
are open-licensed and can be shipped with the app. The layout is unaffected
either way.

Shape is a row of chips rather than a dropdown, so every size a workflow can
make is visible at once. Detail is a slider. The engine's address and the card's
VRAM are on screen at all times, because they are the first things to check when
a run fails.

### Icon

`EasyAI-Icon.png` is the master artwork. `tools/make_icons.py` turns it into the
pack in `assets/icons/`:

```bash
python tools/make_icons.py
```

It writes a nine-resolution `EasyAI.ico` (16/20/24/32/40/48/64/128/256) plus
loose PNGs. Two things it does beyond resizing:

- **Trims the transparent margin.** The master has about 10% padding on each
  side; Windows adds its own, so shipping it just makes the artwork smaller in
  the taskbar. Trimming reclaims a fifth of every icon.
- **Sharpens below 48 pixels**, where a plain downscale goes soft and the
  star's face turns to mush.

Re-run it whenever the master changes.

## Layout

```
EasyAI.py              EasyAI's entry point        (EasyAI.bat to double-click)
EasyAISetup.py         EasyAI Setup                (EasyAISetup.bat)
EasyAIStudio.py        EasyAI Studio               (EasyAI Studio.bat)
Build EXE.bat          builds all three into dist\
*.spec, build_common.py   what goes into each build, and what is left out
app/
  config.py            settings, with defaults merged over what's on disk
  i18n.py              translations - text is looked up in lang/*.json
  modes.py             the modes and what each needs
  ratios.py            ratio table, resolution solver, video ladders
  jobs.py              QThread workers (engine startup, one generation)
  queue.py             the queue: one item at a time, in order
  prompts.py           reading a prompt back out of a picture or video
  comfy/
    client.py          HTTP + websocket: upload, queue, progress, download
    launcher.py        find/start ComfyUI, tie it to EasyAI's lifetime
    objectinfo.py      the pre-flight check, and what each node accepts
  workflows/
    loader.py          find files, check format, pair with a manifest
    manifest.py        the sidecar: schema and autodetection
    grow.py            fill reference groups out to what their node takes
    patch.py           write the user's choices into a copy of the graph
  ui/                  PySide6 windows, tabs and widgets
setup/
  catalog.json         what Setup installs - generated, see above
  install_list.py      setup-settings.json: the editable copy of that list
  existing.py          what is already in a ComfyUI folder
  steps.py             the install and update steps
  download.py          resumable, verified downloads
  authoring.py         adding a workflow (used by Studio)
studio/                EasyAI Studio's pages
tools/                 make_catalog.py, verify_urls.py, make_icons.py
lang/                  zh-Hant.json, zh-Hans.json
workflows/             ← exported API workflows: image/, video/, prompt-enhancer/
output/                results, one folder per mode
tests/
```

## Tests

```bash
python -m pytest tests/ -q
```

972 tests, no ComfyUI needed. The scripts that do need a running server are
separate and are run by hand:

```bash
python tests/smoke_comfy.py
python tests/smoke_workflows.py "E:\ComfyUI Workflow"
python tests/make_starter_workflow.py
python tests/e2e_generate.py image
```

## License

[Apache License 2.0](LICENSE-2.0.txt). Models downloaded by EasyAI Setup carry their
own licences; those that require it are saved into the install folder.
