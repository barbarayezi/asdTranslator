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


def entry_options(context_providers: list[str] | None = None) -> dict[str, Any]:
    """第一步的选项。注意：不问「你感觉如何」。

    context_providers 里含 "sleep" 时，会根据近几天的睡眠/HRV/心情，
    预先勾选更可能贴切的躯体锚点（只读用户自己的身体数据，只推荐、可取消）。
    """
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
        "body_hints": body_hints(context_providers),
    }


def body_hints(context_providers: list[str] | None) -> dict[str, Any]:
    """根据开启的数据源，返回躯体锚点的预填建议。

    只服务于「用户自己的躯体觉察」——读的是用户本机睡眠/身体数据，
    不是读对方。因此与 decode 的「不得用身体数据更准地读对方」红线不冲突：
    这里是帮用户定位「我自己现在是什么状态」，是自我觉察，不是读心。

    失败一律 fail-safe：返回 available=False，不影响主流程。
    """
    if not context_providers or "sleep" not in context_providers:
        return {"available": False}

    try:
        from engine import context as ctx_mod

        provider = next((p for p in ctx_mod.REGISTRY if p.id == "sleep"), None)
        if provider is None:
            return {"available": False}
        signals = provider.gather()
    except Exception:
        return {"available": False}

    if not signals:
        return {"available": False, "reason": "暂无可用的睡眠 / 身体数据"}

    suggested = _map_signals_to_somatic(signals)
    return {
        "available": True,
        "raw": signals,
        "suggested_somatic_ids": suggested,
        "explanation": (
            "根据你近几天的睡眠 / HRV / 心情，下面这些身体感觉可能更贴近你现在的真实状态。"
            "已经帮你预勾选，不贴切就取消——这读的是你的身体，不是对方。"
        ),
        "note": "这些是你的身体数据，用来帮你定位自己的状态，不是用来猜对方在想什么。",
    }


def _map_signals_to_somatic(signals: dict[str, str]) -> list[str]:
    """把睡眠/HRV/心情信号映射到躯体锚点 id。顺序：身体 > 睡眠 > 心情。"""
    import re

    body = signals.get("body_state", "")
    sleep = signals.get("sleep_quality", "")
    mood = signals.get("recent_mood", "")

    ids: list[str] = []
    seen: set[str] = set()

    def add(*xs: str) -> None:
        for x in xs:
            if x not in seen:
                seen.add(x)
                ids.append(x)

    # 1) 身体 / HRV 状态
    if "恢复不足" in body:
        add("exhausted", "want_silence", "shoulder_stiff", "heart_race")
    if "生理负荷偏高" in body:
        add("exhausted", "want_silence", "restless", "cant_focus")

    # 2) 睡眠质量（注意「0 晚差」含「差」字但实为 0，必须按数量判定）
    poor = re.search(r"(\d+)\s*晚差", sleep)
    poor_nights = int(poor.group(1)) if poor else 0
    if poor_nights > 0 or "poor" in sleep.lower():
        add("exhausted", "head_pressure", "cant_focus", "numb")

    # 3) 近期心情
    if "低落" in mood:
        add("want_silence", "exhausted", "numb")
    if "烦躁" in mood or "焦虑" in mood:
        add("restless", "cant_focus", "heart_race")
    if "疲惫" in mood or "累" in mood:
        add("exhausted")

    return ids


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
