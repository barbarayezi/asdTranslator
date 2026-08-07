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

# 用法：
#   ./run.sh          前台运行（占住当前终端，Ctrl+C 停止）
#   ./run.sh --bg     后台常驻，日志写到 /tmp/asdt.log，关终端也不死
if [ "$1" = "--bg" ]; then
  ASDT_DEBUG=0 nohup ./venv/bin/python app.py > /tmp/asdt.log 2>&1 &
  disown
  echo "已后台启动 → http://127.0.0.1:5111  （日志：tail -f /tmp/asdt.log）"
  echo "停止：lsof -ti:5111 | xargs kill"
  exit 0
fi

exec ./venv/bin/python app.py
