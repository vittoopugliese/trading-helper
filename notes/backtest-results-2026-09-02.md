# Resultados iniciales — HYPE/BTC Binance Futures — 2026-09-02

## Dataset y corridas

- HYPEUSDT 1m: 663.009 velas desde 2025-05-30 10:30 UTC hasta el freeze de la corrida.
- BTCUSDT 1m: histórico completo desde 2019-09-08; para comparación se usaron las
  663.010 velas solapadas con HYPE.
- BTC tiene un único minuto ausente en el origen Binance (2019-09-08 19:00 UTC); el
  endpoint consultado para ese minuto devuelve `[]`. No se rellenó artificialmente.
- Baseline: 132 configuraciones, 11 setups, 5m/15m, RR 2/3, volumen 1.5x, HYPE con/sin BTC.
- Sensibilidad: volumen 1.3x y 2.0x en las seis familias más prometedoras.
- Direccional: long y short simulados por separado para evitar que una posición del
  lado opuesto bloquee señales.

Todas las métricas descuentan 5 bps de fee + 2 bps de slippage por lado. Entrada en
la apertura siguiente; SL primero si TP/SL aparecen en la misma vela; máximo 96 barras.

## WR por estrategia HYPE — mejor baseline elegible de cada familia

El cuadro elige la configuración con al menos 30 trades de test. `E test` es más
importante que WR: incorpora tamaño real de wins/losses, costos y timeouts.

| Estrategia | Mejor config | n test | WR test | E test | E train | Lectura |
|---|---:|---:|---:|---:|---:|---|
| Order Block retest proxy | 15m, 3R, BTC | 70 | 35,71% | +0,222R | +0,012R | Única familia positiva en train/test combinando lados. |
| CVD divergence | 15m, 2R, BTC | 313 | 41,85% | +0,093R | -0,238R | Mejora reciente; no estable. |
| A+ composite | 5m, 3R, BTC | 30 | 33,33% | +0,015R | -0,269R | Muestra mínima y train negativo. |
| Absorption reversal | 15m, 3R, BTC | 34 | 35,29% | +0,012R | -0,281R | No validada. |
| Delta drain | 5m, 3R | 171 | 33,92% | -0,052R | -0,078R | Descartar en forma actual. |
| Balance breakout | 15m, 3R | 362 | 28,45% | -0,089R | -0,244R | Descartar en forma actual. |
| Sweep + reclaim | 15m, 3R, BTC | 46 | 32,61% | -0,092R | -0,273R | Mezclar longs/shorts destruye el edge long. |
| Volume + delta flip | 15m, 3R, BTC | 370 | 27,84% | -0,092R | -0,172R | Demasiado genérica. |
| SFP strict proxy | 15m, 3R, BTC | 103 | 29,13% | -0,163R | -0,571R | Falta OI/footprint; proxy no sirve aún. |
| Balance fakeout | 5m, 3R, BTC | 71 | 36,62% | -0,210R | -0,463R | WR engañoso por pérdidas/costos. |
| Balance rotation | 15m, 2R | 270 | 30,74% | -0,245R | -0,138R | Descartar en forma actual. |

En BTC, ninguna de las once reglas transportadas con estos mismos parámetros tuvo
expectancy positiva, ni en el período común ni al repetir el test sobre todo el
histórico 2019–2026. En el test cronológico final completo, la “menos mala” fue CVD
divergence 15m/3R: 1.971 trades, WR 26,43%, E -0,354R. No usar estos parámetros de
HYPE para BTC.

## Lo mejor para revisar primero en ATAS

| Prioridad | Setup aislado | n train/test | WR train/test | E train/test | PF test | IC95 WR test |
|---|---|---:|---:|---:|---:|---:|
| 1 | OB retest proxy LONG, 15m, 3R, vol 1.5x, BTC alineado | 77 / 38 | 31,17% / 42,11% | +0,068R / +0,482R | 1,74 | 27,85–57,81% |
| 2 | OB retest proxy LONG, 15m, 3R, sin BTC | 103 / 47 | 30,10% / 40,43% | +0,041R / +0,424R | 1,63 | 27,64–54,66% |
| 3 | Sweep+reclaim LONG, 15m, 3R, vol 2.0x | 75 / 34 | 30,67% / 41,18% | +0,020R / +0,201R | 1,28 | 26,37–57,78% |
| 4 | Sweep+reclaim LONG, 15m, 3R, vol 1.3x | 179 / 69 | 30,73% / 34,78% | +0,037R / +0,077R | 1,10 | 24,62–46,55% |
| 5 | Sweep+reclaim LONG, 15m, 3R, vol 1.5x | 146 / 58 | 30,82% / 34,48% | +0,033R / +0,047R | 1,06 | 23,56–47,33% |

OB retest tiene mayor rendimiento, pero es frágil al umbral: 1.3x/2.0x fueron
negativos en train aunque positivos en test. Sweep+reclaim long es menos rentable,
pero fue positivo en train/test con 1.3x, 1.5x y 2.0x.

## Régimen temporal

Ningún candidato es todavía “listo para operar”. En OB long + BTC, la expectancy por
trimestre fue aproximadamente -0,245R (2025Q2), +0,122R (Q3), -0,009R (Q4),
+0,025R (2026Q1), +0,456R (Q2) y +0,572R (Q3). La mayor parte del edge apareció en
2026. Sweep long también fue negativo en buena parte de 2025 y positivo en 2026.

Conclusión: candidatos para inspección/forward test, no parámetros de sizing. El
journal real debe confirmar si el régimen 2026 persiste y si Elliott/POI manual agrega
valor. El journal ahora puede medir exactamente ese A/B con `of_pattern` y
`elliott_alineado`.

## Artefactos locales

- `reports/backtests/full_hype_baseline/`: 132 configuraciones y 38,9 MB de trades.
- `reports/backtests/hype_sensitivity/`: sensibilidad de volumen.
- `reports/backtests/hype_directional/`: comparación long/short para auditoría ATAS.
- `reports/backtests/btc_full_history/`: control BTC desde 2019.

Ver las definiciones y límites en `notes/backtesting-methodology.md`.
