"""asdTranslator 引擎层。

模块划分：
  base       — 公共数据结构与知识库加载
  decode     — NT → ASD 解码
  compose    — ASD → NT 转换（只标注，改写需显式触发）
  clarify    — 思路梳理（躯体 → 情绪 → 诉求）
  guardrails — 输出红线检查（LLM 输出必过）
  llm        — 可插拔 LLM 适配层
"""
