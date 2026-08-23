/* asdTranslator 前端
 *
 * UI 层的产品红线（与后端 guardrails 对应）：
 *  - 不渲染任何错误图标、红色波浪线、评分或进步曲线
 *  - 原文永远可见，不被结果覆盖
 *  - 缓冲件默认全部关闭，由用户逐条打开
 *  - 每个改写建议必须同时显示 gain 与 cost
 */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = {
  prefs: window.__PREFS__ || {},
  speakers: [],
  vagueTerms: [],
  compose: null,
  composer: { opener: 'plain', closing: 'plain', buffers: [], slots: {}, reorder: null },
  clarify: { step: 1, somatic: [], situations: [], emotions: [], need: null, slots: {}, tplIndex: 0, includeEmotion: false, data: null, _prefillApplied: false },
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || (opts.body ? 'POST' : 'GET'),
    headers: opts.body ? { 'Content-Type': 'application/json' } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({ ok: false, error: '服务返回了非 JSON 内容' }));
  return data;
}

function toast(msg, ms = 2200) {
  const el = $('#toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, ms);
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => toast('已复制'), () => toast('复制失败，请手动选择'));
}

/* ==================== 标签页 ==================== */
$$('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    $$('.tab').forEach((t) => t.classList.remove('active'));
    $$('.panel').forEach((p) => p.classList.remove('active'));
    tab.classList.add('active');
    $('#panel-' + tab.dataset.tab).classList.add('active');
    if (tab.dataset.tab === 'history') loadHistory();
    if (tab.dataset.tab === 'clarify' && !$('#clarifyBody').innerHTML.trim()) renderClarify();
    if (tab.dataset.tab === 'sleep') loadSleepApp();
  });
});

/* ==================== 睡眠/身体状态（嵌入同机 sleeptracking） ==================== */
let sleepLoaded = false;
async function loadSleepApp() {
  const frame = $('#sleepFrame');
  const fb = $('#sleepFallback');
  if (!frame || !fb) return;
  let d = {};
  try {
    d = await api('/api/sleep-app');
  } catch (e) {
    d = { url: null, reachable: false };
  }
  if (d.url && d.reachable) {
    fb.hidden = true;
    if (!sleepLoaded) { frame.src = d.url; sleepLoaded = true; }
  } else {
    fb.hidden = false;
    $('#sleepUrlHint').textContent = d.url
      ? `预期地址：${d.url}（但连不上）`
      : '没找到 sleeptracking 的 .active_port，确认项目路径是否正确。';
  }
}

$$('.chip-sample').forEach((btn) => {
  btn.addEventListener('click', () => { $('#' + btn.dataset.target).value = btn.dataset.text; });
});

/* ==================== 听懂对方 ==================== */
$('#decodeBtn').addEventListener('click', async () => {
  const text = $('#decodeInput').value.trim();
  if (!text) { toast('先粘贴一段对方说的话'); return; }
  const box = $('#decodeResult');
  box.innerHTML = '<div class="loading">解码中…</div>';
  const providers = [];
  if ($('#ctxSleep').checked) providers.push('sleep');

  const data = await api('/api/decode', {
    body: {
      text,
      scene: $('#scene').value,
      speaker_id: $('#speaker').value ? Number($('#speaker').value) : null,
      use_llm: $('#decodeLlm').checked,
      context_providers: providers,
      context: {
        relationship: $('#ctxRelationship').value.trim(),
        setting: $('#ctxSetting').value.trim(),
        prior: $('#ctxPrior').value.trim(),
        tone: $('#ctxTone').value.trim(),
      },
    },
  });
  renderDecode(data);
});

function renderDecode(d) {
  const box = $('#decodeResult');
  if (!d.ok) { box.innerHTML = `<div class="err-box">${esc(d.error || '解码失败')}</div>`; return; }

  const parts = [];
  const s = d.summary;
  const compLabel = { thin: '信息太少', partial: '背景不全', adequate: '背景较全' }[s.completeness] || '';
  parts.push(`
    <div class="summary-card level-${esc(s.level)}">
      ${compLabel ? `<span class="comp-badge comp-${esc(s.completeness)}">${esc(compLabel)}</span>` : ''}
      <div class="summary-headline">${esc(s.headline)}</div>
      ${s.priority.map((p) => `<p class="summary-priority">· ${esc(p)}</p>`).join('')}
    </div>`);

  // 信息不够：明确告诉用户还缺什么，而不是假装分析完整
  if (s.missing_context && s.missing_context.length) {
    parts.push(`
      <div class="missing-box">
        <p class="missing-title">补上这些背景，解读会更准：</p>
        <ul>${s.missing_context.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>
      </div>`);
  }

  // 身体/睡眠状态：仅作用户的自我觉察参考，明确不改对方原意
  if (s.body_context) {
    const labels = { body_state: '身体信号', recent_mood: '近期心情', sleep_quality: '睡眠质量' };
    const items = Object.entries(s.body_context)
      .map(([k, v]) => `<li><b>${esc(labels[k] || k)}</b>：${esc(v)}</li>`)
      .join('');
    parts.push(`
      <div class="body-box">
        <p class="body-title">结合你近期的身体状态（仅作你的参考）</p>
        <ul>${items}</ul>
        <p class="body-note">${esc(s.body_note || '')}</p>
      </div>`);
  }

  // 三层拆解
  const L = d.layers;
  const layer = (title, items, hintWhenEmpty) => `
    <div class="layer">
      <h4>${title}</h4>
      ${items.length
        ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>`
        : `<p class="none">${hintWhenEmpty}</p>`}
    </div>`;
  parts.push(`
    <div>
      <p class="section-title">这段话拆成三层</p>
      <div class="layers" style="margin-top:8px">
        ${layer('陈述的事实', L.facts, '没有识别到明确的事实陈述')}
        ${layer('表达的情绪', L.emotions, '没有明显的情绪词')}
        ${layer('想让你做的事', L.actions, '没有识别到明确的行动要求')}
      </div>
    </div>`);

  // 命中的潜台词
  if (d.hits.length) {
    parts.push('<p class="section-title">可能不是字面意思的地方</p>');
    d.hits.forEach((h) => {
      const readings = h.readings.map((r) => `
        <div class="reading">
          <div class="conf">
            ${Math.round(r.confidence * 100)}%
            <div class="conf-bar"><div class="conf-fill" style="width:${r.confidence * 100}%"></div></div>
          </div>
          <div>
            <div class="reading-text">${esc(r.text)}</div>
            ${r.when ? `<div class="reading-when">成立条件：${esc(r.when)}</div>` : ''}
          </div>
        </div>`).join('');
      parts.push(`
        <div class="hit">
          <div class="hit-head">
            <span class="hit-phrase">「${esc(h.matched)}」</span>
            <span class="tone-pill tone-${esc(h.tone)}">${esc(h.category_label)}</span>
          </div>
          <div class="hit-body">
            ${h.literal ? `<div class="literal">字面意思：${esc(h.literal)}</div>` : ''}
            ${readings}
            ${h.probe ? `<div class="probe"><b>你可以这样做 · </b>${esc(h.probe)}</div>` : ''}
            ${state.prefs.show_rationale && h.why ? `<div class="why">${esc(h.why)}</div>` : ''}
          </div>
        </div>`);
    });
  }

  // 模糊量词
  if (d.vague.length) {
    parts.push('<p class="section-title">模糊的时间和数量</p>');
    parts.push(`<div class="card">${d.vague.map((v) => `
      <div class="vague-item">
        <div class="vague-term">「${esc(v.matched)}」</div>
        <div style="flex:1">
          <div class="vague-range">通常是 <b>${esc(v.range)}</b>
            ${v.is_personal ? '<span class="personal-tag">这个人的历史区间</span>' : ''}
          </div>
          <div class="vague-spread">${esc(v.spread)}</div>
          <div class="vague-clarify">想确认的话：${esc(v.clarify)}</div>
        </div>
      </div>`).join('')}</div>`);
  }

  // LLM 增强
  if (d.llm) parts.push(renderLlmDecode(d.llm));

  parts.push(`<div class="note-box">${esc(d.disclaimer)}</div>`);
  box.innerHTML = parts.join('');
}

function renderLlmDecode(llm) {
  if (!llm.ok) {
    return `<div class="note-box">大模型这一路没跑通：${esc(llm.error || '未知原因')}。上面的规则引擎结果不受影响。</div>`;
  }
  const x = llm.data || {};
  const readings = (x.readings || []).map((r) => `
    <div class="reading">
      <div class="conf">${Math.round((r.confidence || 0) * 100)}%
        <div class="conf-bar"><div class="conf-fill" style="width:${(r.confidence || 0) * 100}%"></div></div>
      </div>
      <div><div class="reading-text">${esc(r.text)}</div>
      ${r.when ? `<div class="reading-when">成立条件：${esc(r.when)}</div>` : ''}</div>
    </div>`).join('');
  const replies = (x.reply_options || []).map((o) => `
    <div class="rewrite">
      <span class="rewrite-level">${esc(o.label)}</span>
      <div style="flex:1">
        <div>${esc(o.text)}</div>
        <div class="output-actions"><button class="btn-ghost" data-copy="${esc(o.text)}">复制</button></div>
      </div>
    </div>`).join('');
  const gw = llm.guardrail && llm.guardrail.violations.length
    ? `<div class="why">红线检查：命中 ${llm.guardrail.violations.length} 处 warn 级提示（已放行，仅供参考）。</div>` : '';
  return `
    <p class="section-title">大模型补充解读</p>
    <div class="hit"><div class="hit-body">
      ${readings}
      ${x.hidden_request ? `<div class="probe"><b>真正想让你做的事 · </b>${esc(x.hidden_request)}</div>` : ''}
      ${x.emotional_subtext ? `<div class="literal" style="margin-top:8px">没明说的情绪：${esc(x.emotional_subtext)}</div>` : ''}
      ${x.suggested_probe ? `<div class="probe"><b>可以这样问 · </b>${esc(x.suggested_probe)}</div>` : ''}
      ${replies ? `<p class="section-title" style="margin-top:12px">可直接发送的回复</p>${replies}` : ''}
      ${gw}
    </div></div>`;
}

/* ==================== 把话说出去 ==================== */
$('#composeBtn').addEventListener('click', async () => {
  const text = $('#composeInput').value.trim();
  if (!text) { toast('先写下你想说的话'); return; }
  const box = $('#composeResult');
  box.innerHTML = '<div class="loading">分析中…</div>';
  const data = await api('/api/compose/analyze', {
    body: { text, scene: $('#scene').value, use_llm: $('#composeLlm').checked },
  });
  state.compose = data;
  state.composer = { opener: 'plain', closing: 'plain', buffers: [], slots: {}, reorder: null };
  renderCompose(data);
  renderComposer(data);
});

function renderCompose(d) {
  const box = $('#composeResult');
  if (!d.ok) { box.innerHTML = `<div class="err-box">${esc(d.error || '分析失败')}</div>`; return; }

  const parts = [];
  parts.push(`
    <div class="summary-card level-${d.level === 'high' ? 'alert' : d.level === 'medium' ? 'warn' : d.level === 'none' ? 'clear' : 'info'}">
      <div class="summary-headline">${esc(d.headline)}</div>
    </div>`);

  parts.push(`<div class="original-box"><h4>你的原文（不会被修改）</h4>${esc(d.original)}</div>`);

  if (d.flags.length) {
    parts.push('<p class="section-title">在 NT 语境里可能被读作什么</p>');
    d.flags.forEach((f) => {
      const rw = (f.rewrites || []).map((r) => `
        <div class="rewrite">
          <span class="rewrite-level">选项 ${r.level}</span>
          <div style="flex:1">
            <div>${esc(r.template)}</div>
            <div class="rewrite-cost">代价：${esc(r.cost)}</div>
          </div>
        </div>`).join('');
      parts.push(`
        <div class="flag">
          <div class="flag-head">
            <span class="flag-label">${esc(f.label)}</span>
            ${f.matched ? `<span class="flag-matched">${esc(f.matched)}</span>` : ''}
          </div>
          <div class="flag-reading">${esc(f.nt_reading)}</div>
          ${f.keep_note ? `<div class="keep-note">保持原样也可以：${esc(f.keep_note)}</div>` : ''}
          ${rw}
        </div>`);
    });
  }

  if (d.layering) {
    const l = d.layering;
    if (l.kind === 'reorder') {
      parts.push(`
        <p class="section-title">信息密度分层</p>
        <div class="card">
          <p style="font-size:13px">${esc(l.text)}</p>
          <div class="original-box"><h4>调整后预览</h4>${esc(l.preview)}</div>
          <div class="output-actions">
            <button class="btn-ghost" data-copy="${esc(l.preview)}">复制这一版</button>
          </div>
        </div>`);
    } else {
      parts.push(`
        <p class="section-title">信息密度分层</p>
        <div class="card">
          <p style="font-size:13px">${esc(l.text)}</p>
          <div id="sentPicker">${l.sentences.map((s, i) =>
            `<button class="btn-ghost" style="margin:3px 4px 0 0" data-sent="${i}">${i + 1}. ${esc(s.slice(0, 20))}${s.length > 20 ? '…' : ''}</button>`).join('')}</div>
        </div>`);
    }
  }

  if (d.llm) parts.push(renderLlmCompose(d.llm));
  parts.push(`<div class="note-box">${esc(d.note)}</div>`);
  box.innerHTML = parts.join('');

  $$('#sentPicker [data-sent]').forEach((b) => {
    b.addEventListener('click', () => {
      state.composer.reorder = Number(b.dataset.sent);
      $$('#sentPicker [data-sent]').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      applyComposer();
      toast('已把第 ' + (Number(b.dataset.sent) + 1) + ' 句提到开头');
    });
  });
}

function renderLlmCompose(llm) {
  if (!llm.ok) {
    return `<div class="note-box">大模型这一路没跑通：${esc(llm.error || '未知原因')}。上面的规则引擎结果不受影响。</div>`;
  }
  const x = llm.data || {};
  const variants = (x.variants || []).map((v) => `
    <div class="flag" style="margin-bottom:8px">
      <div class="flag-head"><span class="flag-label">${esc(v.label)}</span></div>
      <div class="output-text">${esc(v.text)}</div>
      <div class="opt-tradeoff" style="margin-top:8px">
        <span class="gain">换来：${esc(v.gain)}</span> ｜ <span class="cost">损失：${esc(v.cost)}</span>
      </div>
      <div class="output-actions"><button class="btn-ghost" data-copy="${esc(v.text)}">复制</button></div>
    </div>`).join('');
  const branches = (x.branches || []).map((b) => `
    <div class="branch branch-${esc(b.key)}"><h5>${esc(b.label)}</h5><p class="action">${esc(b.action)}</p></div>`).join('');
  return `
    <p class="section-title">大模型改写变体</p>
    ${variants}
    ${branches ? `<p class="section-title">对方可能怎么反应</p>${branches}` : ''}`;
}

function renderComposer(d) {
  if (!d.ok) return;
  $('#composerCard').hidden = false;
  const body = $('#composerBody');
  const on = state.prefs.default_hedges_on;

  const optRow = (type, o, checked) => {
    const slots = (o.text.match(/\{(\w+)\}/g) || []).map((m) => m.slice(1, -1));
    return `
      <label class="opt-row ${checked ? 'checked' : ''}" data-type="${type}" data-id="${esc(o.id)}">
        <input type="${type === 'buffer' ? 'checkbox' : 'radio'}" name="${type}" value="${esc(o.id)}" ${checked ? 'checked' : ''}>
        <div style="flex:1">
          <div class="opt-label">${esc(o.label)}</div>
          ${o.text ? `<div class="opt-text">「${esc(o.text)}」</div>` : ''}
          <div class="opt-tradeoff">
            <span class="gain">换来：${esc(o.gain)}</span> ｜ <span class="cost">代价：${esc(o.cost)}</span>
          </div>
          ${slots.map((s) => `<input class="slot-input" data-slot="${esc(s)}" placeholder="填写 ${esc(s)}">`).join('')}
        </div>
      </label>`;
  };

  const html = [];
  html.push(`<div class="composer-group"><h4>开场</h4>${d.openers.map((o) => optRow('opener', o, o.id === 'plain')).join('')}</div>`);
  if (d.buffers.length) {
    html.push(`<div class="composer-group"><h4>缓冲件（针对上面标记的点，默认全关）</h4>${d.buffers.map((b) => optRow('buffer', b, on)).join('')}</div>`);
  }
  html.push(`<div class="composer-group"><h4>收尾</h4>${d.closings.map((o) => optRow('closing', o, o.id === 'plain')).join('')}</div>`);
  html.push(`
    <div class="composer-group"><h4>如果这是一次拒绝，选强度</h4>
      ${d.refusal_levels.map((r) => `
        <div class="opt-row" style="cursor:default">
          <div style="flex:1">
            <div class="opt-label">${esc(r.label)}</div>
            <div class="opt-text">「${esc(r.template)}」</div>
            <div class="opt-tradeoff">${esc(r.note)}</div>
            <div class="output-actions"><button class="btn-ghost" data-copy="${esc(r.template)}">复制模板</button></div>
          </div>
        </div>`).join('')}
    </div>`);
  html.push('<div id="composerOut"></div>');
  body.innerHTML = html.join('');

  if (on) state.composer.buffers = d.buffers.map((b) => b.id);

  body.addEventListener('change', onComposerChange);
  body.addEventListener('input', (e) => {
    if (e.target.classList.contains('slot-input')) {
      state.composer.slots[e.target.dataset.slot] = e.target.value;
      applyComposer();
    }
  });
  applyComposer();
}

function onComposerChange(e) {
  const row = e.target.closest('.opt-row');
  if (!row) return;
  const { type, id } = row.dataset;
  if (type === 'opener' || type === 'closing') {
    $$(`.opt-row[data-type="${type}"]`).forEach((r) => r.classList.remove('checked'));
    row.classList.add('checked');
    state.composer[type] = id;
  } else if (type === 'buffer') {
    row.classList.toggle('checked', e.target.checked);
    const set = new Set(state.composer.buffers);
    e.target.checked ? set.add(id) : set.delete(id);
    state.composer.buffers = Array.from(set);
  }
  applyComposer();
}

async function applyComposer() {
  if (!state.compose) return;
  const c = state.composer;
  const data = await api('/api/compose/apply', {
    body: {
      text: state.compose.original,
      opener_id: c.opener,
      closing_id: c.closing,
      buffer_ids: c.buffers,
      slots: c.slots,
      reorder_index: c.reorder,
    },
  });
  const out = $('#composerOut');
  if (!out) return;
  out.innerHTML = `
    <div class="output-box">
      <h4>组装结果</h4>
      <div class="output-text">${esc(data.composed)}</div>
      ${data.unfilled_slots.length
        ? `<div class="unfilled">还有 ${data.unfilled_slots.length} 个空要填：${data.unfilled_slots.map(esc).join('、')}——填完再发。</div>`
        : ''}
      <div class="output-actions">
        <button class="btn-ghost" data-copy="${esc(data.composed)}">复制</button>
        <button class="btn-ghost" data-copy="${esc(state.compose.original)}">复制原文（不加任何东西）</button>
      </div>
    </div>`;
}

/* ==================== 梳理思路 ==================== */
async function renderClarify() {
  const c = state.clarify;
  const body = $('#clarifyBody');
  $$('.step-dot').forEach((d) => {
    const n = Number(d.dataset.step);
    d.classList.toggle('active', n === c.step);
    d.classList.toggle('done', n < c.step);
  });

  if (c.step === 1) {
    const providers = $('#ctxSleep').checked ? 'sleep' : '';
    const d = await api('/api/clarify/entry' + (providers ? '?providers=' + encodeURIComponent(providers) : ''));

    // 躯体锚点预填：开启睡眠数据源时，把更可能贴切的锚点预勾选（只做一次，不覆盖手动选择）
    const bh = d.body_hints || {};
    const prefill = new Set((bh.available && bh.suggested_somatic_ids) || []);
    if (prefill.size && !c._prefillApplied) {
      if (!c.somatic.length) c.somatic = [...prefill];
      c._prefillApplied = true;
    }

    let bodyBox = '';
    if (bh.available && prefill.size) {
      const signals = Object.entries(bh.raw || {})
        .map(([k, v]) => `<li><b>${esc(k)}</b>：${esc(v)}</li>`).join('');
      bodyBox = `
        <div class="body-box">
          <div class="body-box-title">${esc(bh.explanation || '根据你的身体数据，下面这些感觉可能更贴近你现在的状态')}</div>
          ${signals ? `<ul class="body-signals">${signals}</ul>` : ''}
          <p class="body-box-note">${esc(bh.note || '')}</p>
        </div>`;
    } else if (bh.available && bh.reason) {
      bodyBox = `<div class="body-box muted"><p class="body-box-note">${esc(bh.reason)}</p></div>`;
    }

    body.innerHTML = `
      <div class="card">
        <p class="clarify-prompt">${esc(d.prompt)}</p>
        ${bodyBox}
        <p class="clarify-hint">${esc(d.skip_hint)}</p>
        ${Object.entries(d.somatic_groups).map(([g, items]) => `
          <div class="chip-group"><h4>${esc(g)}</h4>
            <div class="chips">${items.map((i) =>
              `<button class="chip${prefill.has(i.id) ? ' prefill' : ''}" data-kind="somatic" data-id="${esc(i.id)}" title="${esc(i.note || '')}">${esc(i.label)}</button>`).join('')}</div>
          </div>`).join('')}
        <div class="chip-group"><h4>${esc(d.situation_prompt)}</h4>
          <div class="chips">${d.situations.map((s) =>
            `<button class="chip" data-kind="situations" data-id="${esc(s.id)}">${esc(s.label)}</button>`).join('')}</div>
        </div>
        <div class="step-nav">
          <span></span>
          <div>
            <button class="btn-ghost" id="clarifySkip">跳过，我先说事情</button>
            <button class="btn-primary" id="clarifyNext">下一步</button>
          </div>
        </div>
      </div>`;
    bindChips();
    $('#clarifyNext').addEventListener('click', () => {
      c.step = state.prefs.emotion_module_enabled ? 2 : 3;
      renderClarify();
    });
    $('#clarifySkip').addEventListener('click', () => { c.step = 3; renderClarify(); });
  }

  else if (c.step === 2) {
    const d = await api('/api/clarify/emotions', { body: { somatic: c.somatic, situations: c.situations } });
    body.innerHTML = `
      <div class="card">
        <p class="clarify-prompt">${esc(d.prompt)}</p>
        ${d.notes.map((n) => `<div class="keep-note">${esc(n)}</div>`).join('')}
        <div class="chip-group"><h4>比较可能的（按你选的身体感觉推的）</h4>
          <div class="chips">${d.candidates.length
            ? d.candidates.map((e) => `<button class="chip weighted" data-kind="emotions" data-id="${esc(e.label)}">${esc(e.label)}</button>`).join('')
            : '<span class="hint">上一步没选身体感觉，直接从下面完整列表里挑吧。</span>'}</div>
        </div>
        <div class="chip-group"><h4>完整列表</h4>
          <div class="chips">${d.all_emotions.map((e) =>
            `<button class="chip" data-kind="emotions" data-id="${esc(e)}">${esc(e)}</button>`).join('')}</div>
        </div>
        <div class="keep-note">${esc(d.skip_note)}</div>
        <div class="step-nav">
          <button class="btn-ghost" id="clarifyBack">上一步</button>
          <div>
            <button class="btn-ghost" id="clarifySkip2">${esc(d.skip_label)}</button>
            <button class="btn-primary" id="clarifyNext">下一步</button>
          </div>
        </div>
      </div>`;
    bindChips();
    $('#clarifyBack').addEventListener('click', () => { c.step = 1; renderClarify(); });
    $('#clarifySkip2').addEventListener('click', () => { c.emotions = []; c.step = 3; renderClarify(); });
    $('#clarifyNext').addEventListener('click', () => { c.step = 3; renderClarify(); });
  }

  else if (c.step === 3) {
    const d = await api('/api/clarify/needs');
    body.innerHTML = `
      <div class="card">
        <p class="clarify-prompt">${esc(d.prompt)}</p>
        <p class="clarify-hint">${esc(d.hint)}</p>
        <div class="need-list">${d.needs.map((n) =>
          `<button class="need-item" data-need="${esc(n.id)}">${esc(n.label)}${n.note ? `<small>${esc(n.note)}</small>` : ''}</button>`).join('')}</div>
        <div class="step-nav">
          <button class="btn-ghost" id="clarifyBack">上一步</button>
          <span></span>
        </div>
      </div>`;
    $$('.need-item').forEach((b) => b.addEventListener('click', () => {
      c.need = b.dataset.need; c.tplIndex = 0; c.slots = {}; c.step = 4; renderClarify();
    }));
    $('#clarifyBack').addEventListener('click', () => {
      c.step = state.prefs.emotion_module_enabled ? 2 : 1; renderClarify();
    });
  }

  else if (c.step === 4) {
    const d = await api('/api/clarify/build', {
      body: {
        need_id: c.need, slots: c.slots, emotions: c.emotions,
        situations: c.situations, template_index: c.tplIndex, include_emotion: c.includeEmotion,
      },
    });
    c.data = d;
    if (!d.ok) { body.innerHTML = `<div class="err-box">${esc(d.error)}</div>`; return; }

    body.innerHTML = `
      <div class="card">
        <div class="card-head"><h2>${esc(d.need)}</h2>
          <span class="hint">${c.emotions.length ? '已记录情绪：' + c.emotions.join('、') : '未记录情绪'}</span></div>
        <div class="output-box">
          <h4>可以直接说出口的话</h4>
          <div class="output-text" id="clarifyOut">${esc(d.full || d.sentence)}</div>
          <div class="output-actions">
            <button class="btn-ghost" data-copy="${esc(d.full || d.sentence)}">复制</button>
          </div>
          ${d.unfilled_slots.length ? `<div class="unfilled">需要补全：${d.unfilled_slots.map(esc).join('、')}</div>` : ''}
        </div>
        ${d.unfilled_slots.length ? `<div class="composer-group" style="margin-top:14px"><h4>填空</h4>
          ${d.unfilled_slots.map((s) => `<input class="slot-input" data-cslot="${esc(s)}" placeholder="${esc(s)}" value="${esc(c.slots[s] || '')}">`).join('')}
        </div>` : ''}
        ${c.emotions.length ? `<label class="checkbox" style="margin-top:12px">
          <input type="checkbox" id="incEmotion" ${c.includeEmotion ? 'checked' : ''}>
          <span>把情绪也说出来（默认不加——诉求本身就是完整信息）</span></label>` : ''}
        ${d.note ? `<div class="keep-note" style="margin-top:12px">${esc(d.note)}</div>` : ''}
        ${d.alternatives.length ? `<div class="composer-group" style="margin-top:14px"><h4>换一种说法</h4>
          ${d.alternatives.map((a) => `<div class="opt-row" style="cursor:pointer" data-alt="${a.index}">
            <div class="opt-text" style="flex:1">${esc(a.text)}</div></div>`).join('')}
        </div>` : ''}
      </div>
      <div class="card">
        <div class="card-head"><h2>对方可能怎么反应</h2>
          <span class="hint">三个分支都要有预案，不只准备顺利的那个</span></div>
        ${d.branches.map((b) => `
          <div class="branch branch-${esc(b.key)}">
            <h5>${esc(b.label)}</h5>
            <p>${esc(b.prompt)}</p>
            <p class="action">→ ${esc(b.action)}</p>
          </div>`).join('')}
      </div>
      <div class="step-nav">
        <button class="btn-ghost" id="clarifyBack">上一步</button>
        <button class="btn-ghost" id="clarifyRestart">重新来一遍</button>
      </div>`;

    $$('[data-cslot]').forEach((i) => i.addEventListener('change', () => {
      c.slots[i.dataset.cslot] = i.value; renderClarify();
    }));
    $$('[data-alt]').forEach((r) => r.addEventListener('click', () => {
      c.tplIndex = Number(r.dataset.alt); renderClarify();
    }));
    const inc = $('#incEmotion');
    if (inc) inc.addEventListener('change', () => { c.includeEmotion = inc.checked; renderClarify(); });
    $('#clarifyBack').addEventListener('click', () => { c.step = 3; renderClarify(); });
    $('#clarifyRestart').addEventListener('click', () => {
      state.clarify = { step: 1, somatic: [], situations: [], emotions: [], need: null, slots: {}, tplIndex: 0, includeEmotion: false, data: null, _prefillApplied: false };
      renderClarify();
    });
  }
}

function bindChips() {
  $$('.chip[data-kind]').forEach((chip) => {
    const kind = chip.dataset.kind;
    if (state.clarify[kind].includes(chip.dataset.id)) chip.classList.add('on');
    chip.addEventListener('click', () => {
      const arr = state.clarify[kind];
      const i = arr.indexOf(chip.dataset.id);
      i >= 0 ? arr.splice(i, 1) : arr.push(chip.dataset.id);
      chip.classList.toggle('on');
    });
  });
}

/* ==================== 记录 ==================== */
async function loadHistory() {
  const d = await api('/api/history?limit=60');
  const kindLabel = { decode: '听懂对方', compose: '把话说出去', clarify: '梳理思路', clarify_free: '自由梳理' };
  $('#historyBody').innerHTML = d.entries.length
    ? d.entries.map((e) => `
        <div class="history-item">
          <span class="history-kind">${esc(kindLabel[e.kind] || e.kind)}</span>
          <div class="history-text">
            ${esc((e.input || '').slice(0, 160))}${(e.input || '').length > 160 ? '…' : ''}
            ${e.output ? `<div style="color:var(--text-2);margin-top:3px">→ ${esc(e.output.slice(0, 160))}</div>` : ''}
            <div class="history-time">${esc(e.created_at)}</div>
          </div>
          <button class="btn-ghost" data-del="${e.id}">删除</button>
        </div>`).join('')
    : '<p class="hint">还没有记录。</p>';
  $$('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    await api('/api/history/' + b.dataset.del, { method: 'DELETE' });
    loadHistory();
  }));
}

$('#clearHistory').addEventListener('click', async () => {
  if (!confirm('清空全部本机记录？此操作不可恢复。')) return;
  await api('/api/history', { method: 'DELETE' });
  loadHistory();
  toast('已清空');
});

/* ==================== 设置 ==================== */
function openDrawer(open) {
  $('#settingsDrawer').hidden = !open;
  $('#drawerMask').hidden = !open;
  if (open) loadSettings();
}
$('#openSettings').addEventListener('click', () => openDrawer(true));
$('#closeSettings').addEventListener('click', () => openDrawer(false));
$('#drawerMask').addEventListener('click', () => openDrawer(false));

async function loadSettings() {
  const p = await api('/api/prefs');
  state.prefs = p.prefs;
  $('#prefEmotion').checked = !!p.prefs.emotion_module_enabled;
  $('#prefRationale').checked = !!p.prefs.show_rationale;
  $('#prefHedges').checked = !!p.prefs.default_hedges_on;

  $('#llmInfo').innerHTML = `
    <div><b>状态：</b>${esc(p.llm.reason)}</div>
    <div><b>模型：</b>${esc(p.llm.model)}</div>
    <div><b>地址：</b>${esc(p.llm.base_url)}</div>`;

  const g = await api('/api/guardrails');
  $('#guardrailList').innerHTML = g.rules.map((r) => `
    <div class="guardrail-item">
      <b>${esc(r.label)}</b><span class="sev sev-${esc(r.severity)}">${r.severity === 'block' ? '拦截' : '提示'}</span>
      <p>${esc(r.why)}</p>
    </div>`).join('') +
    (g.self_test_failures.length
      ? `<div class="err-box">红线自检有 ${g.self_test_failures.length} 项未通过</div>`
      : `<p class="setting-desc" style="margin-top:8px">对抗性自检全部通过。</p>`);

  renderSpeakerEditor();
}

$$('#prefEmotion, #prefRationale, #prefHedges').forEach((el) => {
  el.addEventListener('change', async () => {
    const updates = {
      emotion_module_enabled: $('#prefEmotion').checked,
      show_rationale: $('#prefRationale').checked,
      default_hedges_on: $('#prefHedges').checked,
    };
    const r = await api('/api/prefs', { body: updates });
    state.prefs = r.prefs;
    toast('已保存');
  });
});

async function loadSpeakers() {
  const d = await api('/api/speakers');
  state.speakers = d.speakers;
  state.vagueTerms = d.vague_terms;
  $('#speaker').innerHTML = '<option value="">未指定</option>' +
    d.speakers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
}

function renderSpeakerEditor() {
  const box = $('#speakerEditor');
  box.innerHTML = state.speakers.map((s) => `
    <div class="speaker-card">
      <b>${esc(s.name)}</b> <span class="hint">${esc(s.relation || '')}</span>
      ${s.notes ? `<div>${esc(s.notes)}</div>` : ''}
      ${Object.keys(s.overrides).length
        ? `<div style="margin-top:4px;color:var(--tone-good)">已校准：${Object.entries(s.overrides).map(([k, v]) => `${esc(k)} → ${esc(v)}`).join('；')}</div>`
        : ''}
      <div style="margin-top:6px"><button class="btn-ghost danger" data-delsp="${s.id}">删除</button></div>
    </div>`).join('') + `
    <div class="speaker-form">
      <input id="spName" placeholder="名字（例：张三）">
      <input id="spRelation" placeholder="关系（例：直属上级 / 伴侣）">
      <textarea id="spNotes" rows="2" placeholder="备注：这个人的沟通习惯"></textarea>
      <div class="override-row">
        <select id="spTerm">${state.vagueTerms.map((t) =>
          `<option value="${esc(t.id)}">${esc(t.patterns[0])}（默认 ${esc(t.default_range)}）</option>`).join('')}</select>
        <input id="spValue" placeholder="他的真实区间，例：3 个工作日">
      </div>
      <button class="btn-primary" id="spSave">保存这个人</button>
    </div>`;

  $$('[data-delsp]').forEach((b) => b.addEventListener('click', async () => {
    await api('/api/speakers/' + b.dataset.delsp, { method: 'DELETE' });
    await loadSpeakers(); renderSpeakerEditor();
  }));

  $('#spSave').addEventListener('click', async () => {
    const name = $('#spName').value.trim();
    if (!name) { toast('先填个名字'); return; }
    const existing = state.speakers.find((s) => s.name === name);
    const overrides = Object.assign({}, existing ? existing.overrides : {});
    const val = $('#spValue').value.trim();
    if (val) overrides[$('#spTerm').value] = val;
    const r = await api('/api/speakers', {
      body: { name, relation: $('#spRelation').value, notes: $('#spNotes').value, overrides },
    });
    if (r.ok) { await loadSpeakers(); renderSpeakerEditor(); toast('已保存'); }
  });
}

/* ==================== 全局复制委托 ==================== */
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-copy]');
  if (btn) copyText(btn.dataset.copy);
});

/* ==================== 初始化 ==================== */
(async function init() {
  await loadSpeakers();
  if (!$('#llmBadge').classList.contains('badge-on')) {
    $('#decodeLlm').disabled = true;
    $('#composeLlm').disabled = true;
    $('#decodeLlmWrap').title = '未配置大模型，规则引擎独立可用';
  }
})();
