/* Blind review of Elliott 1-2 candidates (BACKTEST EW tab). */
(() => {
  const el = id => document.getElementById(id);
  const T0 = 1000000;          // synthetic time origin: real dates stay hidden
  const STEP = 900;            // seconds per bar on the synthetic axis
  const toTime = i => (T0 + i) * STEP;
  const fromTime = t => Math.round(t / STEP) - T0;
  const COLORS = { up: '#52d273', down: '#e8213f', gold: '#d4a437', cyan: '#23c8de', muted: '#8f86a3' };

  let chart = null;
  let current = null;

  function api(path, opts = {}) {
    return fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts }).then(async res => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || res.statusText);
      return body;
    });
  }

  function fmtR(x) { return x === null || x === undefined ? '—' : `${x > 0 ? '+' : ''}${Number(x).toFixed(2)}R`; }
  function fmtPct(x) { return x === null || x === undefined ? '—' : `${Number(x).toFixed(1)}%`; }

  function draw(payload) {
    const box = el('ew-chart');
    if (chart) { chart.remove(); chart = null; }
    chart = LightweightCharts.createChart(box, {
      autoSize: true,
      layout: { background: { color: '#12101a' }, textColor: '#ece7f2', fontFamily: 'IBM Plex Mono, monospace' },
      grid: { vertLines: { color: '#221c30' }, horzLines: { color: '#221c30' } },
      timeScale: { tickMarkFormatter: t => String(fromTime(t)), rightOffset: 8 },
      localization: { timeFormatter: t => `vela ${fromTime(t)}` },
      crosshair: { mode: 0 },
    });
    const candles = chart.addCandlestickSeries({
      upColor: COLORS.up, downColor: COLORS.down, wickUpColor: COLORS.up, wickDownColor: COLORS.down, borderVisible: false,
      priceFormat: { type: 'custom', formatter: p => `${p.toFixed(2)}%`, minMove: 0.01 },
    });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.3 } });
    candles.setData(payload.bars.map(b => ({ time: toTime(b.i), open: b.o, high: b.h, low: b.l, close: b.c })));

    const volume = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.72, bottom: 0.12 } });
    volume.setData(payload.bars.map(b => ({
      time: toTime(b.i), value: b.v, color: b.d >= 0 ? 'rgba(82,210,115,0.55)' : 'rgba(232,33,63,0.55)',
    })));
    const delta = chart.addHistogramSeries({ priceScaleId: 'delta', lastValueVisible: false, priceLineVisible: false });
    delta.priceScale().applyOptions({ scaleMargins: { top: 0.9, bottom: 0 } });
    delta.setData(payload.bars.map(b => ({ time: toTime(b.i), value: b.d, color: b.d >= 0 ? COLORS.up : COLORS.down })));

    const showMarks = el('ew-show-marks').checked;
    if (showMarks) {
      const isLong = payload.side === 'long';
      const labels = { h_prev: 'prev', l0: '0', h1: '1' };
      const markers = Object.entries(payload.marks).map(([key, m]) => {
        const above = (key === 'l0') !== isLong;
        return { time: toTime(m.i), position: above ? 'aboveBar' : 'belowBar', color: COLORS.cyan, shape: above ? 'arrowDown' : 'arrowUp', text: labels[key] };
      });
      if (payload.outcome) {
        markers.push({ time: toTime(0), position: isLong ? 'belowBar' : 'aboveBar', color: COLORS.gold, shape: 'circle', text: 'decisión' });
        markers.push({ time: toTime(payload.outcome.exit_i), position: 'aboveBar', color: payload.outcome.net_r > 0 ? COLORS.up : COLORS.down, shape: 'square', text: `salida ${fmtR(payload.outcome.net_r)}` });
      }
      candles.setMarkers(markers.sort((a, b) => a.time - b.time));
      Object.entries(payload.fibs).forEach(([lvl, price]) => {
        candles.createPriceLine({ price, color: COLORS.gold, lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: lvl });
      });
    }
    candles.createPriceLine({ price: payload.plan.stop, color: COLORS.down, lineWidth: 1, lineStyle: 0, title: 'SL' });
    candles.createPriceLine({ price: payload.plan.target, color: COLORS.up, lineWidth: 1, lineStyle: 0, title: 'TP 1.618' });
    candles.createPriceLine({ price: 0, color: COLORS.muted, lineWidth: 1, lineStyle: 1, title: 'entrada' });
    const from = Math.max(payload.bars[0].i, -160);
    const to = payload.outcome ? payload.bars[payload.bars.length - 1].i + 5 : 12;
    chart.timeScale().setVisibleLogicalRange({ from: from - payload.bars[0].i, to: to - payload.bars[0].i });
  }

  function setDeciding(enabled) {
    el('ew-decide-box').style.display = enabled ? 'block' : 'none';
    el('ew-next').style.display = enabled ? 'none' : 'inline-block';
  }

  async function loadNext() {
    el('ew-status').textContent = 'Cargando candidato…';
    el('ew-outcome').textContent = '';
    try {
      current = await api('/api/elliott/review/next');
      const p = current.progress;
      el('ew-status').textContent =
        `${current.side.toUpperCase()} · ${current.timeframe} · RR plan ${current.plan.planned_rr.toFixed(1)} · revisados ${p.reviewed}/${p.total}`;
      el('ew-note').value = '';
      draw(current);
      setDeciding(true);
    } catch (e) {
      el('ew-status').textContent = e.message;
    }
  }

  async function decide(decision) {
    if (!current) return;
    const body = {
      candidate_id: current.id,
      decision,
      count_label: el('ew-count').value || null,
      confidence: Number(el('ew-confidence').value) || null,
      note: el('ew-note').value || null,
    };
    try {
      const revealed = await api('/api/elliott/review/decide', { method: 'POST', body: JSON.stringify(body) });
      const o = revealed.outcome;
      const wave3 = o.wave3 === null ? 'sin definir' : o.wave3 ? 'SÍ' : 'NO';
      el('ew-outcome').textContent =
        `${o.symbol} ${o.date.slice(0, 16).replace('T', ' ')} UTC · resultado ${fmtR(o.net_r)} (${o.exit_reason}) · ` +
        `MFE ${fmtR(o.mfe_r)} · onda 3 llegó (superó 1 antes de perder 0): ${wave3} · ` +
        `tu decisión: ${decision === 'take' ? 'TOMO' : 'NO TOMO'}`;
      current = revealed;
      draw(revealed);
      setDeciding(false);
      loadStats();
    } catch (e) {
      el('ew-outcome').textContent = e.message;
    }
  }

  function row(label, m) {
    return `<tr><td>${label}</td><td>${m.trades}</td><td>${fmtPct(m.win_rate)}</td>` +
      `<td>${m.win_rate_ci95[0] === null ? '—' : `${m.win_rate_ci95[0]}–${m.win_rate_ci95[1]}%`}</td>` +
      `<td>${fmtR(m.expectancy_r)}</td><td>${fmtPct(m.wave3_rate)}</td></tr>`;
  }

  async function loadStats() {
    try {
      const s = await api('/api/elliott/review/stats');
      const edge = s.edge_expectancy_r;
      const small = s.taken.trades < 30 ? ' <span style="color:var(--muted)">(menos de 30 tomados: no concluir)</span>' : '';
      el('ew-stats').innerHTML =
        `<table class="mtf-table"><thead><tr><th></th><th>n</th><th>WR</th><th>IC95</th><th>E</th><th>onda 3</th></tr></thead><tbody>` +
        row('Tomados', s.taken) + row('No tomados', s.skipped) + row('Revisados (base)', s.reviewed) +
        row('Todos los candidatos', s.all_candidates) +
        ['1', '2', '3'].map(c => row(`Tomados conf ${c}`, s.taken_by_confidence[c])).join('') +
        `</tbody></table><p class="flow-status" style="margin-top:8px">Tu edge (E tomados − E revisados): <b>${fmtR(edge)}</b>${small}</p>`;
    } catch (e) {
      el('ew-stats').textContent = e.message;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!el('view-elliott')) return;
    el('ew-start').addEventListener('click', () => { loadNext(); loadStats(); });
    el('ew-next').addEventListener('click', loadNext);
    el('ew-take').addEventListener('click', () => decide('take'));
    el('ew-skip').addEventListener('click', () => decide('skip'));
    el('ew-show-marks').addEventListener('change', () => current && draw(current));
    document.querySelector('.app-tab[data-view="elliott"]').addEventListener('click', loadStats);
  });
})();
