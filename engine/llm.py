"""可插拔 LLM 适配层（OpenAI Chat Completions 兼容）。

设计：
  - 未配置 API Key 时整层静默不可用，规则引擎独立工作，功能不残废。
  - 所有输出强制过 guardrails；命中 block 级违规则带着违规说明重试，
    重试仍失败就丢弃 LLM 结果、回落到规则引擎。这是 Jang et al. (2024) §5.2
    的工程化落实：不信任 prompt，只信任输出检测。
"""
from __future__ import annotations

import json
from typing import Any

import config
from engine import guardrails

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


SYSTEM_PROMPT = """你是一个 ASD（阿斯伯格/自闭谱系）与 NT（神经典型者）之间的双向沟通翻译助手。

你的理论立场（必须严格遵守）：
- 沟通断裂是双向的错配，不是 ASD 用户的缺陷（Milton 2012 双重共情问题）。
- ASD 用户的原话在表意上是清楚的。你只描述它在 NT 解码习惯下可能产生的偏差。
- 潜台词由双方共同建构，不存在唯一正确解读。永远给多个候选 + 置信度。

绝对禁止（违反会被自动拦截）：
1. 任何身体行为建议：眼神接触、微笑、语调、坐姿、表情、肢体语言。
2. 任何伪装诱导：不要说"显得更自然""更像正常人""融入大家""提升情商"。
3. 不要建议用户披露自闭症/ADHD 诊断——那是高风险的个人决定，不由你替他做。
4. 不要给单一乐观预设。凡涉及沟通结果，必须给顺利/僵持/拒绝三个分支。
5. 不要说用户"表达有问题""说错了""需要改进"。只说"在 NT 语境中可能被读作 X"。
6. 不做任何能力评分、进步度量、社交水平评级。
7. 不要用"对方就是在…""显然是…"这类确定性读心表述。

风格要求：
- 具体、可执行、简短。给句子，不给原则。
- 每个改写建议都要说明：这样改换来什么，损失什么。
- 用简体中文。
- 严格输出 JSON，不要任何额外文字或 markdown 代码块标记。"""


DECODE_SCHEMA = """输出 JSON：
{
  "readings": [{"text": "候选解读", "confidence": 0.0-1.0, "when": "什么条件下这个解读成立"}],
  "hidden_request": "对方真正想让你做的事，没有则空字符串",
  "emotional_subtext": "对方可能在传递但没明说的情绪，没有则空字符串",
  "suggested_probe": "一句可以直接说出口的追问，用来验证你的判断",
  "reply_options": [{"label": "选项名", "text": "可直接发送的回复"}]
}"""

COMPOSE_SCHEMA = """输出 JSON：
{
  "nt_readings": [{"fragment": "原文片段", "may_be_read_as": "在NT语境中可能被读作什么", "keep_note": "什么情况下保持原样更好"}],
  "variants": [{"label": "变体名", "text": "改写后的完整文本", "gain": "换来什么", "cost": "损失什么"}],
  "layered": {"conclusion": "一句话结论", "points": ["要点1","要点2"], "detail_note": "细节如何处理"},
  "branches": [{"key":"smooth|stall|refused","label":"","action":"对方这样反应时你可以说什么"}]
}"""


def available() -> bool:
    if config.LLM_ENABLED == "off":
        return False
    if requests is None:
        return False
    return bool(config.LLM_API_KEY)


def status() -> dict[str, Any]:
    return {
        "available": available(),
        "reason": (
            "未安装 requests" if requests is None
            else "已关闭" if config.LLM_ENABLED == "off"
            else "未配置 API Key" if not config.LLM_API_KEY
            else "就绪"
        ),
        "model": config.LLM_MODEL,
        "base_url": config.LLM_BASE_URL,
    }


def _call(messages: list[dict[str, str]]) -> str:
    resp = requests.post(  # type: ignore[union-attr]
        f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.LLM_MODEL,
            "messages": messages,
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        },
        timeout=config.LLM_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _parse_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.lower().startswith("json") else raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _guarded_call(user_prompt: str) -> dict[str, Any]:
    """调用 LLM 并强制过红线检查，必要时带违规反馈重试。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    last_check: dict[str, Any] = {}

    for attempt in range(config.LLM_MAX_RETRY_ON_GUARDRAIL + 1):
        try:
            raw = _call(messages)
        except Exception as exc:  # 网络/鉴权/超时统一降级
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "stage": "request"}

        check = guardrails.inspect(raw)
        last_check = check
        if not check["blocked"]:
            data = _parse_json(raw)
            if data is None:
                return {"ok": False, "error": "LLM 返回的不是合法 JSON", "stage": "parse"}
            return {
                "ok": True,
                "data": data,
                "guardrail": check,
                "attempts": attempt + 1,
            }

        # 命中 block 级红线，把违规点回喂给模型重试
        detail = "；".join(
            f"「{v['matched']}」触碰了红线「{v['label']}」——{v['fix_hint']}"
            for v in check["violations"]
            if v["severity"] == "block"
        )
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": f"你的上一次输出违反了硬性约束：{detail}\n请完全重写，去掉所有违规内容，仍然输出同样结构的 JSON。",
            }
        )

    return {
        "ok": False,
        "error": "多次生成均触碰红线，已丢弃 LLM 结果",
        "stage": "guardrail",
        "guardrail": last_check,
    }


def decode(text: str, scene: str, rule_result: dict[str, Any]) -> dict[str, Any]:
    if not available():
        return {"ok": False, "error": "LLM 未配置", "stage": "config"}
    prompt = f"""场景：{config.SCENES.get(scene, scene)}

对方说的话：
\"\"\"{text}\"\"\"

规则引擎已识别出的模式（供参考，可以补充或修正）：
{json.dumps(rule_result.get('hits', [])[:6], ensure_ascii=False)}

请帮 ASD 用户解码这段话。{DECODE_SCHEMA}"""
    return _guarded_call(prompt)


def compose(text: str, scene: str, rule_result: dict[str, Any]) -> dict[str, Any]:
    if not available():
        return {"ok": False, "error": "LLM 未配置", "stage": "config"}
    prompt = f"""场景：{config.SCENES.get(scene, scene)}

ASD 用户想说的话（原文，表意本身是清楚的）：
\"\"\"{text}\"\"\"

规则引擎已标记的点（供参考）：
{json.dumps(rule_result.get('flags', [])[:6], ensure_ascii=False)}

请标注这段话在 NT 语境中可能被读作什么，并给出可选的改写变体。
记住：不是纠错，是翻译。每个变体都要说明 gain 和 cost。{COMPOSE_SCHEMA}"""
    return _guarded_call(prompt)


def clarify(raw_feeling: str, context: str = "") -> dict[str, Any]:
    """自由文本版的思路梳理，作为结构化流程之外的补充入口。"""
    if not available():
        return {"ok": False, "error": "LLM 未配置", "stage": "config"}
    prompt = f"""ASD 用户描述的状态（可能很零散、不成句，这是正常的）：
\"\"\"{raw_feeling}\"\"\"
{f'背景：{context}' if context else ''}

请帮他梳理。注意：很多 ASD 用户有述情障碍，无法直接命名情绪，
所以要从他描述的具体细节出发，给出候选，而不是断言他的感受。
最终必须落到一个可执行的诉求上——情绪命名只是中间产物。

输出 JSON：
{{
  "restated": "把他说的事情用清晰的时间顺序复述一遍",
  "candidate_emotions": [{{"label":"情绪词","evidence":"从他哪句话推出来的"}}],
  "candidate_needs": [{{"label":"可能的诉求","sentence":"可以直接说出口的一句话"}}],
  "unclear_points": ["还需要他自己确认的点"],
  "branches": [{{"key":"smooth|stall|refused","label":"","action":""}}]
}}"""
    return _guarded_call(prompt)
