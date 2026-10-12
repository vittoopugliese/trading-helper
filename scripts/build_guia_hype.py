"""Genera los gráficos de velas (SVG inline) de la ULTRA GUÍA de scalping HYPE.

Lee las velas REALES de data/cache.db (1m, cobertura completa), las resamplea al
timeframe del ejemplo (15m / 5m) y renderiza un candlestick SVG anotado
(entry / SL / TP / banda de riesgo-recompensa / spike de volumen / delta por vela).
Luego reemplaza los placeholders {{CHART:id}} de web/static/guia-hype.template.html
y escribe el archivo final web/static/guia-hype.html (autocontenido).

Reejecutable:  python scripts/build_guia_hype.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Permitir "from agent import ..." corriendo desde la raíz del repo
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import market_data  # noqa: E402

SYMBOL = "HYPEUSDT"
TEMPLATE = ROOT / "web" / "static" / "guia-hype.template.html"
OUTPUT = ROOT / "web" / "static" / "guia-hype.html"

# Paleta (coherente con reference-elliott.html)
C_UP = "#4bb37c"
C_UP_STROKE = "#63d194"
C_DOWN = "#cf3a53"
C_DOWN_STROKE = "#e85a72"
C_ENTRY = "#f2e6e1"
C_STOP = "#e8213f"
C_TARGET = "#52d273"
C_SPIKE = "#d4a437"
C_GRID = "#3a1518"
C_TEXT = "#9c7f7d"
C_TEXT_HI = "#f2e6e1"


# ---------------------------------------------------------------------------
# Ejemplos reales (de reports/backtests/hype_directional/trades.csv)
# ---------------------------------------------------------------------------
EXAMPLES: dict[str, dict] = {
    # Order Block retest — run 39: 15m · 3R · vol 1.5x · +BTC · long
    "ob_win": dict(rule="15min", direction="long",
                   entry_ts="2025-06-29T08:30", exit_ts="2025-06-30T02:15",
                   entry=38.325, stop=38.04289, target=39.17132, exit=39.17132,
                   r="+2.81R", label="Order Block retest · WIN"),
    "ob_loss": dict(rule="15min", direction="long",
                    entry_ts="2025-06-03T17:15", exit_ts="2025-06-03T20:30",
                    entry=37.125, stop=36.50111, target=38.99668, exit=36.50111,
                    r="-1.08R", label="Order Block retest · LOSS"),
    # CVD divergence — run 45: 15m · 2R · +BTC · long
    "cvd_win": dict(rule="15min", direction="long",
                    entry_ts="2025-06-06T09:15", exit_ts="2025-06-06T14:00",
                    entry=34.566, stop=34.13282, target=35.43236, exit=35.43236,
                    r="+1.89R", label="CVD divergence · WIN"),
    "cvd_loss": dict(rule="15min", direction="long",
                     entry_ts="2025-06-01T06:45", exit_ts="2025-06-01T07:15",
                     entry=32.780, stop=32.31436, target=33.71129, exit=32.31436,
                     r="-1.10R", label="CVD divergence · LOSS"),
    # Sweep + reclaim — run 91: 15m · 3R · vol 2.0x · long
    "sweep_win": dict(rule="15min", direction="long",
                      entry_ts="2025-06-22T14:45", exit_ts="2025-06-23T08:15",
                      entry=32.688, stop=31.54016, target=36.13153, exit=36.13153,
                      r="+2.96R", label="Sweep + reclaim · WIN"),
    "sweep_loss": dict(rule="15min", direction="long",
                       entry_ts="2025-06-05T15:00", exit_ts="2025-06-05T17:00",
                       entry=34.818, stop=34.20874, target=36.64577, exit=34.20874,
                       r="-1.08R", label="Sweep + reclaim · LOSS"),
    # Absorption reversal — run 23: 15m · long
    "abs_win": dict(rule="15min", direction="long",
                    entry_ts="2026-01-13T11:00", exit_ts="2026-01-13T19:30",
                    entry=24.258, stop=24.02779, target=24.94862, exit=24.94862,
                    r="+2.85R", label="Absorción / trapped · WIN"),
    "abs_loss": dict(rule="15min", direction="long",
                     entry_ts="2025-06-16T20:15", exit_ts="2025-06-16T21:30",
                     entry=44.536, stop=44.00685, target=46.12345, exit=44.00685,
                     r="-1.12R", label="Absorción / trapped · LOSS"),
    # Casos "estrella" documentados a mano (discrecional, 5m, con narrativa de delta)
    "sweep_hero": dict(rule="5min", direction="long",
                       entry_ts="2026-06-25T14:00", exit_ts="2026-06-25T15:35",
                       start="2026-06-25T12:40", end="2026-06-25T15:40",
                       entry=59.20, stop=58.45, target=60.75, exit=60.75,
                       swept=58.50,
                       r="A+ real", label="Caso real · Sweep+Reclaim 25-jun-2026"),
    "abs_hero": dict(rule="5min", direction="long",
                     entry_ts="2026-06-24T16:45", exit_ts="2026-06-24T17:30",
                     start="2026-06-24T15:55", end="2026-06-24T17:35",
                     entry=59.55, stop=59.20, target=60.20, exit=60.20,
                     swept=59.25,
                     r="real", label="Caso real · Absorción 24-jun-2026"),
}

RULE_MIN = {"5min": 5, "15min": 15}


def _ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def load_resampled(cfg: dict) -> pd.DataFrame:
    rule = cfg["rule"]
    step = RULE_MIN[rule]
    entry_ms = _ms(cfg["entry_ts"])
    exit_ms = _ms(cfg["exit_ts"])
    start_ms = _ms(cfg["start"]) if cfg.get("start") else entry_ms - 18 * step * 60_000
    end_ms = _ms(cfg["end"]) if cfg.get("end") else exit_ms + 6 * step * 60_000

    raw = market_data.load_cached_klines_df(SYMBOL, "1m", start_ms, end_ms)
    if raw.empty:
        return raw

    raw = raw.set_index("open_time")
    agg = (
        raw.resample(rule, label="left", closed="left")
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
             close=("close", "last"), volume=("volume", "sum"),
             taker_buy_base=("taker_buy_base", "sum"))
        .dropna(subset=["open"])
    )
    agg["delta"] = 2 * agg["taker_buy_base"] - agg["volume"]
    agg["vol_ma"] = agg["volume"].rolling(20, min_periods=5).mean()
    return agg.reset_index()


def _fmt(p: float) -> str:
    return f"{p:,.2f}"


def render_chart(df: pd.DataFrame, cfg: dict) -> str:
    if df.empty or len(df) < 3:
        return ('<svg viewBox="0 0 760 60" xmlns="http://www.w3.org/2000/svg">'
                f'<text x="16" y="34" fill="{C_STOP}" font-family="IBM Plex Mono" '
                f'font-size="12">Sin datos para {cfg["label"]}</text></svg>')

    W, H = 760, 320
    ml, mr, mt, mb = 8, 58, 12, 24
    price_h = 208
    gap = 14
    vol_h = H - mt - mb - price_h - gap
    vol_top = mt + price_h + gap
    plot_w = W - ml - mr

    entry, stop, target = cfg["entry"], cfg["stop"], cfg["target"]
    swept = cfg.get("swept")

    lows = df["low"].min()
    highs = df["high"].max()
    pmin = min(lows, stop, entry, target, swept if swept else lows)
    pmax = max(highs, stop, entry, target, swept if swept else highs)
    pad = (pmax - pmin) * 0.06 or 0.5
    pmin -= pad
    pmax += pad
    span = pmax - pmin

    n = len(df)
    slot = plot_w / n
    bw = max(2.0, slot * 0.62)
    vmax = df["volume"].max() or 1.0

    def x(i: float) -> float:
        return ml + i * slot + slot / 2

    def y(p: float) -> float:
        return mt + (pmax - p) / span * price_h

    entry_ms = _ms(cfg["entry_ts"])
    entry_dt = pd.to_datetime(entry_ms, unit="ms", utc=True)
    step = RULE_MIN[cfg["rule"]]
    ratio = 1.5

    parts: list[str] = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
                        f'font-family="IBM Plex Mono" preserveAspectRatio="xMidYMid meet">']
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#150708"/>')

    # Bandas riesgo / recompensa
    y_e, y_s, y_t = y(entry), y(stop), y(target)
    parts.append(f'<rect x="{ml}" y="{min(y_e, y_s):.1f}" width="{plot_w}" '
                 f'height="{abs(y_s - y_e):.1f}" fill="rgba(232,33,63,0.07)"/>')
    parts.append(f'<rect x="{ml}" y="{min(y_e, y_t):.1f}" width="{plot_w}" '
                 f'height="{abs(y_t - y_e):.1f}" fill="rgba(82,210,115,0.06)"/>')

    # Velas + volumen
    for i, row in df.iterrows():
        cx = x(i)
        up = row["close"] >= row["open"]
        col = C_UP if up else C_DOWN
        stroke = C_UP_STROKE if up else C_DOWN_STROKE
        parts.append(f'<line x1="{cx:.1f}" y1="{y(row["high"]):.1f}" x2="{cx:.1f}" '
                     f'y2="{y(row["low"]):.1f}" stroke="{stroke}" stroke-width="1"/>')
        yo, yc = y(row["open"]), y(row["close"])
        bh = max(1.0, abs(yc - yo))
        parts.append(f'<rect x="{cx - bw / 2:.1f}" y="{min(yo, yc):.1f}" width="{bw:.1f}" '
                     f'height="{bh:.1f}" fill="{col}" stroke="{stroke}" stroke-width="0.6"/>')
        # volumen
        spike = row["vol_ma"] and row["volume"] >= row["vol_ma"] * ratio
        vh = row["volume"] / vmax * vol_h
        vcol = C_SPIKE if spike else (C_UP if up else C_DOWN)
        vop = "0.95" if spike else "0.5"
        parts.append(f'<rect x="{cx - bw / 2:.1f}" y="{vol_top + vol_h - vh:.1f}" '
                     f'width="{bw:.1f}" height="{vh:.1f}" fill="{vcol}" opacity="{vop}"/>')

    # Nivel barrido (sweep/absorción)
    if swept:
        ys = y(swept)
        parts.append(f'<line x1="{ml}" y1="{ys:.1f}" x2="{ml + plot_w}" y2="{ys:.1f}" '
                     f'stroke="#8ec5e8" stroke-width="1" stroke-dasharray="2 3" opacity="0.8"/>')
        parts.append(f'<text x="{ml + 4}" y="{ys - 4:.1f}" fill="#8ec5e8" font-size="9">'
                     f'nivel barrido {_fmt(swept)}</text>')

    # Líneas entry / SL / TP + labels a la derecha
    for val, color, tag in ((entry, C_ENTRY, "ENTRY"), (stop, C_STOP, "SL"), (target, C_TARGET, "TP")):
        yy = y(val)
        parts.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{ml + plot_w}" y2="{yy:.1f}" '
                     f'stroke="{color}" stroke-width="1" stroke-dasharray="4 3" opacity="0.85"/>')
        parts.append(f'<rect x="{ml + plot_w + 1}" y="{yy - 7:.1f}" width="{mr - 2}" height="14" '
                     f'fill="#1f0e0f" stroke="{color}" stroke-width="0.6"/>')
        parts.append(f'<text x="{ml + plot_w + 5}" y="{yy + 3.5:.1f}" fill="{color}" '
                     f'font-size="9" font-weight="600">{tag} {_fmt(val)}</text>')

    # Marca de la vela de entrada
    match = df.index[df["open_time"] == entry_dt]
    if len(match):
        ei = int(match[0])
        cxe = x(ei)
        yy = y(entry)
        parts.append(f'<polygon points="{cxe - 5:.1f},{yy + 13:.1f} {cxe + 5:.1f},{yy + 13:.1f} '
                     f'{cxe:.1f},{yy + 4:.1f}" fill="{C_ENTRY}"/>')
        # resalta la vela de señal (la previa)
        if ei > 0:
            cxs = x(ei - 1)
            parts.append(f'<rect x="{cxs - bw / 2 - 2:.1f}" y="{mt}" width="{bw + 4:.1f}" '
                         f'height="{price_h}" fill="none" stroke="{C_SPIKE}" '
                         f'stroke-width="0.8" stroke-dasharray="2 2" opacity="0.55"/>')

    # Eje temporal (primera / última vela) + eje volumen label
    t0 = df["open_time"].iloc[0].strftime("%d-%b %H:%M")
    t1 = df["open_time"].iloc[-1].strftime("%d-%b %H:%M")
    parts.append(f'<text x="{ml}" y="{H - 8}" fill="{C_TEXT}" font-size="9">{t0}Z · {cfg["rule"]}</text>')
    parts.append(f'<text x="{ml + plot_w}" y="{H - 8}" fill="{C_TEXT}" font-size="9" '
                 f'text-anchor="end">{t1}Z</text>')
    parts.append(f'<text x="{ml}" y="{vol_top - 3:.1f}" fill="{C_TEXT}" font-size="8">'
                 f'VOLUMEN (barra dorada = spike ≥ {ratio}× media)</text>')

    parts.append("</svg>")
    return "".join(parts)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    if not TEMPLATE.exists():
        print(f"ERROR: falta el template {TEMPLATE}")
        return 1
    html = TEMPLATE.read_text(encoding="utf-8")

    ok = 0
    for cid, cfg in EXAMPLES.items():
        marker = f"{{{{CHART:{cid}}}}}"
        if marker not in html:
            print(f"  aviso: placeholder {marker} no está en el template")
            continue
        df = load_resampled(cfg)
        svg = render_chart(df, cfg)
        html = html.replace(marker, svg)
        print(f"  ✓ {cid:12s} {len(df):3d} velas  ({cfg['label']})")
        ok += 1

    leftovers = [m for m in EXAMPLES if f"{{{{CHART:{m}}}}}" in html]
    if leftovers:
        print(f"ERROR: quedaron placeholders sin reemplazar: {leftovers}")
        return 2

    OUTPUT.write_text(html, encoding="utf-8")
    print(f"\nGenerado {OUTPUT}  ({ok} charts, {len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
