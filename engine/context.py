"""真实场景数据接入层（ContextProvider）。

用户的核心诉求：「如果输入信息不够，软件也不可能分析得全面」——
所以要能接入真实生活中的各种数据，给解码/梳理补背景。

这个文件回答「能不能接很多数据源」：**能，且架构上是同一个缝**。

    ContextProvider
        ├─ ManualContextProvider    用户手动填（关系/前因/场合/语气）· 永远可用
        ├─ HistoryContextProvider   读本机数据库的最近对话，自动补「前因」· 默认关
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

            rows = db.list_entries(limit=limit)
            prior_bits = [r["input"] for r in rows if (r.get("input") or "").strip()]
            if prior_bits:
                return {"prior": "；".join(prior_bits[:limit])}
        except Exception:
            return {}
        return {}


# ---- 注册表：新增数据源只需在此追加一个 provider 实例 ----
REGISTRY: list[ContextProvider] = [
    ManualContextProvider(),
    HistoryContextProvider(),
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
