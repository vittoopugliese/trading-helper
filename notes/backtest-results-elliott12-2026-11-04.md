# Resultados Elliott 1-2 mecánico — BTCUSDT — 2026-11-04

Reglas: `notes/setup-rules/elliott-1-2-reglas.md` (congeladas antes de correr).
Corrida: `reports/backtests/elliott12_20261104T223745Z` (BTC 1m Binance 2019-09 → 2026-10).
Comando: `python main.py elliott-backtest --timeframes 15m,1h --zigzag 2.0,1.5,3.0`.

## Primario: BTC 15m, zigzag 2 ATR

- 4.374 candidatos geométricos (long + short).
- Lectura: cuando la onda 2 llegó a la zona 0.5-0.786, la onda 3 superó el fin de la 1
  antes de perder el origen el **61%** de las veces (n=1.558).
- Variante A (límite 0.618, SL bajo origen de 1, TP 1.618): n=2.006, WR 34.9%,
  **E -0.28R**; test n=648, E -0.30R.
- Variante B (confirmación, SL bajo mínimo de 2, TP 1.618): n=1.797, WR 22.5%,
  **E -0.39R**; test n=565, E -0.32R.

## Por qué pierde aunque la onda 3 llegue el 61% de las veces

- El TP 1.618 casi nunca llega dentro de la ventana (5-6% de los trades). La mayoría
  sale por tiempo con ganancia chica o por stop.
- MFE: 44-47% de los trades llega a +1R, 20-28% a +2R. La onda 3 empieza, pero corta.
- Costos: con stops de ~0.2-0.5% en 15m, 14 bps ida y vuelta pesan 0.16-0.27R por trade.
- En B, el stop bajo el mínimo de la 2 es muy ajustado: la 2 se extiende y saca el stop.

## Robustez

| TF | zigzag | E A | E B | E B test |
|---|---|---|---|---|
| 15m | 1.5 | -0.29 | -0.39 | -0.32 |
| 15m | 3.0 | -0.18 | -0.31 | -0.30 |
| 1h | 1.5 | -0.07 | -0.24 | +0.04 |
| 1h | 2.0 | -0.12 | -0.19 | +0.00 |
| 1h | 3.0 | -0.10 | -0.21 | -0.30 |

Negativo en todas las variantes. En 1h está cerca de cero (los costos pesan menos).

## Hipótesis (15m, todos los trades)

- H3 tendencia 4h: mejora en las dos variantes. A: -0.20R con / -0.39R sin. B: -0.30R / -0.51R.
  Va en la dirección de las notas, pero sigue negativo.
- V1 volumen onda 2 < onda 1: leve mejora en A (-0.25 / -0.34), nada en B.
- V2 absorción (proxy con velas, sin footprint): no ayuda; en B empeora.

## Lectura

El 1-2 geométrico solo no es un edge en BTC 15m. Si el edge existe, está en la
discriminación del trader (cuáles 1-2 son reales) y/o en la gestión. La revisión ciega
(pestaña BACKTEST EW) mide justamente eso contra esta base.

Cambiar TP, ventana de tiempo o stop ahora sería optimizar sobre datos ya vistos. Si se
prueba gestión (TP1 parcial + BE, como dice `gestion-trade-activo`), validarla en datos no
mirados: HYPE 15m o BTC antes/después de un corte fijado de antemano.
