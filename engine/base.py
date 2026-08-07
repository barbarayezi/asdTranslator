"""引擎公共数据结构与知识库加载。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import config


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    """加载 data/ 下的 JSON 知识库（带缓存）。"""
    path: Path = config.DATA_DIR / f"{name}.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def reload_all() -> None:
    load.cache_clear()


@dataclass
class Reading:
    """一个候选解读。永远不是唯一答案 —— Milton (2012)。"""

    text: str
    confidence: float
    when: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Hit:
    """在文本中命中的一个知识点。"""

    entry_id: str
    matched: str
    start: int
    end: int
    category: str
    category_label: str
    tone: str
    literal: str
    readings: list[Reading] = field(default_factory=list)
    probe: str = ""
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["readings"] = [r.to_dict() for r in self.readings]
        return d


@dataclass
class Flag:
    """ASD→NT 方向的标记。注意措辞：不是错误，是「可能被读作」。"""

    flag_id: str
    label: str
    matched: str
    start: int
    end: int
    nt_reading: str
    severity: str
    keep_note: str
    rewrites: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SENTENCE_ENDINGS = "。！？!?；;\n"


def split_sentences(text: str) -> list[str]:
    """按中文标点切句，保留标点。"""
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in SENTENCE_ENDINGS:
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def find_all(text: str, needle: str) -> list[tuple[int, int]]:
    """返回 needle 在 text 中所有出现的 (start, end)。"""
    spans: list[tuple[int, int]] = []
    if not needle:
        return spans
    idx = text.find(needle)
    while idx != -1:
        spans.append((idx, idx + len(needle)))
        idx = text.find(needle, idx + 1)
    return spans


def dedupe_overlaps(hits: list[Any]) -> list[Any]:
    """同一区间被多条规则命中时，保留匹配串最长的那条。"""
    hits = sorted(hits, key=lambda h: (h.start, -(h.end - h.start)))
    kept: list[Any] = []
    for h in hits:
        if any(h.start >= k.start and h.end <= k.end for k in kept):
            continue
        kept.append(h)
    return sorted(kept, key=lambda h: h.start)
