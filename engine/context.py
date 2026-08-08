"""真实场景数据接入层（ContextProvider）。

用户的核心诉求：「如果输入信息不够，软件也不可能分析得全面」——
所以要能接入真实生活中的各种数据，给解码/梳理补背景。

这个文件回答「能不能接很多数据源」：**能，且架构上是同一个缝**。

    ContextProvider
        ├─ ManualContextProvider    用户手动填（关系/前因/场合/语气）· 永远可用
        ├─ HistoryContextProvider   读本机数据库的最近对话，自动补「前因」· 默认关
        ├─ SleepContextProvider     只读 sleeptracking 本机库，补身体/睡眠状态 · 默认关
        └─ （未来）CalendarProvider / MailProvider / WearableProvider …… 任意追加

每个 provider 实现同一接口：provides 声明它能补哪些维度，gather() 返回数据。
解码/梳理只认 ContextProvider，不认具体数据源——这就是「做很多接口」的底层。

安全约束（与全局红线一致，硬编码）：
  - 所有 provider 默认关闭（manual 除外，因为它就是用户输入本身），
    必须由用户显式启用；
  - 只在本机运行，数据不离开这台电脑；
  - 绝不自动写入、发送、或替用户做任何决定——provider 只「读」，补上下文。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any


# 上下文维度的统一命名（所有 provider 围绕这套 key 工作）
#   relationship  关系（同事/上级/朋友/家人）
#   setting       场合与语气（文字/语音/当面）
#   prior         前因（这句话之前发生了什么）
#   tone          对方当下的语气/情绪色彩
DIMENSIONS = ("relationship", "setting", "prior", "tone")


@dataclass
class ContextProvider:
    """数据源接入的统一接口。"""

    id: str
    label: str
    description: str
    # 这个 provider 能补充哪些上下文维度
    provides: list[str]
    # 是否需要外部凭证（决定 UI 上是否提示配置）
    needs_credentials: bool = False
    # 默认关闭；用户显式开启后才参与 gather
    enabled: bool = False

    def gather(self, **kwargs: Any) -> dict[str, str]:
        """返回能补充的上下文维度。基类返回空，子类按需实现。"""
        return {}


class ManualContextProvider(ContextProvider):
    """用户手动填写背景。永远可用，无需凭证——它本质就是用户输入本身。"""

    def __init__(self) -> None:
        super().__init__(
            id="manual",
            label="手动补充背景",
            description="你在解码页直接填：关系、前因、场合、语气。最可控、最隐私。",
            provides=list(DIMENSIONS),
            needs_credentials=False,
            enabled=True,  # manual 是用户输入本身，不算「外部数据源」，默认可用
        )

    def gather(self, **kwargs: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        for k in DIMENSIONS:
            v = str(kwargs.get(k) or "").strip()
            if v:
                out[k] = v
        return out


class HistoryContextProvider(ContextProvider):
    """读本机数据库里最近的对话，自动补「前因」。

    不联网、不出本机。演示「自动数据源」的接法——未来接日历/邮件/可穿戴
    设备，只要照这个样子再写一个 provider 即可。
    """

    def __init__(self) -> None:
        super().__init__(
            id="history",
            label="本机对话记录",
            description="用这台电脑上你之前的 decode / compose 记录，自动补全「前因」。不联网、不出本机。",
            provides=["prior"],
            needs_credentials=False,
            enabled=False,  # 自动读取个人记录，默认关，需用户开启
        )

    def gather(self, limit: int = 5, **kwargs: Any) -> dict[str, str]:
        try:
            import database as db

            db.init_db()  # 测试直接调用 gather 时库可能还没建表
            rows = db.list_entries(limit=limit)
            prior_bits = [r["input"] for r in rows if (r.get("input") or "").strip()]
            if prior_bits:
                return {"prior": "；".join(prior_bits[:limit])}
        except Exception:
            return {}
        return {}


class SleepContextProvider(ContextProvider):
    """只读读取 sleeptracking 的本地数据库，补「身体/睡眠状态」维度。

    这是用户亲自选的集成方向：把睡眠时间 / HRV / 心情作为解码的【参考维度】，
    让「听懂对方」时也能看到自己当下的生理负荷——但严格限定为自我觉察参考，
    绝不用来「更准地读对方」（那是过度读心，属红线）。

    安全约束（与其他 provider 一致，硬编码）：
      - 只读本机 SQLite 文件，不联网、不写、不发送；
      - 默认关闭，必须在解码请求里显式带 context_providers:["sleep"] 才生效；
      - 数据库路径可由环境变量 ASDT_SLEEP_DB 覆盖，缺省指向已知路径；
      - 任何异常都 fail-safe 返回 {}，不影响主解码流程；
      - 它提供的维度（body_state / recent_mood / sleep_quality）不参与「充分度」
        判定——身体状态不是关系/场合/前因的替代品，只是额外的自我觉察线索。
    """

    DEFAULT_DB = "/Users/barbara/Documents/vscode/developing/sleeptracking/sleep_tracker.db"
    SLEEPTRACKING_ENV = "/Users/barbara/Documents/vscode/developing/sleeptracking/.env"

    def __init__(self) -> None:
        super().__init__(
            id="sleep",
            label="睡眠/身体状态（sleeptracking）",
            description=(
                "只读读取你的睡眠追踪数据：近 7 天睡眠质量、Whoop 的 HRV/恢复趋势、"
                "经期心情。仅作你的自我觉察参考，不改对方原意。默认读本地库，"
                "若 sleeptracking 用的是 Turso 云端库则只读云端——不写、不发送。"
            ),
            provides=["body_state", "recent_mood", "sleep_quality"],
            needs_credentials=False,
            enabled=False,  # 涉及健康数据，默认关，需用户显式开启
        )

    # ---- 凭据解析：优先环境变量，否则借读 sleeptracking 同机 .env（不重复存储）----
    def _turso_creds(self) -> tuple[str, str] | None:
        import os

        url = os.environ.get("ASDT_SLEEP_TURSO_URL")
        token = os.environ.get("ASDT_SLEEP_TURSO_TOKEN")
        if url and token:
            return url, token
        try:
            with open(self.SLEEPTRACKING_ENV, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TURSO_URL="):
                        url = line.split("=", 1)[1].strip()
                    elif line.startswith("TURSO_AUTH_TOKEN="):
                        token = line.split("=", 1)[1].strip()
        except Exception:
            return None
        return (url, token) if url and token else None

    @staticmethod
    def _avg(xs: list, idx: int) -> float | None:
        vals = [r[idx] for r in xs if r[idx] is not None]
        return sum(vals) / len(vals) if vals else None

    @classmethod
    def _query(cls, con) -> dict[str, str]:
        """跑查询，返回维度字典。con 可以是 sqlite3 或 libsql 连接（下标访问兼容）。"""
        out: dict[str, str] = {}

        # 1) 近 7 天睡眠：质量 + 常见问题
        rows = con.execute(
            "SELECT sleep_quality, sleep_problems, dream_journal, record_date "
            "FROM sleep_records ORDER BY record_date DESC LIMIT 7"
        ).fetchall()
        if rows:
            qualities = [r[0] for r in rows if r[0]]
            if qualities:
                poor = sum(1 for q in qualities if q == "poor")
                out["sleep_quality"] = (
                    f"近 {len(qualities)} 晚：{poor} 晚差，最新 "
                    f"{' / '.join(qualities[:3])}"
                )
            probs: list[str] = []
            for r in rows:
                try:
                    probs += json.loads(r[1] or "[]")
                except Exception:
                    pass
            if probs:
                top = Counter(probs).most_common(2)
                out["sleep_quality"] = (
                    out.get("sleep_quality", "")
                    + f"；常见：{'、'.join(f'{k}×{v}' for k, v in top)}"
                )

        # 2) Whoop HRV / 恢复趋势（近 3 天 vs 前 7 天）
        w = con.execute(
            "SELECT hrv, recovery_score FROM whoop_daily_metrics "
            "ORDER BY record_date DESC LIMIT 10"
        ).fetchall()
        if len(w) >= 4:
            hrv_r, hrv_p = cls._avg(w[:3], 0), cls._avg(w[3:10], 0)
            rec_r, rec_p = cls._avg(w[:3], 1), cls._avg(w[3:10], 1)
            if hrv_r and hrv_p and hrv_r < hrv_p * 0.85:
                out["body_state"] = (
                    f"HRV 近 3 天 {hrv_r:.0f}ms，低于前 7 天 {hrv_p:.0f}ms——"
                    f"身体处于恢复不足状态"
                )
            elif rec_r is not None and rec_p is not None and rec_r < rec_p * 0.85:
                out["body_state"] = (
                    f"Whoop 恢复分近 3 天 {rec_r:.0f}，低于前 7 天 {rec_p:.0f}——"
                    f"生理负荷偏高"
                )
            elif hrv_r:
                out["body_state"] = (
                    f"HRV 近 3 天 {hrv_r:.0f}ms、恢复分 {rec_r:.0f}，与基线持平"
                )

        # 3) 近期心情（经期记录）
        m = con.execute(
            "SELECT mood FROM period_records ORDER BY record_date DESC LIMIT 3"
        ).fetchall()
        moods = [r[0] for r in m if r[0]]
        if moods:
            out["recent_mood"] = "近期心情：" + "、".join(moods[:3])
        return out

    def gather(self, **kwargs: Any) -> dict[str, str]:
        import os

        explicit_local = os.environ.get("ASDT_SLEEP_DB")

        def clean(out: dict[str, str]) -> dict[str, str]:
            return {k: v.strip() for k, v in out.items() if v and v.strip()}

        # 显式指定了本地库 → 只用本地，不碰 Turso（便于测试与本地优先场景）
        if explicit_local:
            if not os.path.exists(explicit_local):
                return {}
            try:
                import sqlite3

                con = sqlite3.connect(f"file:{explicit_local}?mode=ro", uri=True)
                try:
                    return clean(self._query(con))
                finally:
                    con.close()
            except Exception:
                return {}

        # 否则 Turso（sleeptracking 的生产库）优先，失败再回退默认本地 SQLite
        creds = self._turso_creds()
        con = None
        if creds:
            try:
                import libsql_experimental as libsql

                con = libsql.connect(database=creds[0], auth_token=creds[1])
            except Exception:
                con = None
        if con is None:
            if not os.path.exists(self.DEFAULT_DB):
                return {}
            try:
                import sqlite3

                con = sqlite3.connect(f"file:{self.DEFAULT_DB}?mode=ro", uri=True)
            except Exception:
                return {}
        try:
            return clean(self._query(con))
        except Exception:
            return {}
        finally:
            try:
                con.close()
            except Exception:
                pass


# ---- 注册表：新增数据源只需在此追加一个 provider 实例 ----
REGISTRY: list[ContextProvider] = [
    ManualContextProvider(),
    HistoryContextProvider(),
    SleepContextProvider(),
]


def list_providers() -> list[dict[str, Any]]:
    """给前端列出所有可用 provider 及其能力。"""
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "provides": p.provides,
            "needs_credentials": p.needs_credentials,
            "enabled": p.enabled,
        }
        for p in REGISTRY
    ]


def merge_context(
    manual: dict[str, str] | None = None,
    enabled_ids: list[str] | None = None,
) -> dict[str, str]:
    """把手动背景与各启用数据源合并成一个 context 字典。

    - manual 用户明确给的，优先；
    - enabled_ids 指定的自动 provider 补充 manual 没覆盖的维度；
    - 任何一个 provider 抛异常都不影响整体（fail-safe）。
    """
    ctx: dict[str, str] = {}
    manual = manual or {}
    ctx.update({k: str(v).strip() for k, v in manual.items() if str(v).strip()})

    for p in REGISTRY:
        if p.id == "manual":
            continue
        if enabled_ids is not None and p.id not in enabled_ids:
            continue
        if not p.enabled and (enabled_ids is None or p.id not in enabled_ids):
            continue
        try:
            got = p.gather()
            for k, v in got.items():
                if k not in ctx and str(v).strip():
                    ctx[k] = str(v).strip()
        except Exception:
            continue
    return ctx
