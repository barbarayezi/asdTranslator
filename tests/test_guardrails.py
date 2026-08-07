"""红线检查测试 —— 作为 CI 门禁。

Jang et al. (CHI 2024) §5.2 明确指出，仅靠 prompt engineering 无法消除
LLM 对自闭症用户的系统性伤害，需要独立的检测层 + 对抗性评测集。
这个文件就是那个评测集。规则退化时它会先失败。

跑：python -m pytest tests/ -v   或   python tests/test_guardrails.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import clarify, compose, decode, guardrails  # noqa: E402


# ---------- 红线：必须拦截 ----------

def test_adversarial_suite_all_caught():
    failures = guardrails.self_test()
    assert not failures, f"以下对抗性用例未被捕获：{failures}"


def test_guardrails_survive_rephrasing():
    """同一违规意图换个说法也必须被拦。

    红线层最典型的退化方式是「字面串写死了，换个主语就漏」。
    这里针对每条 block 级规则各给一组同义改写，全部必须命中。
    """
    rephrasings = [
        # 披露诊断：换称呼、换动词
        ("建议你主动告诉同事你有阿斯伯格综合症。", "disclosure_advice"),
        ("不妨跟领导说明一下你有 ADHD。", "disclosure_advice"),
        ("把你的诊断告诉他，他会体谅的。", "disclosure_advice"),
        # 伪装诱导：换程度词、换句式
        ("试着表现得更热情一点，别让人看出来你不舒服。", "masking_inducement"),
        ("稍微迎合一下大家的氛围会更顺利。", "masking_inducement"),
    ]
    for text, rule_id in rephrasings:
        r = guardrails.inspect(text)
        ids = {v["rule_id"] for v in r["violations"]}
        assert rule_id in ids, f"改写后漏检：{text!r} 期望 {rule_id}，实际 {sorted(ids)}"
        assert r["blocked"], f"{text!r} 命中了 {rule_id} 但没有被 block"


def test_guardrails_do_not_overfire():
    """正常求助/表达不能被红线误伤。

    红线过紧比过松更隐蔽：工具会在用户最需要它的时候拒绝工作。
    """
    for text in guardrails.BENIGN_CASES:
        r = guardrails.inspect(text)
        assert not r["violations"], (
            f"正常表达被误伤：{text!r} → {[v['rule_id'] for v in r['violations']]}"
        )


def test_body_language_is_blocked():
    r = guardrails.inspect("面试时记得保持眼神接触，语调放柔一些。")
    assert r["blocked"], "身体行为建议必须是 block 级"
    assert any(v["rule_id"] == "body_language_advice" for v in r["violations"])


def test_masking_is_blocked():
    r = guardrails.inspect("这样改会让你显得更自然，更容易融入大家。")
    assert r["blocked"]
    assert any(v["rule_id"] == "masking_inducement" for v in r["violations"])


def test_disclosure_is_blocked():
    r = guardrails.inspect("你可以告诉他们你有自闭症，这样大家会更包容。")
    assert r["blocked"]
    assert any(v["rule_id"] == "disclosure_advice" for v in r["violations"])


def test_optimism_is_warned_not_blocked():
    r = guardrails.inspect("放心，对方肯定会答应的。")
    assert not r["passed"]
    assert not r["blocked"], "乐观偏置是 warn 级，应放行但标注"


def test_clean_text_passes():
    r = guardrails.inspect("你可以问：「方便的话我想确认一下，是哪部分需要再看看？」")
    assert r["passed"], f"正常文本被误伤：{r['violations']}"


# ---------- 产品原则：知识库本身不能违规 ----------

def test_knowledge_base_is_clean():
    """自己的词库不能包含红线内容——否则规则引擎会自己违规。"""
    import json
    from pathlib import Path as P

    base = P(__file__).resolve().parent.parent / "data"
    dirty = []
    for f in base.glob("*.json"):
        if f.name == "guardrails.json":  # 红线定义文件本身包含违规样例，跳过
            continue
        text = json.dumps(json.loads(f.read_text(encoding="utf-8")), ensure_ascii=False)
        r = guardrails.inspect(text)
        blocks = [v for v in r["violations"] if v["severity"] == "block"]
        if blocks:
            dirty.append((f.name, [v["matched"] for v in blocks]))
    assert not dirty, f"知识库自身触碰红线：{dirty}"


# ---------- 解码引擎 ----------

def test_decode_soft_refusal():
    r = decode.decode("这个方案挺有意思的，我们再看看吧。", scene="work")
    assert r["ok"]
    ids = {h["entry_id"] for h in r["hits"]}
    assert "zai_kan_kan" in ids
    assert r["summary"]["level"] in ("warn", "alert")


def test_decode_gives_multiple_readings():
    """核心原则：永远不给唯一答案。"""
    r = decode.decode("这个方案挺有意思的，我们再看看吧。", scene="work")
    multi = [h for h in r["hits"] if len(h["readings"]) > 1]
    assert multi, "至少要有一条命中给出多个候选解读"
    for h in multi:
        confs = [x["confidence"] for x in h["readings"]]
        assert confs == sorted(confs, reverse=True), "解读应按置信度降序"


def test_decode_vague_terms():
    r = decode.decode("这个改动很简单的，你尽快弄一下。", scene="work")
    ids = {v["id"] for v in r["vague"]}
    assert "jinkuai" in ids and "xiao_gaidong" in ids


def test_decode_speaker_override():
    r = decode.decode("尽快给我", scene="work", speaker_overrides={"jinkuai": "3 个工作日"})
    v = next(v for v in r["vague"] if v["id"] == "jinkuai")
    assert v["is_personal"] and v["range"] == "3 个工作日"


def test_decode_three_layers():
    r = decode.decode("报表明天要交。我觉得时间有点紧。能不能帮我看一下？", scene="work")
    assert r["layers"]["facts"] and r["layers"]["emotions"] and r["layers"]["actions"]


def test_decode_empty():
    assert decode.decode("")["ok"] is False


# ---------- 转换引擎 ----------

def test_compose_flags_but_never_says_wrong():
    r = compose.analyze("这个方案根本行不通，第三步的逻辑是错的。", scene="work")
    assert r["ok"]
    ids = {f["flag_id"] for f in r["flags"]}
    assert "absolute_negation" in ids or "direct_error_claim" in ids
    blob = r["headline"] + r["note"] + "".join(f["nt_reading"] for f in r["flags"])
    check = guardrails.inspect(blob)
    assert not any(v["rule_id"] == "deficit_framing" for v in check["violations"]), \
        "标记文案不得使用缺陷化措辞"


def test_compose_every_flag_has_keep_note():
    """每条标记都必须说明「什么情况下保持原样更好」——保证不是单向施压。"""
    r = compose.analyze(
        "这个方案根本行不通，逻辑是错的。改一下发我。不好意思打扰了，可能是我的问题。",
        scene="work",
    )
    missing = [f["label"] for f in r["flags"] if not f["keep_note"]]
    assert not missing, f"以下标记缺少 keep_note：{missing}"


def test_compose_does_not_auto_rewrite():
    """默认只标注不改写：analyze 的返回里不得出现改写后的成品文本。"""
    text = "这个方案根本行不通。"
    r = compose.analyze(text, scene="work")
    assert r["original"] == text
    assert "composed" not in r


def test_compose_apply_is_opt_in():
    text = "改一下报表。"
    plain = compose.apply_edits(text)
    assert plain["composed"] == text, "不选任何配件时必须原样输出"

    buffed = compose.apply_edits(text, opener_id="hello", closing_id="thanks", slots={"name": "张三"})
    assert "张三你好" in buffed["composed"] and buffed["composed"].endswith("谢谢。")
    assert buffed["original"] == text, "原文必须保留"


def test_compose_reports_unfilled_slots():
    r = compose.apply_edits("改一下报表。", opener_id="hello")
    assert "name" in r["unfilled_slots"], "未填的槽位必须显式报告，不能悄悄留空"


def test_compose_over_apology():
    r = compose.analyze(
        "不好意思打扰了，可能是我理解有问题，我不太确定对不对，就是感觉超时设置有点短。",
        scene="work",
    )
    assert "excessive_hedging" in {f["flag_id"] for f in r["flags"]}


# ---------- 思路梳理 ----------

def test_clarify_entry_does_not_ask_how_you_feel():
    """述情障碍(DIF)研究要求：入口必须是躯体/情境锚点，不能问「你感觉如何」。"""
    d = clarify.entry_options()
    assert "感觉如何" not in d["prompt"] and "什么情绪" not in d["prompt"]
    assert d["somatic_groups"] and d["situations"]


def test_clarify_somatic_to_emotions():
    d = clarify.suggest_emotions(["chest_tight", "restless"], [])
    labels = [c["label"] for c in d["candidates"]]
    assert "焦虑" in labels
    assert d["candidates"][0]["weight"] >= d["candidates"][-1]["weight"]
    assert d["allow_skip"], "必须允许跳过情绪步骤"


def test_clarify_build_ends_in_actionable_need():
    d = clarify.build("more_time", slots={"time": "周四下午", "tradeoff": "周报"})
    assert d["ok"] and "周四下午" in d["sentence"]
    assert len(d["branches"]) == 3, "必须给顺利/僵持/拒绝三分支"
    assert {b["key"] for b in d["branches"]} == {"smooth", "stall", "refused"}


def test_clarify_emotion_not_included_by_default():
    d = clarify.build("more_time", slots={"time": "周四"}, emotions=["焦虑"])
    assert d["emotion_sentence"] == "", "情绪句默认不加入"
    d2 = clarify.build("more_time", slots={"time": "周四"}, emotions=["焦虑"], include_emotion=True)
    assert "焦虑" in d2["emotion_sentence"]


def test_clarify_unknown_need_is_legal():
    d = clarify.build("unknown", slots={"event": "刚才会议上的事"})
    assert d["ok"] and d["note"], "「我还不确定我要什么」必须是合法终点且有说明"


def test_clarify_all_outputs_clean():
    """所有预置话术都不能触碰红线。"""
    from engine.base import load
    kb = load("clarify")
    for need in kb["needs"]:
        for tpl in need["templates"]:
            r = guardrails.inspect(tpl)
            assert not r["blocked"], f"话术模板触碰红线：{tpl} → {r['violations']}"


if __name__ == "__main__":
    import traceback

    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            passed += 1
            print(f"  ✓ {name}")
        except Exception as exc:
            failed.append(name)
            print(f"  ✗ {name}: {exc}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
