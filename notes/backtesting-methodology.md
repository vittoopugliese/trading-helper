# Backtesting reproducible — Binance Futures + volumen/order flow

## Qué mide este motor

El dataset canónico es la vela 1m de Binance USD-M Futures. Conserva OHLC, volumen,
número de trades y `taker_buy_base`; de ahí se calcula:

- `delta = 2 * taker_buy_base - volume`
- `buy_ratio = taker_buy_base / volume`
- CVD acumulado, volume/MA20, ATR y niveles previos causales

Las velas 5m/15m/1h se reconstruyen en UTC. Un bucket con un minuto faltante o un
borde parcial se descarta; no se inventan velas.

## Reglas contra hindsight

- La señal usa sólo velas cerradas hasta `t` y entra en `open[t+1]`.
- La media de volumen excluye la vela evaluada.
- Soporte/resistencia es el extremo de las 30 velas anteriores, nunca un pivot futuro.
- El filtro BTC usa el último dato BTC disponible hacia atrás.
- Sólo hay una posición abierta por estrategia.
- Si una vela toca SL y TP, el resultado por defecto es SL (`stop_first`).
- Se descuentan 5 bps de fee y 2 bps de slippage por lado.
- El ranking usa primero expectancy del 30% cronológico final (out-of-sample).

## Setups implementados

| ID del motor | Traducción mecánica |
|---|---|
| `volume_delta_flip` | Pico de volumen y cambio de signo del delta confirmado por precio. |
| `sweep_reclaim` | Barrido del nivel previo con volumen/delta agresor y confirmación opuesta en la vela siguiente. |
| `sfp_strict_proxy` | Mecha cruza el nivel, cierre vuelve dentro, volumen alto y delta atrapado. Sin OI/footprint. |
| `absorption_reversal` | Proxy de absorción en borde: wick/rechazo pese a delta agresor, luego flip. |
| `delta_drain` | Tres deltas direccionales que pierden fuerza en el nivel y flip contrario. |
| `cvd_divergence` | Convención de tus notas: precio HL+CVD LL para long; precio LH+CVD HH para short. |
| `balance_rotation` | Delta flip responsive dentro del 20% exterior del rango previo. |
| `balance_breakout` | Cierre fuera del rango previo con volumen alto y delta alineado. |
| `balance_fakeout` | Perfora el rango con volumen bajo y cierra nuevamente dentro. |
| `order_block_retest_proxy` | Última vela opuesta antes de BOS/impulso ≥1.5 ATR; primer retest confirmado. |
| `a_plus_composite` | Sweep/absorción + tendencia EMA20/EMA50; el filtro BTC se prueba aparte. |

`btc_filter=True` no crea una entrada: exige EMA20>EMA50 de BTC para longs y la
condición inversa para shorts.

## Lo que no se debe llamar “backtesteado” todavía

- Elliott y POIs dibujados a mano: el YAML actual no es un historial point-in-time.
- POC dentro de la mecha, stacked imbalances, finished/unfinished auction: necesitan
  aggTrades/footprint por precio y una definición adicional.
- Book walls/DOM: el snapshot live no permite reconstruir el libro histórico.
- Absorción institucional: una vela sólo permite un proxy de “agresión sin avance”.
- Trapped traders con OI: requiere importar `daily/metrics` y sincronizarlo a 5m.

Por eso los nombres que dependen de información ausente llevan `_proxy`. Los trades
exportados incluyen timestamps UTC para revisar visualmente la señal en ATAS.

## Comandos

```powershell
# Descarga completa, reanudable, con checksums y reparación REST de cola/huecos
python main.py history-download --symbols HYPEUSDT,BTCUSDT --interval 1m

# Cobertura local sin red
python main.py history-status --symbols HYPEUSDT,BTCUSDT --interval 1m

# Grilla completa (11 setups, 1m/5m/15m, RR 2/3, volumen 1.3/1.5/2.0)
python main.py backtest-suite

# Corrida más rápida sobre el período común HYPE/BTC
python main.py backtest-suite --symbols HYPEUSDT --start 2025-05-30 --timeframes 5m,15m
```

Cada corrida genera `summary.json`, `ranking.csv` y `trades.csv` bajo
`reports/backtests/<UTC>`. `trades.csv` es el puente de auditoría con ATAS; no se
mezcla con el journal real `sessions.db`.

## Cómo leer el ranking

No elegir por WR solo. Exigir al menos 30 trades en test y revisar juntos:

- expectancy y profit factor positivos en train y test;
- intervalo de confianza del WR;
- drawdown y racha máxima;
- consistencia long/short, timeframe y umbral de volumen;
- MAE/MFE para detectar stops o targets mal calibrados.

Un target 2R necesita más de 33,3% antes de costos; 3R, más de 25%. Fees, slippage,
timeouts y salidas parciales cambian esos umbrales efectivos.
