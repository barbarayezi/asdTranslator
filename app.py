"""asdTranslator — ASD ↔ NT 双向沟通翻译器。

理论基础：Milton (2012) 双重共情问题。沟通断裂是双向错配，不是单方缺陷，
所以两个翻译方向是同等一等公民，不存在"主功能"和"辅助功能"之分。

运行：python app.py  → http://127.0.0.1:5111
"""
from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, render_template, request

import config
import database as db
from engine import clarify, compose, decode, guardrails, llm
from engine.base import load, reload_all

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["JSON_AS_ASCII"] = False


@app.before_request
def _ensure_db() -> None:
    if not getattr(app, "_db_ready", False):
        db.init_db()
        app._db_ready = True  # type: ignore[attr-defined]


def body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


# ---------------- 页面 ----------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        scenes=config.SCENES,
        prefs=db.get_prefs(),
        llm_status=llm.status(),
    )


# ---------------- NT → ASD 解码 ----------------

@app.post("/api/decode")
def api_decode():
    data = body()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "请先粘贴对方说的话"}), 400

    scene = data.get("scene") or "work"
    prefs = db.get_prefs()
    speaker = db.get_speaker(data.get("speaker_id"))
    overrides = speaker["overrides"] if speaker else {}

    result = decode.decode(
        text,
        scene=scene,
        min_confidence=float(prefs.get("min_confidence", 0.2)),
        speaker_overrides=overrides,
    )
    result["speaker"] = speaker

    if data.get("use_llm") and llm.available():
        enhanced = llm.decode(text, scene, result)
        result["llm"] = enhanced
        if enhanced.get("ok"):
            result["engine"] = "rules+llm"

    db.add_entry("decode", text, scene=scene,
                 speaker_id=data.get("speaker_id"),
                 payload={"level": result.get("summary", {}).get("level")})
    return jsonify(result)


# ---------------- ASD → NT 转换 ----------------

@app.post("/api/compose/analyze")
def api_compose_analyze():
    data = body()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "请先写下你想说的话"}), 400

    scene = data.get("scene") or "work"
    result = compose.analyze(text, scene=scene)

    if data.get("use_llm") and llm.available():
        enhanced = llm.compose(text, scene, result)
        result["llm"] = enhanced
        if enhanced.get("ok"):
            result["engine"] = "rules+llm"

    db.add_entry("compose", text, scene=scene, payload={"level": result.get("level")})
    return jsonify(result)


@app.post("/api/compose/apply")
def api_compose_apply():
    data = body()
    result = compose.apply_edits(
        text=data.get("text") or "",
        opener_id=data.get("opener_id") or "plain",
        closing_id=data.get("closing_id") or "plain",
        buffer_ids=data.get("buffer_ids") or [],
        slots=data.get("slots") or {},
        reorder_index=data.get("reorder_index"),
    )
    return jsonify(result)


# ---------------- 思路梳理 ----------------

@app.get("/api/clarify/entry")
def api_clarify_entry():
    return jsonify(clarify.entry_options())


@app.post("/api/clarify/emotions")
def api_clarify_emotions():
    data = body()
    return jsonify(
        clarify.suggest_emotions(
            data.get("somatic") or [],
            data.get("situations") or [],
        )
    )


@app.get("/api/clarify/needs")
def api_clarify_needs():
    return jsonify(clarify.need_options())


@app.post("/api/clarify/build")
def api_clarify_build():
    data = body()
    result = clarify.build(
        need_id=data.get("need_id") or "",
        slots=data.get("slots") or {},
        emotions=data.get("emotions") or [],
        situations=data.get("situations") or [],
        template_index=int(data.get("template_index") or 0),
        include_emotion=bool(data.get("include_emotion")),
    )
    if result.get("ok"):
        db.add_entry("clarify", data.get("need_id") or "", result.get("full", ""),
                     payload={"emotions": data.get("emotions") or []})
    return jsonify(result)


@app.post("/api/clarify/freeform")
def api_clarify_freeform():
    """自由文本入口——说不清楚的时候直接倒出来，交给 LLM 梳理。"""
    data = body()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "写点什么都行，不用成句"}), 400
    if not llm.available():
        return jsonify({
            "ok": False,
            "error": "这个入口需要配置大模型。你也可以用左边的三步流程，不需要联网。",
        }), 503
    result = llm.clarify(text, data.get("context") or "")
    if result.get("ok"):
        db.add_entry("clarify_free", text, payload={})
    return jsonify(result)


# ---------------- 说话人档案 ----------------

@app.get("/api/speakers")
def api_speakers():
    return jsonify({"speakers": db.list_speakers(), "vague_terms": load("vague_terms")["entries"]})


@app.post("/api/speakers")
def api_speaker_save():
    data = body()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "需要一个名字"}), 400
    sp = db.upsert_speaker(
        name=name,
        relation=data.get("relation") or "",
        notes=data.get("notes") or "",
        overrides=data.get("overrides") or {},
    )
    return jsonify({"ok": True, "speaker": sp})


@app.delete("/api/speakers/<int:speaker_id>")
def api_speaker_delete(speaker_id: int):
    db.delete_speaker(speaker_id)
    return jsonify({"ok": True})


# ---------------- 设置 / 历史 / 红线 ----------------

@app.get("/api/prefs")
def api_prefs_get():
    return jsonify({"prefs": db.get_prefs(), "llm": llm.status(), "scenes": config.SCENES})


@app.post("/api/prefs")
def api_prefs_set():
    return jsonify({"ok": True, "prefs": db.set_prefs(body())})


@app.get("/api/history")
def api_history():
    return jsonify({"entries": db.list_entries(request.args.get("kind"), int(request.args.get("limit", 50)))})


@app.delete("/api/history/<int:entry_id>")
def api_history_delete(entry_id: int):
    db.delete_entry(entry_id)
    return jsonify({"ok": True})


@app.delete("/api/history")
def api_history_clear():
    db.clear_entries()
    return jsonify({"ok": True})


@app.get("/api/guardrails")
def api_guardrails():
    return jsonify({"rules": guardrails.rules_digest(), "self_test_failures": guardrails.self_test()})


@app.post("/api/guardrails/check")
def api_guardrails_check():
    return jsonify(guardrails.inspect(body().get("text") or ""))


@app.post("/api/reload")
def api_reload():
    """改完 data/*.json 不用重启。"""
    reload_all()
    return jsonify({"ok": True})


if __name__ == "__main__":
    db.init_db()
    failures = guardrails.self_test()
    if failures:
        print("⚠️  红线自检未通过：", failures)
    else:
        print("✅ 红线自检通过（%d 个对抗性用例）" % len(guardrails.ADVERSARIAL_CASES))
    print(f"→ http://{config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
