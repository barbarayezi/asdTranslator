"""asdTranslator 配置。

设计约定：所有用户数据默认留在本机。LLM 为可选增强，未配置时规则引擎独立可用。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("ASDT_DB", BASE_DIR / "asd_translator.db"))

# ---- Flask ----
SECRET_KEY = os.environ.get("ASDT_SECRET", "asd-translator-local-dev")
HOST = os.environ.get("ASDT_HOST", "127.0.0.1")
PORT = int(os.environ.get("ASDT_PORT", "5111"))
DEBUG = os.environ.get("ASDT_DEBUG", "1") == "1"

# ---- LLM（可选）----
# 兼容任何 OpenAI Chat Completions 风格的服务：OpenAI / DeepSeek / Moonshot / 本地 Ollama 等
LLM_ENABLED = os.environ.get("ASDT_LLM_ENABLED", "auto")  # auto | on | off
LLM_BASE_URL = os.environ.get("ASDT_LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.environ.get("ASDT_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("ASDT_LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT = int(os.environ.get("ASDT_LLM_TIMEOUT", "45"))
LLM_MAX_RETRY_ON_GUARDRAIL = 2

# ---- 产品原则开关（用户可在设置页覆盖）----
DEFAULT_PREFS = {
    # 述情障碍模块只有约一半 ASD 用户需要，因此可关闭
    "emotion_module_enabled": True,
    # 默认只标注不改写；改写必须显式触发
    "auto_rewrite": False,
    # 缓冲语默认全部关闭，由用户逐条打开
    "default_hedges_on": False,
    # 解读的最低置信度显示阈值
    "min_confidence": 0.2,
    # 默认场景
    "default_scene": "work",
    # 是否显示"为什么"的文献依据
    "show_rationale": True,
}

SCENES = {
    "work": "职场沟通",
    "social": "日常社交",
    "intimate": "亲密关系",
}
