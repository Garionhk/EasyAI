"""Giving a reference-picture group the slots its node can actually take.

Qwen Image 2.1's encoder accepts up to sixteen reference pictures through one
auto-growing input group - ``images.image_1``, ``images.image_2`` and so on -
but a workflow exported from ComfyUI wires only the one the author happened to
use. EasyAI would then offer a single picture slot, however many the node can
read.

This adds the rest: one more picture loader per extra member, copied from the
one already there and wired into the next name. It happens in memory when the
workflow loads - the file itself is never rewritten, so it can still be shared
or re-added through EasyAI Studio exactly as exported.

Only a group that its workflow wires a single picture into is grown. That is
how ComfyUI exports these groups by default, so it marks a workflow whose
author never chose a number. MiniMax H3's reference workflow wires three into a
group that takes nine; three is a choice, and it is left alone.

What was added is recorded in the manifest, so the same slots come back when
the workflow loads with ComfyUI closed - without it, a manifest written with
the engine running would point at nodes that did not exist offline.
"""
from __future__ import annotations

import copy
import re

#: Loader classes that read a picture, and the input holding its filename.
IMAGE_LOADERS = {"loadimage": "image"}

#: The most picture slots EasyAI has - IMAGE_SLOTS runs image_in to image_in_10.
MAX_PICTURES = 10


def _links(graph: dict) -> list[tuple[str, str, str]]:
    """(consumer id, input name, source id) for every wire in the graph."""
    found = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        for name, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and value and isinstance(value[0], (str, int)):
                found.append((str(node_id), name, str(value[0])))
    return found


def _is_picture_loader(node) -> bool:
    return (isinstance(node, dict)
            and str(node.get("class_type", "")).lower() in IMAGE_LOADERS)


def _picture_loader_count(graph: dict) -> int:
    return sum(1 for node in graph.values() if _is_picture_loader(node))


def new_node_id(consumer: str, name: str) -> str:
    """A stable id for an added loader, so every load produces the same graph.

    Colons are avoided: ComfyUI reads ``459:474`` as "node 474 inside
    subgraph 459", and an added node is not inside anything.
    """
    return "easyai_" + re.sub(r"[^A-Za-z0-9]+", "_", f"{consumer}_{name}")


def plan(graph: dict, caps, limit: int = MAX_PICTURES) -> list[dict]:
    """Which groups to grow, and into which names. Needs the running engine.

    Returns one entry per picture loader that gets company::

        {"node": "459:474", "group": "images", "loader": "470",
         "names": ["image_1", ..., "image_10"],
         "also": [{"node": "50", "group": "images", "names": [...]}]}

    ``names`` is the whole group as it should end up, first member included,
    so the manifest alone is enough to rebuild it offline.

    ``also`` lists other groups the same loader feeds. Flux 3 Klein's edit
    workflow hands its picture both to the edit node and to a batch the prompt
    upsampler looks at; growing only the first would let the model see ten
    pictures while the upsampler describes one. Every group a loader feeds
    grows in step, sharing the added loaders.
    """
    if caps is None or not getattr(caps, "available", False):
        return []

    # Which group members each consumer already has wired to a picture loader.
    members: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for consumer, input_name, source in _links(graph):
        group, dot, member = input_name.partition(".")
        if not dot or not _is_picture_loader(graph.get(source)):
            continue
        members.setdefault((consumer, group), []).append((member, source))

    # Single-picture groups the node can grow, collected per loader.
    by_loader: dict[str, list[tuple[str, str, list[str]]]] = {}
    for (consumer, group), wired in sorted(members.items()):
        if len(wired) != 1:
            continue
        class_type = str((graph.get(consumer) or {}).get("class_type", ""))
        grow = caps.autogrow(class_type, group)
        if not grow:
            continue
        member, loader = wired[0]
        names = grow["names"]
        if member not in names:
            continue
        # Continue from the member already wired, in the node's own order.
        by_loader.setdefault(loader, []).append(
            (consumer, group, names[names.index(member):]))

    specs = []
    room = limit - _picture_loader_count(graph)
    for loader, groups in sorted(by_loader.items()):
        if room <= 0:
            break
        # As many as the smallest group takes, so every group gets each one.
        count = min(len(names) for _, _, names in groups)
        count = min(count, 1 + room)
        if count < 2:
            continue
        (consumer, group, names), others = groups[0], groups[1:]
        spec = {"node": consumer, "group": group, "loader": loader,
                "names": names[:count]}
        if others:
            spec["also"] = [{"node": c, "group": g, "names": n[:count]}
                            for c, g, n in others]
        specs.append(spec)
        room -= count - 1
    return specs


def apply(graph: dict, specs: list[dict]) -> list[str]:
    """Add the loaders the plan calls for. Safe to repeat.

    Returns the ids of any loaders added this time.
    """
    added = []
    for spec in specs or []:
        consumer = graph.get(spec.get("node"))
        template = graph.get(spec.get("loader"))
        if not isinstance(consumer, dict) or not _is_picture_loader(template):
            continue
        targets = [spec] + [a for a in (spec.get("also") or [])
                            if isinstance(graph.get(a.get("node")), dict)]
        for position, name in enumerate((spec.get("names") or [])[1:], 1):
            node_id = new_node_id(spec["node"], name)
            if node_id not in graph:
                clone = copy.deepcopy(template)
                clone.setdefault("_meta", {})["title"] = (
                    (template.get("_meta") or {}).get("title") or "Load Image")
                graph[node_id] = clone
                added.append(node_id)
            for target in targets:
                names = target.get("names") or []
                if position >= len(names):
                    continue
                inputs = graph[target["node"]].setdefault("inputs", {})
                key = f"{target.get('group', '')}.{names[position]}"
                inputs.setdefault(key, [node_id, 0])
    return added


def member_loaders(graph: dict, spec: dict) -> list[str]:
    """The loader feeding each member of a grown group, in the node's order."""
    inputs = (graph.get(spec.get("node")) or {}).get("inputs") or {}
    group = spec.get("group", "")
    loaders = []
    for name in spec.get("names") or []:
        value = inputs.get(f"{group}.{name}")
        if isinstance(value, list) and value:
            loaders.append(str(value[0]))
    return loaders


def grown_names(specs: list[dict], node: str, group: str) -> list[str] | None:
    """The full name list of a grown group, or None if it was not grown."""
    for spec in specs or []:
        if spec.get("node") == node and spec.get("group") == group:
            return list(spec.get("names") or [])
    return None
