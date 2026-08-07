# CLAUDE.md — asdTranslator 开发约定

给在这个仓库里工作的 AI 助手看的。先读这个，再动代码。

---

## 一句话

ASD ↔ NT 双向沟通翻译器。Flask + SQLite + 原生前端，规则引擎优先、LLM 可选插拔。

## 最重要的一条

**这个项目的产品红线比代码质量优先级更高。**

它服务的用户群体里，masking（伪装）与抑郁、耗竭、自杀意念存在实证相关
（Cage & Troxell-Whitman 2019；Cassidy et al. 2018）。一个「帮你显得更正常」的
功能不是体验瑕疵，是伤害。改动之前先确认没有踩下面任何一条。

---

## 不可协商的约束

违反任何一条都算 bug，不管需求是谁提的。

### 1. 绝不自动改写用户的话

`compose.analyze()` 只返回标记，不返回改写后的文本。
`compose.apply_edits()` 只在用户显式选择了要应用哪些改动时才执行。

顶层响应里**不应该出现** `rewritten` 字段。测试 `test_compose_does_not_auto_rewrite`
和 `test_compose_apply_is_opt_in` 守这条。

### 2. 每个标记必须带 keep_note

`keep_note` 说明「在什么场合保留原话是对的」。没有 keep_note 的标记等于在说
「你这么说是错的」。测试 `test_compose_every_flag_has_keep_note` 守这条。

同理，每个改写建议必须带 `cost` 字段。软化措辞会损失精确性，不说出来就是隐瞒。

### 3. 不做能力评分

不存、不算、不显示任何形式的社交表现分数、进度条、等级。
`database.py` 在数据层就拒绝这类字段。

量化社交表现会强化自我监控——那正是 masking 的心理机制。

### 4. 情绪入口必须是躯体锚点

述情障碍在 ASD 群体患病率 49.93%（Kinnaird et al. 2019）。
直接问「你现在感觉如何」对近一半用户是无效入口。

`clarify.entry_options()` 的第一步永远是身体感觉，不是情绪词。
「说不上来」和「我还不确定我要什么」都是合法终点，不是未完成状态。
测试 `test_clarify_entry_does_not_ask_how_you_feel` 和
`test_clarify_unknown_need_is_legal` 守这条。

### 5. 解码永远给多个候选

`decode()` 的每个 hit 至少 2 个 readings，各自带 confidence 和 when（适用条件）。

给唯一解读就是在假装潜台词有标准答案。它没有——同一句话在不同关系里可能完全相反。
测试 `test_decode_gives_multiple_readings` 守这条。

### 6. 红线层独立于 LLM

`engine/guardrails.py` 不依赖 `engine/llm.py`。任何输出——规则引擎的、模型的——
都要过 `inspect()`。

Jang et al. (CHI 2024) §5.2 的结论：prompt engineering 消不掉这三类系统性伤害
（诱导 masking、建议披露诊断、过度乐观），必须有独立检测层。
不要试图用「更好的 system prompt」替代这一层。

---

## 改红线规则时

红线用两种匹配：`patterns`（字面串）和 `regex_patterns`（正则）。

**新增 block 级规则时必须同时加正则。** 字面匹配对安全层太脆弱——
「告诉对方你有阿斯伯格」被拦而「告诉同事你有阿斯伯格」放行，这条规则就没用了。

改完必须做三件事：

1. 往 `ADVERSARIAL_CASES` 加**至少 2 条不同措辞**的用例（换主语、换动词、换句式）
2. 检查 `BENIGN_CASES` 还全绿——红线过紧比过松更隐蔽，工具会在用户最需要时罢工
3. 跑 `test_knowledge_base_is_clean`——知识库自己的文案也要过红线检查

历史教训：曾经把「语调」当作裸串放进 `body_language_advice`，结果知识库里所有
描述性提到「语调」的文案全被自己的红线判违规。现在改成了带前缀限定
（`注意语调` / `语调要` / `调整语调`）。**描述一个现象 ≠ 建议用户那样做。**

---

## 改知识库时

`data/*.json`，改完不用重启：

```bash
curl -X POST http://127.0.0.1:5111/api/reload
```

### nt_phrases.json 的两种匹配模式

```jsonc
{
  "patterns":       ["呵呵"],                    // 子串匹配，出现在任何位置都算
  "exact_patterns": ["哦", "嗯", "。", "666"]     // 仅整条消息完全等于时才算
}
```

`exact_patterns` 是必需的。像「哦」「。」这类，只有当它是**整条回复**时才携带
冷淡/敷衍的含义。作为子串会在几乎每句话里误报。

历史教训：这几个串一开始放在 `patterns` 里，导致输入的每一个句号都被标成「反语」，
一句话报出三个假阳性。修复方式是拆成两个字段，不是加长度判断。

### 每条词条的完整形状

```jsonc
{
  "id": "...",
  "patterns": [...],
  "exact_patterns": [...],        // 可选
  "category": "soft_refusal",     // 必须是 categories 里已定义的
  "literal": "字面意思",
  "readings": [                   // 至少 2 条
    {"text": "...", "confidence": 0.65, "when": "什么情况下适用这条解读"}
  ],
  "probe": "怎么低成本地验证",     // 可选但强烈建议
  "scenes": ["work", "social", "intimate"]
}
```

`when` 字段不能省。「65% 概率是软性拒绝」没有可操作性，
「如果对方没追问细节也没约下次时间，就更可能是软性拒绝」才有。

场景 id 只有三个：`work` / `social` / `intimate`（见 `config.SCENES`）。
写别的会被静默过滤掉，不报错——这是个容易踩的坑。

---

## 跑起来 / 测试

```bash
./run.sh                                        # 建 venv + 启动，端口 5111
./venv/bin/python tests/test_guardrails.py      # 27 项，必须全绿
```

本地有代理会拦 localhost，curl 记得加 `--noproxy '*'`。

服务用 `nohup ... &` 启动会随 shell 会话退出被杀，要用真正的后台方式。

---

## 代码约定

- 中文注释，解释**为什么**这么设计，不解释代码在做什么
- 涉及产品红线的地方，注释里引用具体文献（作者 + 年份 + 章节）
- 类型标注用 `from __future__ import annotations` + 新式语法（`list[str]`）
- 数据结构用 `@dataclass`，不用裸 dict 传递
- 知识库的读取一律走 `engine.base.load()`，它有缓存
- 前端不引任何框架，不引 CDN

## UI 约束

- 不用红色报错样式——用户的表达不是 bug
- 语义色只表示注意力等级，不表示对错
- 原文永远可见，改写并列展示而不是替换
- 无动画闪烁（感官过载考量）
- 低饱和配色

---

## 常见误判

**「加个一键优化按钮吧」** — 不行。违反约束 1。一键改写就是在说原话不对。

**「置信度显示成星级更直观」** — 不行。那是评分的变体，违反约束 3。

**「让 LLM 自己遵守红线，省掉检测层」** — 不行。违反约束 6，且有实证反驳。

**「『你说话太直接了』这个提示很有用」** — 缺陷化措辞，红线 `deficit_framing` 会拦。
换成「这句话在 NT 语境里可能被读成 X」。
