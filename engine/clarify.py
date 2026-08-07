"""思路梳理：躯体锚点 → 候选情绪 → 具体诉求 → 可发送的表达 + 三分支预案。

设计依据 Kinnaird et al. (2019): ASD 群体述情障碍患病率 49.93%（NT 4.89%），
且「难以识别情绪」与「难以描述情绪」是两个独立的大效应。因此：
  - 入口是躯体感受和情境，不是「你现在感觉如何」
  - 识别（选情绪词）与描述（组织成句）分成两步
  - 「没感觉 / 我不知道」是合法答案，可以直接跳到诉求
  - 终点是可执行诉求，情绪命名只是中间产物
"""
from __future__ import annotations

from typing import Any

from engine.base import load


def entry_options() -> dict[str, Any]:
    """第一步的选项。注意：不问「你感觉如何」。"""
    kb = load("clarify")
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in kb["somatic"]:
        groups.setdefault(item["group"], []).append(
            {"id": item["id"], "label": item["label"], "note": item.get("note", "")}
        )
    return {
        "prompt": "先不用管情绪。看看下面哪些身体上的感觉符合你现在的状态，可以多选，也可以一个都不选。",
        "somatic_groups": groups,
        "situations": kb["situations"],
        "situation_prompt": "再看看刚才发生了什么（多选，可跳过）：",
        "skip_hint": "如果你现在说不上来，直接点「跳过，我先说事情」也完全可以。",
    }


def suggest_emotions(somatic_ids: list[str], situation_ids: list[str]) -> dict[str, Any]:
    """第二步：从躯体感受推候选情绪词。只推荐，不断言。"""
    kb = load("clarify")
    somap = {s["id"]: s for s in kb["somatic"]}

    tally: dict[str, int] = {}
    notes: list[str] = []
    for sid in somatic_ids:
        item = somap.get(sid)
        if not item:
            continue
        if item.get("note"):
            notes.append(item["note"])
        for emo in item["emotions"]:
            tally[emo] = tally.get(emo, 0) + 1

    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    candidates = [{"label": e, "weight": n} for e, n in ranked]

    others = [e for e in kb["emotion_pool"] if e not in tally]

    return {
        "prompt": (
            "根据你选的身体感觉，下面这些情绪词比较常见。挑出贴近的（可多选），"
            "都不贴切的话可以从完整列表里找，或者直接跳过。"
        ),
        "candidates": candidates,
        "all_emotions": others,
        "notes": notes,
        "allow_skip": True,
        "skip_label": "说不上来 / 没感觉",
        "skip_note": "「说不上来」是有效答案，不是失败。可以直接进入下一步。",
    }


def need_options() -> dict[str, Any]:
    kb = load("clarify")
    return {
        "prompt": "最后一步，也是最重要的一步：你希望对方做什么？",
        "hint": "情绪本身不用说给对方听也没关系。真正需要传达的是这一条。",
        "needs": [
            {
                "id": n["id"],
                "label": n["label"],
                "slots": n.get("slots", []),
                "note": n.get("note", ""),
            }
            for n in kb["needs"]
        ],
    }


def build(
    need_id: str,
    slots: dict[str, str] | None = None,
    emotions: list[str] | None = None,
    situations: list[str] | None = None,
    template_index: int = 0,
    include_emotion: bool = False,
) -> dict[str, Any]:
    """第三步：组装成一句能说出口的话，并给三分支预案。"""
    kb = load("clarify")
    slots = slots or {}
    emotions = emotions or []
    situations = situations or []

    need = next((n for n in kb["needs"] if n["id"] == need_id), None)
    if not need:
        return {"ok": False, "error": "unknown_need"}

    templates = need["templates"]
    idx = max(0, min(template_index, len(templates) - 1))
    sentence = templates[idx]
    for k, v in slots.items():
        sentence = sentence.replace("{%s}" % k, v)

    # 情绪句是可选的、独立的一句，默认不加入
    emotion_sentence = ""
    if include_emotion and emotions:
        emotion_sentence = "我现在有点%s。" % "、".join(emotions[:2])

    sit_map = {s["id"]: s["label"] for s in kb["situations"]}
    context_line = "、".join(sit_map[s] for s in situations if s in sit_map)

    branches = kb["branches"]
    return {
        "ok": True,
        "need": need["label"],
        "sentence": sentence,
        "emotion_sentence": emotion_sentence,
        "full": (emotion_sentence + sentence).strip(),
        "alternatives": [
            {"index": i, "text": t} for i, t in enumerate(templates) if i != idx
        ],
        "unfilled_slots": [s for s in need.get("slots", []) if "{%s}" % s in sentence],
        "context_line": context_line,
        "branches": [
            {"key": k, **v} for k, v in branches.items() if not k.startswith("_")
        ],
        "note": need.get("note", ""),
        "reminder": (
            "情绪句默认不加。要不要把情绪说给对方，完全由你决定——"
            "不说也不代表沟通不完整，诉求本身就是完整的信息。"
        ),
    }
