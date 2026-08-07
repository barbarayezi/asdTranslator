"""SQLite 数据层。所有数据留在本机。

只存三类东西：
  prefs          — 用户偏好
  speaker_profile— 说话人档案（用于把「尽快」校准成这个人的真实区间）
  entry          — 历史记录（用户可随时清空；不做任何统计评分）
  custom_phrase  — 用户自建词条

明确不存：任何形式的能力评分、进步曲线、使用频率排行。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS prefs (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS speaker_profile (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    relation   TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    overrides  TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS entry (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    scene      TEXT DEFAULT 'work',
    speaker_id INTEGER,
    input      TEXT NOT NULL,
    output     TEXT DEFAULT '',
    payload    TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS custom_phrase (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern    TEXT NOT NULL,
    category   TEXT DEFAULT 'soft_refusal',
    reading    TEXT NOT NULL,
    confidence REAL DEFAULT 0.6,
    speaker_id INTEGER,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_entry_kind ON entry(kind, created_at DESC);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------- prefs ----------

def get_prefs() -> dict[str, Any]:
    prefs = dict(config.DEFAULT_PREFS)
    with get_conn() as conn:
        for row in conn.execute("SELECT key, value FROM prefs"):
            try:
                prefs[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                prefs[row["key"]] = row["value"]
    return prefs


def set_prefs(updates: dict[str, Any]) -> dict[str, Any]:
    with get_conn() as conn:
        for k, v in updates.items():
            conn.execute(
                "INSERT INTO prefs(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, json.dumps(v, ensure_ascii=False)),
            )
    return get_prefs()


# ---------- speakers ----------

def list_speakers() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM speaker_profile ORDER BY name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["overrides"] = json.loads(d.get("overrides") or "{}")
        out.append(d)
    return out


def upsert_speaker(
    name: str, relation: str = "", notes: str = "", overrides: dict[str, str] | None = None
) -> dict[str, Any]:
    payload = json.dumps(overrides or {}, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO speaker_profile(name, relation, notes, overrides) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET relation=excluded.relation, "
            "notes=excluded.notes, overrides=excluded.overrides",
            (name, relation, notes, payload),
        )
        row = conn.execute("SELECT * FROM speaker_profile WHERE name=?", (name,)).fetchone()
    d = dict(row)
    d["overrides"] = json.loads(d.get("overrides") or "{}")
    return d


def get_speaker(speaker_id: int | None) -> dict[str, Any] | None:
    if not speaker_id:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM speaker_profile WHERE id=?", (speaker_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["overrides"] = json.loads(d.get("overrides") or "{}")
    return d


def delete_speaker(speaker_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM speaker_profile WHERE id=?", (speaker_id,))


# ---------- entries ----------

def add_entry(
    kind: str,
    input_text: str,
    output_text: str = "",
    scene: str = "work",
    speaker_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO entry(kind, scene, speaker_id, input, output, payload) VALUES(?,?,?,?,?,?)",
            (
                kind,
                scene,
                speaker_id,
                input_text,
                output_text,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
    return int(cur.lastrowid)


def list_entries(kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql = "SELECT * FROM entry"
    args: list[Any] = []
    if kind:
        sql += " WHERE kind=?"
        args.append(kind)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        out.append(d)
    return out


def delete_entry(entry_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM entry WHERE id=?", (entry_id,))


def clear_entries() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM entry")


# ---------- custom phrases ----------

def list_custom_phrases() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM custom_phrase ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def add_custom_phrase(
    pattern: str, reading: str, category: str = "soft_refusal",
    confidence: float = 0.6, speaker_id: int | None = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO custom_phrase(pattern, category, reading, confidence, speaker_id) VALUES(?,?,?,?,?)",
            (pattern, category, reading, confidence, speaker_id),
        )
    return int(cur.lastrowid)


def delete_custom_phrase(phrase_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM custom_phrase WHERE id=?", (phrase_id,))
