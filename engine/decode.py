"""NT → ASD 解码器。

把对方说的话拆成：陈述的事实 / 表达的情绪 / 真实想让你做的事，
并标出软性拒绝、反语、模糊量词、隐含请求等。

关键架构约束（来自 TwIPS, Haroon & Dogar 2024）：
本模块只在**你这一侧**工作，不需要对话另一方安装任何东西或配合使用
tone indicator。这是它相对于「让 NT 标注语气」类方案的核心优势。
"""
from __future__ import annotations

import re
from typing import Any

from engine.base import Hit, Reading, dedupe_overlaps, find_all, load, split_sentences

# 事实性线索：数字、日期、专有名词式表达
_FACT_HINTS = re.compile(
    r"(\d+[\d.:：/年月日号点分%％]*|上周|本周|下周|昨天|今天|明天|周[一二三四五六日天])"
)
# 情绪性线索
_EMO_HINTS = re.compile(
    r"(觉得|感觉|担心|希望|失望|生气|着急|烦|累|难受|无奈|可惜|遗憾|开心|高兴|不满|郁闷|无语)"
)
# 行动性线索
_ACT_HINTS = re.compile(
    r"(能不能|可不可以|麻烦|需要|请|要求|得|应该|最好|建议|帮我|给我|发我|改|做|完成|提交|确认|回复|安排)"
)


def _match_phrases(text: str, scene: str, min_conf: float) -> list[Hit]:
    """匹配 NT 惯用语。

    两种模式：
      patterns        —— 子串匹配，出现在任何位置都算
      exact_patterns  —— 整条消息完全等于该串时才算

    exact 模式是必需的：像「哦」「。」「牛」这类，只有当它是**整条回复**时
    才携带冷淡/敷衍的含义。作为子串它们会在几乎每句话里误报。
    """
    kb = load("nt_phrases")
    cats = kb["categories"]
    stripped = text.strip()
    hits: list[Hit] = []

    for entry in kb["entries"]:
        if scene and entry.get("scenes") and scene not in entry["scenes"]:
            continue
        cat = cats.get(entry["category"], {})
        readings = [
            Reading(r["text"], r["confidence"], r.get("when", ""))
            for r in entry["readings"]
            if r["confidence"] >= min_conf
        ]
        if not readings:
            continue
        readings.sort(key=lambda r: -r.confidence)

        def mk(pat: str, start: int, end: int) -> Hit:
            return Hit(
                entry_id=entry["id"],
                matched=pat,
                start=start,
                end=end,
                category=entry["category"],
                category_label=cat.get("label", entry["category"]),
                tone=cat.get("tone", "info"),
                literal=entry.get("literal", ""),
                readings=readings,
                probe=entry.get("probe", ""),
                why=cat.get("why", ""),
            )

        for pat in entry.get("patterns", []):
            for start, end in find_all(text, pat):
                hits.append(mk(pat, start, end))

        for pat in entry.get("exact_patterns", []):
            if stripped == pat:
                hits.append(mk(pat, 0, len(text)))

    return dedupe_overlaps(hits)


def _match_vague(text: str, speaker_overrides: dict[str, str] | None = None) -> list[dict[str, Any]]:
    kb = load("vague_terms")
    overrides = speaker_overrides or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in kb["entries"]:
        for pat in entry["patterns"]:
            if pat not in text or entry["id"] in seen:
                continue
            seen.add(entry["id"])
            personal = overrides.get(entry["id"])
            out.append(
                {
                    "id": entry["id"],
                    "matched": pat,
                    "dimension": entry["dimension"],
                    "range": personal or entry["default_range"],
                    "is_personal": bool(personal),
                    "default_range": entry["default_range"],
                    "spread": entry["spread"],
                    "clarify": entry["clarify"],
                }
            )
            break
    return out


def _decompose(text: str) -> dict[str, list[str]]:
    """把整段话拆成 事实 / 情绪 / 行动诉求 三层。

    对应研究摘要里的「情绪 vs 事实分离」需求。规则是启发式的，
    目的是提供一个起点，不是权威切分 —— UI 上会如实说明这一点。
    """
    facts: list[str] = []
    emotions: list[str] = []
    actions: list[str] = []
    for sent in split_sentences(text):
        scored = False
        if _ACT_HINTS.search(sent):
            actions.append(sent)
            scored = True
        if _EMO_HINTS.search(sent):
            emotions.append(sent)
            scored = True
        if _FACT_HINTS.search(sent) or not scored:
            facts.append(sent)
    return {"facts": facts, "emotions": emotions, "actions": actions}


def _summarize(
    hits: list[Hit],
    vague: list[dict[str, Any]],
    text: str = "",
    context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """汇总层。

    新增「充分度」信号，正面回应「输入不够就分析不全面」：
      - completeness: thin / partial / adequate
          thin      —— 文本本身太短，引擎无事可做
          partial   —— 文本够长，但缺关键背景维度（关系/场合/前因）
          adequate  —— 关键背景都给了，可以放心按字面理解
      - missing_context: 还缺哪些维度，以及「对方有没有给具体时间或条件」
                         （拒绝/边界类命中时必问，因为那是区分拒绝与缓兵之计的关键）

    关键约束：没有给足背景时，绝不声称「大概率字面」——那样会让人误以为
    分析已经完整，其实只是没东西可分析（正是用户指出的盲点）。
    """
    tones = [h.tone for h in hits]
    if "alert" in tones:
        level = "alert"
    elif "warn" in tones:
        level = "warn"
    elif hits or vague:
        level = "info"
    else:
        level = "clear"

    context = context or {}
    # 缺失这些维度，解码会偏猜
    dims = [
        ("relationship", "你们的关系（同事 / 上级 / 朋友 / 家人）"),
        ("setting", "说这话的场合和语气（文字 / 语音 / 当面）"),
        ("prior", "这句话之前发生了什么（前因）"),
    ]
    missing_context = [label for key, label in dims if not str(context.get(key, "")).strip()]

    refusal = [h for h in hits if h.category == "soft_refusal"]
    boundary = [h for h in hits if h.category == "boundary"]
    if refusal or boundary:
        missing_context.append(
            "对方有没有给出具体的时间或条件（用来判断到底是拒绝，还是缓兵之计）"
        )

    stripped = text.strip()
    # 极短且零命中 —— 引擎真的无事可做
    thin = len(stripped) <= 4 and not hits and not vague

    if thin:
        completeness = "thin"
    elif missing_context:
        completeness = "partial"
    else:
        completeness = "adequate"

    headline_map = {
        "alert": "这段话里有字面意思和真实意思不一致的地方，建议逐条看。",
        "warn": "有几处可能不是字面意思，下面列了候选解读。",
        "info": "整体比较直白，有少量需要留意的措辞。",
        "clear": "没有匹配到常见的潜台词模式。",
    }
    if completeness == "thin":
        headline = (
            "信息太少，我只能给按字面的最可能解读。补全背景（关系 / 场合 / 前因）会更准——"
            "但这不保证对方真是那个意思。"
        )
    elif completeness == "partial":
        headline = headline_map[level] + "有背景信息缺失，下面列了补全后能更准的地方。"
    else:
        headline = (
            headline_map[level] + "结合你给的背景，这段话可以按字面理解，但仍需结合你对这个人的了解。"
        )

    priority: list[str] = []
    if boundary:
        priority.append("检测到边界信号——建议优先处理这一条，其他的可以之后再看。")
    if refusal:
        priority.append(
            f"有 {len(refusal)} 处可能是软性拒绝。判断的关键通常是：对方有没有给出具体的时间或条件。"
        )
    return {
        "level": level,
        "completeness": completeness,
        "headline": headline,
        "priority": priority,
        "missing_context": missing_context,
        "counts": {
            "phrases": len(hits),
            "vague": len(vague),
        },
    }


def decode(
    text: str,
    scene: str = "work",
    min_confidence: float = 0.2,
    speaker_overrides: dict[str, str] | None = None,
    context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """主入口。返回结构化解码结果。

    context: 可选的背景维度（relationship / setting / prior / tone），
    来自用户手动填写或已启用的数据源 provider。只用于「充分度」判断，
    不凭空改写解读——背景缺失时我们提示缺什么，而不是假装读懂了。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty"}

    hits = _match_phrases(text, scene, min_confidence)
    vague = _match_vague(text, speaker_overrides)
    layers = _decompose(text)

    return {
        "ok": True,
        "engine": "rules",
        "original": text,
        "scene": scene,
        "summary": _summarize(hits, vague, text, context),
        "hits": [h.to_dict() for h in hits],
        "vague": vague,
        "layers": layers,
        "disclaimer": (
            "以上是候选解读，不是标准答案。潜台词由对话双方共同建构，"
            "同一句话在不同关系和语境下含义可能完全不同（Milton 2012）。"
            "请结合你对这个人的了解判断。"
        ),
    }
