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
  $('sizing-tp').textContent = (levels.targets && levels.targets[0] != null) ? levels.targets[0].toFixed(4) : '—';
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

  document.querySelectorAll('[data-quick]').forEach(btn => {
    btn.addEventListener('click', () => sendChat(btn.dataset.quick));
  });

  connectSSE();
  refreshCockpit();
  updateWatcherStatus();
  loadSettings();
  loadJournal();

  setInterval(refreshCockpit, 60000);
});
