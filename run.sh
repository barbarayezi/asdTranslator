#!/usr/bin/env bash
# 启动 asdTranslator。首次运行会自动建 venv 并装依赖。
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "首次运行，创建虚拟环境…"
  python3 -m venv venv
  ./venv/bin/pip install -q -r requirements.txt
fi

# 可选：接入大模型（不配置则纯规则引擎运行，功能不残废）
# export ASDT_LLM_API_KEY="sk-..."
# export ASDT_LLM_BASE_URL="https://api.deepseek.com/v1"
# export ASDT_LLM_MODEL="deepseek-chat"

exec ./venv/bin/python app.py
