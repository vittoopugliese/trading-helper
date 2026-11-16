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

  const blocked = v.blocked_reasons || [];
  if (v.mechanical_entry) {
    mechEl.style.display = 'block';
    mechEl.className = 'mechanical';
    mechEl.textContent = 'ENTRADA MECANICA — elegible: niveles, riesgo y gatillo validados';
  } else if (blocked.length) {
    mechEl.style.display = 'block';
    mechEl.className = 'mechanical stale';
    mechEl.textContent = `NO ELEGIBLE — ${blocked.slice(0, 3).join(' · ')}${blocked.length > 3 ? ` (+${blocked.length - 3})` : ''}`;
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
    // What the condition does: blocks (critical), adds weight to the score, or gates/informs.
    const role = c.critical ? 'obligatoria'
      : c.weight > 0 ? `peso ${Math.round(c.weight * 100)}%`
      : ['htf_alignment', 'atas_footprint'].includes(c.name) ? 'filtro A+' : 'info';
    li.textContent = `${c.passed ? '✓' : '✗'} [${role}] ${c.name}: ${c.detail}`;
    condEl.appendChild(li);
  });

  const ps = v.position_size || {};
  const levels = v.levels || {};
  $('sizing-entry').textContent = levels.entry != null ? levels.entry.toFixed(4) : '—';
  $('sizing-sl').textContent = levels.stop != null ? levels.stop.toFixed(4) : '—';
  $('sizing-tp').textContent = levels.target != null ? levels.target.toFixed(4) : '—';
  $('sizing-risk').textContent = ps.risk_amount != null ? `$${ps.risk_amount.toFixed(2)}` : '—';
  $('sizing-margin').textContent = ps.margin != null ? `$${ps.margin.toFixed(2)}` : '—';
  $('sizing-notional').textContent = ps.notional != null ? `$${ps.notional.toFixed(0)}` : '—';
  if (ps.capped_by && ps.capped_by.length) {
    $('sizing-notional').textContent += ` (tope: ${ps.capped_by.join(', ')})`;
  }
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
  const problems = [...(ctx.issues || [])];
  if (ctx.expired) problems.push(`contexto vencido: editado hace ${ctx.age_days} días`);
  $('ctx-issues').textContent = problems.length ? 'A corregir: ' + problems.join(' · ') : '';
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
    refreshFlow();

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

/* --- Order flow ATAS / liquidaciones / señales forward --- */

function esc(v) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(v ?? '').replace(/[&<>"']/g, c => map[c]);
}

function num(v, d = 2) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  return Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : Math.abs(n) >= 1e4 ? (n / 1e3).toFixed(1) + 'k' : n.toFixed(d);
}

function row(label, value, cls = '') {
  return `<div class="row"><span>${esc(label)}</span><span class="${cls}">${value}</span></div>`;
}

function setStatus(id, text, cls) {
  const el = $(id);
  el.textContent = text;
  el.className = 'flow-status ' + (cls || '');
}

function renderAtas(a) {
  if (!a || !a.available) {
    setStatus('atas-status', `Sin datos: ${a?.reason || 'agregá "Trading Helper Bridge" al chart de ATAS'}`, 'warn');
    $('atas-body').innerHTML = '';
    return;
  }
  const alt = a.requested_tf ? ` (pediste ${a.requested_tf})` : '';
  setStatus('atas-status', `${a.exchange || 'ATAS'} ${a.tf}${alt} · hace ${Math.round(a.age_sec)}s · ${a.closed_bars} velas`, 'ok');
  const lc = a.last_closed || {};
  const tags = side => (a.recent_signals?.[side] || []).map(t => `<span class="tag ${side}">${esc(t)}</span>`).join('') || '—';
  const bt = a.big_trades_15m || {};
  const dom = a.dom;
  $('atas-body').innerHTML = [
    row('Última vela Δ / vol', `${num(lc.delta, 0)} / ${num(lc.volume, 0)}${lc.volume_x ? ` (${lc.volume_x}x)` : ''}`, (lc.delta || 0) >= 0 ? 'long' : 'short'),
    row('CVD pendiente 5', num(a.cvd_slope_5, 0), (a.cvd_slope_5 || 0) >= 0 ? 'long' : 'short'),
    row('Divergencia CVD', esc(a.cvd_divergence || 'no'), a.cvd_divergence === 'bullish' ? 'long' : a.cvd_divergence === 'bearish' ? 'short' : ''),
    row('Big trades 15m (buy/sell)', `${num(bt.buy_volume, 0)} / ${num(bt.sell_volume, 0)}`, (bt.net || 0) >= 0 ? 'long' : 'short'),
    row('DOM ±1% bid/ask', dom ? `${esc(dom.ratio ?? '—')} (${esc(dom.bias)})` : 'sin DOM', dom?.bias === 'bullish' ? 'long' : dom?.bias === 'bearish' ? 'short' : ''),
    `<div style="margin-top:6px">LONG: ${tags('long')}</div>`,
    `<div>SHORT: ${tags('short')}</div>`,
  ].join('');
}

function renderLiquidations(l) {
  if (!l || l.error) {
    setStatus('liq-status', `Error: ${l?.error || 'sin respuesta'}`, 'warn');
    $('liq-body').innerHTML = '';
    return;
  }
  setStatus('liq-status', l.ready
    ? `${l.events_24h} eventos en 24h · ${l.coverage_hours}h de historial`
    : `Juntando historial (${l.coverage_hours}h de 6h): z-score todavía no disponible`, l.ready ? 'ok' : 'warn');
  const z = v => v == null ? '' : ` z=${v}`;
  $('liq-body').innerHTML = [
    row('Longs liquidados 5m / 15m / 60m', `$${num(l.long_usd_5m, 0)} / $${num(l.long_usd_15m, 0)} / $${num(l.long_usd_60m, 0)}`, 'short'),
    row('Shorts liquidados 5m / 15m / 60m', `$${num(l.short_usd_5m, 0)} / $${num(l.short_usd_15m, 0)} / $${num(l.short_usd_60m, 0)}`, 'long'),
    row('Flush de longs (15m)', (l.long_spike ? 'SÍ' : 'no') + z(l.long_z_15m), l.long_spike ? 'long' : ''),
    row('Squeeze de shorts (15m)', (l.short_spike ? 'SÍ' : 'no') + z(l.short_z_15m), l.short_spike ? 'short' : ''),
  ].join('');
}

function renderSignals(st) {
  const o = st?.overall || {};
  setStatus('sig-status', `${o.n || 0} resueltas · ${st?.open || 0} abiertas · mínimo ${st?.min_samples} por grupo para concluir`, o.n ? 'ok' : 'warn');
  const ci = o.win_rate_ci95 ? ` [${(o.win_rate_ci95[0] * 100).toFixed(0)}–${(o.win_rate_ci95[1] * 100).toFixed(0)}%]` : '';
  const rows = [
    row('WR', o.win_rate != null ? `${(o.win_rate * 100).toFixed(1)}%${ci}` : '—'),
    row('Expectancy', o.expectancy_r != null ? `${o.expectancy_r}R` : '—', (o.expectancy_r || 0) >= 0 ? 'long' : 'short'),
  ];
  const labels = { atas_confirmed: 'Con gatillo ATAS', liq_flush: 'Con flush de liquidaciones', at_poi: 'En POI', trend_aligned: 'A favor de tendencia' };
  for (const [feat, label] of Object.entries(labels)) {
    const f = st?.by_feature?.[feat];
    if (!f) continue;
    const fmt = c => c.n ? `${(c.win_rate * 100).toFixed(0)}% / ${c.expectancy_r}R (n=${c.n})` : 'n=0';
    rows.push(row(`${label}: sí | no`, `${fmt(f.con)} | ${fmt(f.sin)}${f.suficiente ? '' : ' *'}`));
  }
  rows.push('<p style="font-size:10px;color:var(--muted);margin-top:6px">* muestra insuficiente: no sacar conclusiones todavía.</p>');
  $('sig-body').innerHTML = rows.join('');
}

async function refreshFlow() {
  const sym = normSymbol($('symbol-input').value);
  if (!sym) return;
  const tf = $('tf-select').value;
  const [atas, liq, sig] = await Promise.allSettled([
    api(`/atas/snapshot/${sym}?timeframe=${tf}`),
    api(`/liquidations/${sym}`),
    api(`/signals/stats?symbol=${sym}`),
  ]);
  renderAtas(atas.status === 'fulfilled' ? atas.value : { available: false, reason: atas.reason?.message });
  renderLiquidations(liq.status === 'fulfilled' ? liq.value : { error: liq.reason?.message });
  if (sig.status === 'fulfilled') renderSignals(sig.value);
}

function browserNotify(title, body) {
  if (!('Notification' in window) || Notification.permission !== 'granted' || !document.hidden) return;
  try { new Notification(title, { body, tag: title }); } catch { /* ignore */ }
}

async function saveContext() {
  const sym = normSymbol($('symbol-input').value);
  const targets = $('ctx-targets').value.split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n));
  try {
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
  } catch (e) {
    let msg = e.message;
    try { const d = JSON.parse(e.message).detail; msg = `${d.message}: ${(d.issues || []).join(' · ')}`; } catch { /* plain text */ }
    $('ctx-issues').textContent = msg;
    return;
  }
  $('ctx-issues').textContent = '';
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
        const title = p.title || `${(p.direction || '').toUpperCase()} ${p.trigger || ''}`.trim();
        const body = p.message || p.detail || '';
        pushAlert(`ALERTA: ${title}${body ? ' — ' + body : ''}`, 'alert');
        browserNotify(title, body);
      } else if (data.type === 'signals') {
        renderSignals(data.payload);
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
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
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
  loadNotesStatus();
}

async function loadNotesStatus() {
  try {
    const st = await api('/notes/status');
    const pending = st.stale.length + st.missing.length + st.orphans.length;
    setStatus('notes-status', pending
      ? `${pending} archivo(s) desactualizados (editados ${st.stale.length}, nuevos ${st.missing.length}, borrados ${st.orphans.length}). Reindexá.`
      : `Al día: ${st.files_on_disk} notas, ${st.chunks} fragmentos (pipeline v${st.pipeline_version})`, pending ? 'warn' : 'ok');
  } catch (e) {
    setStatus('notes-status', 'Error: ' + e.message, 'warn');
  }
}

async function reindexNotes() {
  setStatus('notes-status', 'Reindexando...', '');
  try { await api('/notes/reindex', { method: 'POST' }); } catch (e) { /* status shows it */ }
  loadNotesStatus();
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

async function calcManualSizing() {
  // Same policy as the engine (server side): risk cap, margin cap, costs, open exposure.
  const entry = parseFloat($('calc-entry').value);
  const sl = parseFloat($('calc-sl').value);
  if (!entry || !sl) return;
  const params = new URLSearchParams({ entry, stop: sl });
  const cap = parseFloat($('calc-cap').value);
  const riskPct = parseFloat($('calc-risk-pct').value);
  if (cap) params.set('account', cap);
  if (riskPct) params.set('risk_pct', riskPct);
  try {
    const r = await api(`/sizing?${params}`);
    if (r.blocked_reason) {
      $('calc-result').textContent = `Bloqueado: ${r.blocked_reason}`;
      return;
    }
    const caps = r.capped_by && r.capped_by.length ? ` | tope: ${r.capped_by.join(', ')}` : '';
    const guard = r.daily_guard?.blocked ? ` | ${r.daily_guard.detail}` : '';
    $('calc-result').textContent =
      `SL ${r.stop_pct}% | Riesgo $${r.risk_amount} (incl. costos) | Qty ${r.position_size} | Notional $${r.notional} | Margen $${r.margin} x${r.leverage}${caps}${guard}`;
  } catch (e) {
    $('calc-result').textContent = 'Error: ' + e.message;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.app-tab').forEach(tab => {
    tab.addEventListener('click', () => showAppView(tab.dataset.view));
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
  $('reindex-btn').addEventListener('click', reindexNotes);

  document.querySelectorAll('[data-quick]').forEach(btn => {
    btn.addEventListener('click', () => sendChat(btn.dataset.quick));
  });

  connectSSE();
  refreshCockpit();
  updateWatcherStatus();
  loadSettings();
  loadJournal();

  setInterval(refreshCockpit, 60000);
  setInterval(refreshFlow, 10000);
});
