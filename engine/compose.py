"""ASD → NT 转换器。

产品原则（不可退让）：
1. 默认只标注，不改写。原文永远完整保留并可见。
2. 标记措辞是「在 NT 语境中可能被读作 X」，绝不是「你说错了」。
3. 每个改写建议都必须显示 gain / cost，让用户知道自己在交换什么。
4. 缓冲件逐条开关，用户可以全部拒绝直接发原文。
5. 不提供整体人设改写（那是 masking 效率工具，是红线）。
"""
from __future__ import annotations

import re
from typing import Any

from engine.base import Flag, dedupe_overlaps, find_all, load, split_sentences

_JARGON = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")
_APOLOGY_HEAD = re.compile(r"^(不好意思|抱歉|对不起|打扰了|冒昧)")


def _pattern_flags(text: str) -> list[Flag]:
    kb = load("directness")
    flags: list[Flag] = []
    for rule in kb["flags"]:
        for pat in rule.get("patterns", []):
            for start, end in find_all(text, pat):
                flags.append(
                    Flag(
                        flag_id=rule["id"],
                        label=rule["label"],
                        matched=pat,
                        start=start,
                        end=end,
                        nt_reading=rule["nt_reading"],
                        severity=rule["severity"],
                        keep_note=rule.get("keep_note", ""),
                        rewrites=rule.get("rewrites", []),
                    )
                )
    return dedupe_overlaps(flags)


def _structural_flags(text: str, scene: str) -> list[Flag]:
    kb = load("directness")
    rules = {f["id"]: f for f in kb["flags"]}
    th = kb["structural_thresholds"]
    sents = split_sentences(text)
    out: list[Flag] = []

    def mk(rule_id: str, matched: str, start: int, end: int) -> Flag:
        r = rules[rule_id]
        return Flag(
            flag_id=r["id"],
            label=r["label"],
            matched=matched,
            start=start,
            end=end,
            nt_reading=r["nt_reading"],
            severity=r["severity"],
            keep_note=r.get("keep_note", ""),
            rewrites=r.get("rewrites", []),
        )

    # 信息密度：单句过长且连接词多
    conns = th["connective_words"]
    cursor = 0
    for s in sents:
        start = text.find(s, cursor)
        cursor = start + len(s) if start >= 0 else cursor
        n_conn = sum(s.count(c) for c in conns)
        if len(s) >= th["high_density_chars_per_sentence"] and n_conn >= th["high_density_min_clauses"]:
            out.append(mk("high_density", s[:24] + "…", max(start, 0), max(start, 0) + len(s)))
        elif len(s) >= th["long_sentence_chars"]:
            out.append(mk("long_sentence", s[:24] + "…", max(start, 0), max(start, 0) + len(s)))

    # 开场 / 收尾（只在较正式的场景检查）
    if scene == "work" and len(text) > 40:
        head = text[: th["opener_check_max_chars"]]
        has_opener = any(k in head for k in ("你好", "您好", "hi", "Hi", "老师", "关于", "早上好", "在吗"))
        if not has_opener:
            out.append(mk("no_opener", text[:12], 0, min(12, len(text))))
        tail = text[-14:]
        has_closing = any(k in tail for k in ("谢谢", "辛苦", "感谢", "麻烦", "回复", "推进", "确认"))
        if not has_closing:
            out.append(mk("no_closing", text[-12:], max(0, len(text) - 12), len(text)))

    # 意图未标注：既没有疑问标记也没有明确的意图句
    if len(text) > 30:
        has_q = "?" in text or "？" in text
        has_intent = any(
            k in text
            for k in ("我想确认", "我需要", "同步一下", "需要你", "请你", "我的建议是", "我想说", "不需要你")
        )
        if not has_q and not has_intent:
            out.append(mk("unmarked_intent", text[:12], 0, min(12, len(text))))

    # 术语密度（跨专业时才有意义，这里只提示不判定）
    tokens = _JARGON.findall(text)
    if len(set(tokens)) >= 4:
        out.append(mk("jargon_density", "、".join(sorted(set(tokens))[:4]), 0, 0))

    return out


def _layer_suggestion(text: str) -> dict[str, Any] | None:
    """信息密度分层：结论先行。不删任何内容，只改呈现顺序。"""
    sents = split_sentences(text)
    if len(sents) < 3:
        return None
    concl_markers = ("所以", "因此", "综上", "总之", "结论是", "我的建议是", "我认为")
    idx = next(
        (i for i in range(len(sents) - 1, -1, -1) if any(m in sents[i] for m in concl_markers)),
        None,
    )
    if idx is None or idx == 0:
        # 没有显式结论句就提示用户自己指定，不擅自猜
        return {
            "kind": "ask",
            "text": "这段有 %d 句。如果告诉我哪一句是结论，我可以把它提到最前面，其余顺序不变。" % len(sents),
            "sentences": sents,
        }
    reordered = [sents[idx]] + [s for i, s in enumerate(sents) if i != idx]
    return {
        "kind": "reorder",
        "text": "把结论提到第一句，其余顺序不变。零信息损失。",
        "preview": "".join(reordered),
        "moved": sents[idx],
        "sentences": sents,
    }


def analyze(text: str, scene: str = "work") -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty"}

    # 两类标记分别去重后再合并。
    # 结构性标记（信息密度、缺少开场等）的 span 覆盖整句甚至全文，
    # 如果和短模式标记一起去重，会把后者整个吞掉——这是两个正交的维度，不应互相压制。
    flags = dedupe_overlaps(_pattern_flags(text)) + dedupe_overlaps(_structural_flags(text, scene))
    hedges = load("hedges")

    # 只提供与命中标记相关的缓冲件，避免给一堆无关选项
    flag_ids = {f.flag_id for f in flags}
    buffers = [b for b in hedges["buffers"] if set(b.get("attach_to", [])) & flag_ids]

    severities = [f.severity for f in flags]
    if "high" in severities:
        level = "high"
    elif "medium" in severities:
        level = "medium"
    elif flags:
        level = "low"
    else:
        level = "none"

    headline = {
        "high": "有几处在 NT 语境里容易被读作对人的否定。下面逐条列出，你可以选择改或不改。",
        "medium": "有一些措辞可能被误读，都是可选项。",
        "low": "只有很轻微的几处，多数情况下直接发就行。",
        "none": "没有命中任何标记。这段直接发出去应该没问题。",
    }[level]

    return {
        "ok": True,
        "engine": "rules",
        "original": text,
        "scene": scene,
        "level": level,
        "headline": headline,
        "flags": [f.to_dict() for f in flags],
        "layering": _layer_suggestion(text),
        "openers": hedges["openers"],
        "buffers": buffers,
        "closings": hedges["closings"],
        "refusal_levels": hedges["refusal_levels"],
        "note": (
            "以上是标注，不是纠错。你的原文在表意上是清楚的——"
            "这些标记只描述它在 NT 解码习惯下可能产生的偏差（Milton 2012 双重共情问题）。"
            "是否调整取决于你想要什么：表达的准确性，还是对方的配合度。"
        ),
    }


def apply_edits(
    text: str,
    opener_id: str = "plain",
    closing_id: str = "plain",
    buffer_ids: list[str] | None = None,
    slots: dict[str, str] | None = None,
    reorder_index: int | None = None,
) -> dict[str, Any]:
    """按用户显式选择组装最终文本。未选中的一律不动。"""
    hedges = load("hedges")
    slots = slots or {}
    buffer_ids = buffer_ids or []

    body = (text or "").strip()

    if reorder_index is not None:
        sents = split_sentences(body)
        if 0 <= reorder_index < len(sents):
            body = "".join([sents[reorder_index]] + [s for i, s in enumerate(sents) if i != reorder_index])

    def fill(tpl: str) -> str:
        out = tpl
        for k, v in slots.items():
            out = out.replace("{%s}" % k, v)
        # 未填充的槽位保留为可见占位，提醒用户补全，而不是悄悄留空
        return out

    prefix_parts: list[str] = []
    opener = next((o for o in hedges["openers"] if o["id"] == opener_id), None)
    if opener and opener["text"]:
        prefix_parts.append(fill(opener["text"]))

    for bid in buffer_ids:
        b = next((x for x in hedges["buffers"] if x["id"] == bid), None)
        if b:
            prefix_parts.append(fill(b["text"]))

    closing = next((c for c in hedges["closings"] if c["id"] == closing_id), None)
    suffix = fill(closing["text"]) if closing and closing["text"] else ""

    composed = "".join(prefix_parts) + body
    if suffix:
        composed = composed.rstrip() + ("" if composed.rstrip().endswith(("。", "！", "？", "!", "?")) else "。")
        composed += suffix

    unfilled = re.findall(r"\{(\w+)\}", composed)
    return {
        "ok": True,
        "original": text,
        "composed": composed,
        "unfilled_slots": sorted(set(unfilled)),
        "applied": {
            "opener": opener_id,
            "closing": closing_id,
            "buffers": buffer_ids,
            "reorder_index": reorder_index,
        },
    }
