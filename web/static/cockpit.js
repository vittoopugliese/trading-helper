/* Trading Cockpit — live dashboard */

const API = '/api';
let eventSource = null;
let chatStreaming = false;

function $(id) { return document.getElementById(id); }

function normSymbol(s) {
  s = (s || '').toUpperCase().trim();
  if (s && !s.endsWith('USDT')) s += 'USDT';
  return s;
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, opts);
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || res.statusText);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res.text();
}

function showAppView(name) {
  document.querySelectorAll('.app-view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.app-tab').forEach(t => t.classList.remove('active'));
  const view = $('view-' + name);
  const tab = document.querySelector(`.app-tab[data-view="${name}"]`);
  if (view) view.classList.add('active');
  if (tab) tab.classList.add('active');
}

function renderVerdict(data) {
  const v = data.verdict || {};
  const gradeEl = $('verdict-grade');
  const mechEl = $('verdict-mechanical');
  const summaryEl = $('verdict-summary');
  const condEl = $('verdict-conditions');
  const patternsEl = $('verdict-patterns');

  const grade = v.grade || 'NO_TRADE';
  gradeEl.textContent = `${grade} ${(v.direction || '').toUpperCase()} ${Math.round((v.score || 0) * 100)}%`;
  gradeEl.className = 'verdict-grade ' + (grade === 'A+' ? 'aplus' : grade === 'B' ? 'b' : 'notrade');

  if (v.mechanical_entry) {
    mechEl.style.display = 'block';
    mechEl.className = 'mechanical';
    mechEl.textContent = 'ENTRADA MECANICA — ejecuta con SL estructural';
  } else if (v.context_stale) {
    const drift = v.context_drift_pct != null ? ` (${(v.context_drift_pct * 100).toFixed(1)}% del POI)` : '';
    mechEl.style.display = 'block';
    mechEl.className = 'mechanical stale';
    mechEl.textContent = `CONTEXTO DESACTUALIZADO${drift} — niveles NO operables, rehace el conteo Elliott`;
  } else {
    mechEl.style.display = 'none';
  }

  summaryEl.textContent = v.summary || '';
  patternsEl.textContent = (v.of_patterns || []).join(', ') || '—';

  condEl.innerHTML = '';
  (v.conditions || []).forEach(c => {
    const li = document.createElement('li');
    li.className = c.passed ? 'pass' : 'fail';
    li.textContent = `${c.passed ? '✓' : '✗'} ${c.name}: ${c.detail}`;
    condEl.appendChild(li);
  });

  const ps = v.position_size || {};
  const levels = v.levels || {};
  $('sizing-entry').textContent = levels.entry != null ? levels.entry.toFixed(4) : '—';
  $('sizing-sl').textContent = levels.stop != null ? levels.stop.toFixed(4) : '—';
  $('sizing-tp').textContent = levels.target != null ? levels.target.toFixed(4) : '—';
  $('sizing-risk').textContent = ps.risk_usd != null ? `$${ps.risk_usd.toFixed(2)}` : '—';
  $('sizing-margin').textContent = ps.margin_usd != null ? `$${ps.margin_usd.toFixed(2)}` : '—';
  $('sizing-notional').textContent = ps.notional_usd != null ? `$${ps.notional_usd.toFixed(0)}` : '—';
}

function renderMultiTf(mtf) {
  const tbody = $('mtf-body');
  tbody.innerHTML = '';
  Object.entries(mtf || {}).forEach(([tf, row]) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${tf}</td>
      <td>${row.close}${row.stale_cache ? ' ⚠' : ''}</td>
      <td>${row.trend || '—'}</td>
      <td>${row.buy_ratio != null ? row.buy_ratio.toFixed(2) : '—'}</td>
      <td>${row.delta_sum_8 != null ? (row.delta_sum_8 > 0 ? '+' : '') + row.delta_sum_8 : '—'}</td>
      <td>${row.rsi != null ? row.rsi.toFixed(1) : '—'}</td>`;
    tbody.appendChild(tr);
  });
}

function renderContext(ctx) {
  if (!ctx) return;
  $('ctx-bias').value = ctx.bias || 'none';
  $('ctx-wave').value = ctx.wave || '';
  $('ctx-poi-low').value = ctx.poi?.low ?? '';
  $('ctx-poi-high').value = ctx.poi?.high ?? '';
  $('ctx-inv').value = ctx.invalidation ?? '';
  $('ctx-targets').value = (ctx.targets || []).join(', ');
  $('ctx-notes').value = ctx.notes || '';
  const img = $('chart-preview');
  if (ctx.has_chart || ctx.chart_image) {
    img.src = `${API}/context/${normSymbol($('symbol-input').value)}/chart?t=${Date.now()}`;
    img.style.display = 'block';
  } else {
    img.style.display = 'none';
  }
}

async function refreshCockpit() {
  const sym = normSymbol($('symbol-input').value);
  if (!sym) return;
  $('refresh-btn').disabled = true;
  try {
    const [analysis, mtf, btc] = await Promise.all([
      api(`/analyze/${sym}?timeframe=${$('tf-select').value}&trade_type=${$('trade-type').value}`),
      api(`/multi-tf/${sym}`),
      sym !== 'BTCUSDT' ? api('/btc-context') : Promise.resolve(null),
    ]);

    $('live-price').textContent = analysis.price_live?.toFixed(4) || '—';
    renderVerdict(analysis);
    renderMultiTf(mtf);

    const btcEl = $('btc-banner');
    if (btc && sym !== 'BTCUSDT') {
      btcEl.style.display = 'block';
      btcEl.className = 'btc-ok ' + (btc.favorable_for_alts_long ? 'btc-ok' : 'btc-bad');
      btcEl.textContent = `BTC ${btc.price?.toFixed(0)} ${btc.trend} | ${btc.label}`;
    } else {
      btcEl.style.display = 'none';
    }

    try {
      const ctx = await api(`/context/${sym}`);
      renderContext(ctx);
    } catch {
      /* no context yet */
    }
  } catch (e) {
    $('verdict-summary').textContent = 'Error: ' + e.message;
  } finally {
    $('refresh-btn').disabled = false;
  }
}

async function saveContext() {
  const sym = normSymbol($('symbol-input').value);
  const targets = $('ctx-targets').value.split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n));
  await api(`/context/${sym}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      bias: $('ctx-bias').value,
      wave: $('ctx-wave').value,
      poi_low: parseFloat($('ctx-poi-low').value) || null,
      poi_high: parseFloat($('ctx-poi-high').value) || null,
      invalidation: parseFloat($('ctx-inv').value) || null,
      targets,
      notes: $('ctx-notes').value,
    }),
  });
  await refreshCockpit();
}

async function uploadChart(file) {
  if (!file) return;
  const sym = normSymbol($('symbol-input').value);
  const fd = new FormData();
  fd.append('file', file);
  await fetch(`${API}/context/${sym}/chart`, { method: 'POST', body: fd });
  await refreshCockpit();
}

function pushAlert(text, cls = 'scan') {
  const feed = $('alert-feed');
  const div = document.createElement('div');
  div.className = 'item ' + cls;
  div.textContent = text;
  feed.prepend(div);
  while (feed.children.length > 50) feed.removeChild(feed.lastChild);
}

function connectSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`${API}/events`);
  eventSource.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === 'ping' || data.type === 'connected') return;
      if (data.type === 'scan') {
        const p = data.payload || {};
        pushAlert(`[scan] ${p.symbol} ${p.price?.toFixed(4)} ${p.grade} ${Math.round((p.score || 0) * 100)}%`);
        if (p.symbol === normSymbol($('symbol-input').value)) {
          renderVerdict({ verdict: p.verdict, snapshot: p.snapshot });
        }
      } else if (data.type === 'alert') {
        const p = data.payload || {};
        pushAlert(`ALERTA: ${p.title || p.trigger || JSON.stringify(p)}`, 'alert');
      } else if (data.type === 'execution') {
        const p = data.payload || {};
        const labels = { placed: 'enviada', filled: 'FILL', closed: 'cerrada', cancelled: 'cancelada', failed: 'falló', proposal: 'propuesta' };
        if (data.kind !== 'sync_error') {
          const detail = p.reason || p.result || '';
          const cls = data.kind === 'filled' || data.kind === 'closed' ? 'alert' : 'scan';
          pushAlert(`[exec] #${p.id ?? ''} ${labels[data.kind] || data.kind} ${detail}`.trim(), cls);
        }
        if ($('view-execution').classList.contains('active')) loadExecution();
      } else if (data.type === 'watcher_started') {
        pushAlert('Watcher iniciado', 'scan');
        updateWatcherStatus();
      } else if (data.type === 'watcher_stopped') {
        pushAlert('Watcher detenido', 'scan');
        updateWatcherStatus();
      }
    } catch { /* ignore */ }
  };
  eventSource.onerror = () => {
    setTimeout(connectSSE, 5000);
  };
}

async function startWatcher() {
  const sym = normSymbol($('symbol-input').value);
  await api('/watcher/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol: sym,
      timeframe: $('tf-select').value,
      scalp: true,
      bias: $('watcher-bias').value,
      interval_sec: parseInt($('watcher-interval').value, 10) || 30,
    }),
  });
  updateWatcherStatus();
}

async function stopWatcher() {
  await api('/watcher/stop', { method: 'POST' });
  updateWatcherStatus();
}

async function updateWatcherStatus() {
  const st = await api('/watcher/status');
  $('watcher-status').textContent = st.running
    ? `ON — ${st.symbol} cada ${st.interval_sec}s`
    : 'OFF';
}

async function loadSettings() {
  const s = await api('/settings');
  $('set-account').value = s.account_size;
  $('set-leverage').value = s.leverage;
  $('set-max-risk').value = s.max_risk_usd;
  $('set-key-status').textContent = s.has_anthropic_key ? 'API key configurada' : 'Sin API key';
  $('set-bingx-status').textContent = (s.has_bingx_keys ? 'Keys configuradas' : 'Sin keys') + ` · modo ${s.execution_mode}`;
}

async function saveRiskSettings() {
  await api('/settings/risk', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      default_account_size: parseFloat($('set-account').value),
      leverage: parseInt($('set-leverage').value, 10),
      max_risk_usd: parseFloat($('set-max-risk').value),
    }),
  });
  await loadSettings();
}

async function saveApiKey() {
  const key = $('set-api-key').value.trim();
  if (!key) return;
  await api('/settings/anthropic-key', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: key }),
  });
  $('set-api-key').value = '';
  await loadSettings();
}

async function loadJournal() {
  const stats = await api('/journal/stats');
  $('journal-stats').textContent = stats.message
    ? stats.message
    : `${stats.total} trades | WR ${stats.win_rate}% | avg R ${stats.avg_r}`;
  const recent = await api('/journal/recent?limit=10');
  const el = $('journal-recent');
  el.innerHTML = recent.map(t =>
    `<div class="item">${t.created_at?.slice(0, 16)} ${t.symbol} ${t.direction} ${t.result} entry=${t.entry}</div>`
  ).join('') || '<div class="item">Sin trades</div>';
}

function appendChat(role, text) {
  const log = $('chat-log');
  const div = document.createElement('div');
  div.className = 'chat-msg ' + role;
  div.textContent = (role === 'user' ? 'Tú: ' : 'Bot: ') + text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

async function sendChat(quickAction) {
  if (chatStreaming) return;
  const msg = $('chat-input').value.trim();
  if (!msg && !quickAction) return;
  chatStreaming = true;
  $('chat-send').disabled = true;
  const withBooks = (document.getElementById('chat-books')?.checked) || quickAction === 'analyze_books';
  const booksTag = withBooks ? '📚 ' : '';
  appendChat('user', booksTag + (quickAction ? `[${quickAction}] ${msg}`.trim() : msg));
  $('chat-input').value = '';
  const assistantEl = appendChat('assistant', '');

  try {
    const res = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: msg || quickAction,
        symbol: normSymbol($('symbol-input').value),
        timeframe: $('tf-select').value,
        trade_type: $('trade-type').value,
        include_chart: true,
        quick_action: quickAction || null,
        with_books: withBooks,
      }),
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') continue;
        try {
          const j = JSON.parse(payload);
          if (j.text) assistantEl.textContent = 'Bot: ' + (assistantEl.textContent.replace(/^Bot: /, '') + j.text);
          if (j.error) assistantEl.textContent = 'Bot: Error — ' + j.error;
          if (j.proposal) renderChatProposal(j.proposal);
        } catch { /* skip */ }
      }
    }
  } catch (e) {
    assistantEl.textContent = 'Bot: Error — ' + e.message;
  } finally {
    chatStreaming = false;
    $('chat-send').disabled = false;
  }
}

function calcManualSizing() {
  const cap = parseFloat($('calc-cap').value) || 300;
  const riskPct = parseFloat($('calc-risk-pct').value) || 7;
  const entry = parseFloat($('calc-entry').value);
  const sl = parseFloat($('calc-sl').value);
  const lev = parseFloat($('calc-lev').value) || 10;
  if (!entry || !sl) return;
  const slPct = Math.abs(entry - sl) / entry;
  const riskUsd = Math.min(cap * riskPct / 100, 21);
  const notional = riskUsd / slPct;
  const margin = notional / lev;
  $('calc-result').textContent =
    `SL ${(slPct * 100).toFixed(2)}% | Riesgo $${riskUsd.toFixed(2)} | Notional $${notional.toFixed(0)} | Margen $${margin.toFixed(0)}`;
}

/* --- Ejecucion BingX --- */

let exMode = 'demo';

function esc(v) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(v ?? '').replace(/[&<>"']/g, c => map[c]);
}

function fmt(n, d = 4) {
  return n == null || isNaN(n) ? '—' : Number(n).toFixed(d);
}

// Execution routes require X-Cockpit: forces a CORS preflight so other sites can't call them.
async function exApi(path, opts = {}) {
  const headers = { 'X-Cockpit': '1', ...(opts.body ? { 'Content-Type': 'application/json' } : {}) };
  const res = await fetch(`${API}/execution${path}`, { ...opts, headers });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error((data && data.detail) || text || res.statusText);
  return data;
}

function exShowError(msg) {
  const el = $('ex-error');
  el.textContent = msg || '';
  el.style.display = msg ? 'block' : 'none';
}

function proposalHtml(rec) {
  const p = rec.plan || {};
  const errs = (rec.errors || []).map(e => `<li class="err">✗ ${esc(e)}</li>`).join('');
  const warns = (rec.warnings || []).map(w => `<li class="warn">! ${esc(w)}</li>`).join('');
  let actions = '';
  if (rec.status === 'pending') {
    const live = rec.mode === 'live';
    actions = `<div class="ex-confirm">
      ${live ? `<input data-confirm="${rec.id}" placeholder="LIVE">` : ''}
      <button class="cp-btn success" data-approve="${rec.id}">Aprobar y enviar</button>
      <button class="cp-btn" data-reject="${rec.id}">Rechazar</button>
      <span style="color:var(--muted)">vence ${esc((rec.expires_at || '').slice(11, 19))} UTC</span>
    </div>`;
  }
  return `
    <div class="ex-kv">
      <span>#</span><span>${rec.id} · <span class="ex-status ${esc(rec.status)}">${esc(rec.status)}</span> · ${esc(rec.mode)} · ${esc(rec.source)}</span>
      <span>Orden</span><span>${esc((p.side || '').toUpperCase())} ${esc(p.symbol)} ${fmt(p.qty)} @ ${fmt(p.entry)} (post-only)</span>
      <span>SL / TP</span><span>${fmt(p.stop)} / ${fmt(p.take_profit)} · SL ${fmt(p.stop_pct, 2)}% · R:R ${fmt(p.rr, 2)}</span>
      <span>Riesgo</span><span>$${fmt(p.risk_usd, 2)} · notional $${fmt(p.notional_usd, 0)} · margen $${fmt(p.margin_usd, 2)} (x${esc(p.leverage)})</span>
      <span>Comisiones</span><span>si SL $${fmt(p.fees_if_stop_usd, 3)} (${fmt(p.fees_if_stop_r, 2)}R) · si TP $${fmt(p.fees_if_target_usd, 3)}</span>
      ${rec.rationale ? `<span>Motivo</span><span>${esc(rec.rationale)}</span>` : ''}
      ${rec.note ? `<span>Nota</span><span>${esc(rec.note)}</span>` : ''}
    </div>
    <ul class="ex-msgs">${errs}${warns}</ul>
    ${actions}`;
}

function renderPreview(rec) {
  $('ex-preview').innerHTML = rec ? proposalHtml(rec) : 'Sin propuesta.';
}

function renderPositions(positions) {
  const el = $('ex-positions');
  if (!positions || !positions.length) { el.textContent = 'Sin posiciones.'; return; }
  el.innerHTML = `<table class="mtf-table"><thead><tr><th>Símbolo</th><th>Lado</th><th>Qty</th><th>Entry</th><th>Mark</th><th>PnL</th><th>Liq</th><th></th></tr></thead><tbody>
    ${positions.map(p => `<tr>
      <td>${esc(p.symbol)}</td><td>${esc(p.side)}</td><td>${fmt(p.qty)}</td><td>${fmt(p.entry_price)}</td>
      <td>${fmt(p.mark_price)}</td><td>${fmt(p.unrealized_pnl, 2)}</td><td>${fmt(p.liquidation_price)}</td>
      <td><button class="cp-btn danger" data-close="${esc(p.symbol)}">Cerrar a mercado</button></td></tr>`).join('')}
  </tbody></table>`;
}

let exRecords = [];

function renderRecords(records) {
  const el = $('ex-records');
  exRecords = records || [];
  if (!exRecords.length) { el.textContent = 'Sin órdenes todavía.'; return; }
  el.innerHTML = `<table class="mtf-table"><thead><tr><th>#</th><th>Hora</th><th>Orden</th><th>Riesgo</th><th>Estado</th><th>Resultado</th><th></th></tr></thead><tbody>
    ${exRecords.map(r => {
      const p = r.plan || {};
      let btn = '';
      if (r.status === 'pending') btn = `<button class="cp-btn" data-show="${r.id}">Revisar</button>`;
      if (r.status === 'placed') btn = `<button class="cp-btn" data-cancel="${r.id}">Cancelar</button>`;
      const result = r.rr_real != null
        ? `${r.rr_real > 0 ? '+' : ''}${fmt(r.rr_real, 2)}R`
        : (r.note || (r.errors || [])[0] || '');
      return `<tr>
        <td>${r.id}</td><td>${esc((r.created_at || '').slice(5, 16).replace('T', ' '))}</td>
        <td>${esc(p.side)} ${esc(r.symbol)} ${fmt(p.qty)} @ ${fmt(p.entry)} SL ${fmt(p.stop)} TP ${fmt(p.take_profit)}</td>
        <td>$${fmt(p.risk_usd, 2)}</td><td><span class="ex-status ${esc(r.status)}">${esc(r.status)}</span></td>
        <td title="${esc(result)}">${esc(String(result).slice(0, 60))}</td><td>${btn}</td></tr>`;
    }).join('')}
  </tbody></table>`;
}

async function loadExecution() {
  try {
    const st = await exApi('/status');
    exMode = st.mode;
    const modeEl = $('ex-mode');
    modeEl.textContent = st.mode === 'live' ? 'LIVE' : 'DEMO (VST)';
    modeEl.className = 'ex-mode' + (st.mode === 'live' ? ' live' : '');
    $('ex-account').textContent = st.connected
      ? `Equity ${fmt(st.equity, 2)} · libre ${fmt(st.free, 2)} · riesgo máx/orden $${fmt(st.risk_budget_usd, 2)}`
      : `Sin conexión: ${st.error || 'configurá las keys en Settings'}`;
    const lim = st.limits || {};
    const lossBad = st.daily_loss_usd != null && st.daily_loss_usd >= lim.max_daily_loss_usd;
    const streakBad = st.consecutive_losses >= lim.consecutive_loss_stop;
    $('ex-guard').innerHTML = `<span class="${lossBad ? 'ex-guard-bad' : 'ex-guard-ok'}">pérdida hoy $${fmt(st.daily_loss_usd, 2)} / ${esc(lim.max_daily_loss_usd)}</span>
      · <span class="${streakBad ? 'ex-guard-bad' : 'ex-guard-ok'}">racha pérdidas ${esc(st.consecutive_losses)} / ${esc(lim.consecutive_loss_stop)}</span>
      ${st.halted ? ' · <span class="ex-guard-bad">KILL SWITCH ACTIVO</span>' : ''}
      ${st.monitor_error ? ` · <span class="ex-guard-bad">monitor: ${esc(st.monitor_error)}</span>` : ''}`;
    $('ex-halt').style.display = st.halted ? 'none' : '';
    $('ex-resume').style.display = st.halted ? '' : 'none';
    renderPositions(st.positions);
    renderRecords(st.records);
  } catch (e) {
    exShowError(e.message);
  }
}

async function exSuggest(source) {
  exShowError('');
  const sym = normSymbol($('ex-symbol').value);
  try {
    const s = await exApi(`/suggest/${sym}?source=${source}&timeframe=${$('tf-select').value}&trade_type=${$('trade-type').value}`);
    $('ex-symbol').value = s.symbol;
    $('ex-side').value = s.side;
    $('ex-entry').value = s.entry;
    $('ex-stop').value = s.stop;
    $('ex-tp').value = s.take_profit;
    $('ex-rationale').value = s.rationale || '';
    $('ex-propose').dataset.source = source;
  } catch (e) {
    exShowError(e.message);
  }
}

async function exPropose() {
  exShowError('');
  const body = {
    symbol: normSymbol($('ex-symbol').value),
    side: $('ex-side').value,
    entry: parseFloat($('ex-entry').value),
    stop: parseFloat($('ex-stop').value),
    take_profit: parseFloat($('ex-tp').value),
    timeframe: $('tf-select').value,
    source: $('ex-propose').dataset.source || 'manual',
    rationale: $('ex-rationale').value,
  };
  if ([body.entry, body.stop, body.take_profit].some(n => !(n > 0))) {
    exShowError('Completá entry, SL y TP con precios válidos.');
    return;
  }
  try {
    renderPreview(await exApi('/proposals', { method: 'POST', body: JSON.stringify(body) }));
    $('ex-propose').dataset.source = 'manual';
    loadExecution();
  } catch (e) {
    exShowError(e.message);
  }
}

async function exApprove(id, root) {
  exShowError('');
  const input = root.querySelector(`[data-confirm="${id}"]`);
  try {
    const rec = await exApi(`/proposals/${id}/approve`, {
      method: 'POST', body: JSON.stringify({ confirm: input ? input.value : null }),
    });
    renderPreview(rec);
    pushAlert(`Orden #${id} enviada: ${rec.plan.side} ${rec.symbol} ${rec.plan.qty} @ ${rec.plan.entry}`, 'alert');
  } catch (e) {
    exShowError(e.message);
    if (root.classList.contains('chat-proposal')) window.alert(e.message);
  }
  loadExecution();
}

async function exAction(path, okMsg) {
  exShowError('');
  try {
    const out = await exApi(path, { method: 'POST', body: '{}' });
    if (okMsg) pushAlert(okMsg, 'scan');
    return out;
  } catch (e) {
    exShowError(e.message);
    return null;
  } finally {
    loadExecution();
  }
}

async function exClose(symbol) {
  let confirmText = null;
  if (exMode === 'live') {
    confirmText = window.prompt(`Cerrar ${symbol} a mercado (taker). Escribí LIVE para confirmar:`);
    if (confirmText === null) return;
  } else if (!window.confirm(`Cerrar ${symbol} a mercado (taker)?`)) {
    return;
  }
  exShowError('');
  try {
    await exApi(`/positions/${symbol}/close`, { method: 'POST', body: JSON.stringify({ confirm: confirmText }) });
    pushAlert(`Cierre a mercado enviado: ${symbol}`, 'alert');
  } catch (e) {
    exShowError(e.message);
  }
  setTimeout(loadExecution, 1500);
}

// One delegated handler for buttons rendered in the preview, tables and chat cards.
async function exClick(ev) {
  const t = ev.target.closest('button');
  if (!t) return;
  const root = ev.currentTarget;
  if (t.dataset.approve) {
    t.disabled = true;
    await exApprove(Number(t.dataset.approve), root);
  } else if (t.dataset.reject) {
    const rec = await exAction(`/proposals/${t.dataset.reject}/reject`);
    if (rec) renderPreview(rec);
    if (rec && root.classList.contains('chat-proposal')) root.innerHTML = proposalHtml(rec);
  } else if (t.dataset.cancel) {
    await exAction(`/orders/${t.dataset.cancel}/cancel`, `Orden #${t.dataset.cancel} cancelada`);
  } else if (t.dataset.close) {
    await exClose(t.dataset.close);
  } else if (t.dataset.show) {
    renderPreview(exRecords.find(r => r.id === Number(t.dataset.show)));
  }
}

function renderChatProposal(rec) {
  const div = document.createElement('div');
  div.className = 'chat-proposal';
  div.innerHTML = `<strong>Propuesta de orden (${esc(rec.mode)})</strong>${proposalHtml(rec)}`;
  div.addEventListener('click', exClick);
  $('chat-log').appendChild(div);
  $('chat-log').scrollTop = $('chat-log').scrollHeight;
}

async function saveBingxKeys() {
  const key = $('set-bingx-key').value.trim();
  const secret = $('set-bingx-secret').value.trim();
  if (!key || !secret) return;
  await exApi('/keys', { method: 'PUT', body: JSON.stringify({ api_key: key, api_secret: secret }) });
  $('set-bingx-key').value = '';
  $('set-bingx-secret').value = '';
  await loadSettings();
  loadExecution();
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.app-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      showAppView(tab.dataset.view);
      if (tab.dataset.view === 'execution') {
        $('ex-symbol').value = normSymbol($('symbol-input').value);
        loadExecution();
      }
    });
  });

  $('refresh-btn').addEventListener('click', refreshCockpit);
  $('save-ctx-btn').addEventListener('click', saveContext);
  $('chart-upload').addEventListener('change', e => uploadChart(e.target.files[0]));
  $('watcher-start').addEventListener('click', startWatcher);
  $('watcher-stop').addEventListener('click', stopWatcher);
  $('save-risk-btn').addEventListener('click', saveRiskSettings);
  $('save-key-btn').addEventListener('click', saveApiKey);
  $('chat-send').addEventListener('click', () => sendChat(null));
  $('calc-sizing-btn').addEventListener('click', calcManualSizing);
  $('ex-from-context').addEventListener('click', () => exSuggest('context'));
  $('ex-from-verdict').addEventListener('click', () => exSuggest('verdict'));
  $('ex-propose').addEventListener('click', exPropose);
  $('ex-halt').addEventListener('click', () => exAction('/halt', 'Kill switch activado'));
  $('ex-resume').addEventListener('click', () => exAction('/resume', 'Ejecución reanudada'));
  $('save-bingx-btn').addEventListener('click', saveBingxKeys);
  $('view-execution').addEventListener('click', exClick);

  document.querySelectorAll('[data-quick]').forEach(btn => {
    btn.addEventListener('click', () => sendChat(btn.dataset.quick));
  });

  connectSSE();
  refreshCockpit();
  updateWatcherStatus();
  loadSettings();
  loadJournal();

  setInterval(refreshCockpit, 60000);
  loadExecution();
  setInterval(() => {
    if ($('view-execution').classList.contains('active')) loadExecution();
  }, 10000);
});
