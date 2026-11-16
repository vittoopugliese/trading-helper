# Datos requeridos — cada regla de las notas, traducida a un dato medible

## Que es
El diccionario entre lo que dicen las notas ("barra gigante", "el delta se pone verde",
"liquidaron a los longs") y el dato exacto que lo mide: de donde sale, con que umbral y
donde esta en el codigo. Si una regla no tiene fila aca, no se mide: es opinion.

Umbrales fijados el 2026-10-12, ANTES de mirar resultados (config.yaml -> `atas`,
`liquidations`, `signal_ledger`). No se ajustan mirando el journal o el ledger: eso es
volver al "mejor de mil intentos" de la review del backtest.

## Fuentes

| Fuente | Que da | Codigo | Disponible cuando |
|---|---|---|---|
| ATAS · indicador `Trading Helper Bridge` | footprint por nivel (bid/ask), delta, max/min delta, OI por vela, POC, VAH/VAL, big trades (cumulative trades >= 250 contratos), DOM ±1% | `atas-bridge/`, `agent/atas_feed.py` | ATAS abierto con el indicador en el chart y el cockpit corriendo |
| Bybit `allLiquidation` + Binance `forceOrder` | liquidaciones en vivo por lado | `agent/liquidations.py` | cockpit corriendo; z-score con >= 6 h de historial |
| Binance Futures REST | velas, aggTrades, OI 1h, funding, long/short ratio | `agent/market_data.py`, `agent/metrics.py` | siempre |
| Contexto | POI, invalidacion, targets, onda | `context/SYMBOL.yaml` | lo cargas vos; vence si el precio se aleja > 8% |

## Gatillo de 4 señales (gatillo-long/short-visual-atas.md)

| # | Regla en la nota | Dato | Umbral | Fuente |
|---|---|---|---|---|
| 1 | "Toca mi linea" | `price_in_poi` | precio dentro del POI ±0.1% (cerca = ±0.3%) | contexto + precio |
| 2 | "Pincha y vuelve" (absorcion) | `absorcion_en_minimo` / `absorcion_en_maximo` | >= 35% del volumen de la vela en el 25% extremo, delta de esa zona en contra, cierre en la mitad opuesta | ATAS footprint |
| 3 | "Barra gigante" | `volume_x` / `volume_spike` | >= 1.5x la media de las 20 velas cerradas previas | ATAS (o velas Binance sin ATAS) |
| 4 | "Delta rojo -> verde" | `delta_reversal_intra_vela` | min delta <= -15% del volumen y la vela cierra con delta > 0 (espejo short) | ATAS (max/min delta) |
| 4 | idem, entre velas | `delta_flip` | vela previa delta <= -10% de su volumen, actual >= +10% | ATAS |
| — | "Delta leading en contra" (frenar) | `imbalance_apilado_vendedor` / `_comprador` | 3+ niveles seguidos con imbalance diagonal >= 3x del lado contrario | ATAS footprint |

Regla del motor: con ATAS vivo, un A+ en POI necesita la señal 2 o la 4. Solo señales en
contra -> baja a B. Sin ATAS el grade no cambia, pero la alerta dice "sin ATAS".

## Patrones de entrada (entry-patterns.md)

| Patron | Dato que lo confirma | Dato que lo invalida |
|---|---|---|
| Absorcion / traders atrapados | `absorcion_en_minimo` + `poc_pos` <= 0.33 (POC en la mecha inferior) | delta de la zona sigue negativo y cierra abajo |
| Drenaje de delta | `cvd_divergence = bearish` (agotamiento: nuevo maximo con menos delta neto en las ultimas 5 velas que en las 5 previas) | delta neto sube con el precio |
| Toma de liquidez (sweep) | mecha fuera del nivel + `volume_x >= 1.5` + (si hay historial) `liq_flush` del lado barrido | sweep con `volume_x < 1.5` = liquidez falsa (red flag de la nota) |
| Retest de OB | volumen de la vela del OB (ATAS) + absorcion en el borde | imbalance apilado en contra atravesando el OB |
| Finished / unfinished auction | `unfinished_low` / `unfinished_high`: bid y ask > 0 en el extremo de la vela | — (es iman, no gatillo) |

## Divergencias CVD (smart-money/poi-clusters-liquidity.md)

Se calculan sobre el delta neto de las ultimas 5 velas cerradas de ATAS vs las 5 previas.
`cvd_divergence_type` dice cual de las dos lecturas disparo:

- `agotamiento`: precio hace minimo mas bajo y el delta neto es menos negativo -> bullish
  (espejo bearish en maximos).
- `absorcion` (la definicion de la nota): precio hace minimo mas alto mientras el delta neto
  es mas negativo (vendedores pegan mas fuerte y no logran bajar) -> bullish. Espejo bearish.

## Confirmacion bullish / bearish y SFP

| Regla | Dato | Estado |
|---|---|---|
| +Volumen +OI +Delta | `volume_x`, `oi_change` por vela (ATAS) u `oi_change_1h` (Binance), `delta` | medido |
| SFP: OI disminuye | `oi_change` < 0 en la vela del sweep (ATAS) | medido si el feed de ATAS trae OI |
| SFP: traders atrapados | `absorcion_en_*` + `liq_flush` del lado atrapado | medido (liq requiere historial) |
| Funding no extremo | abs(funding) <= 0.05% | medido |
| Long/short ratio | `long_short_ratio` Binance 1h | se muestra, no puntua |

## Liquidaciones (dato nuevo)

| Dato | Definicion |
|---|---|
| `long_usd_15m` / `short_usd_15m` | USD liquidado por lado en 15 min (Bybit completo + Binance muestreado) |
| `long_z_15m` | z-score de esos 15 min contra los bloques de 15 min de las 24 h previas |
| `long_spike` / `liq_flush` | z >= 2.5 y > 0 USD. Para un LONG interesa el flush de LONGS (venta forzada ya ejecutada) |

Estado: HIPOTESIS H2 (ver `notes/research/confiabilidad-win-rate.md`). Se muestra y se
registra, no puntua en el score hasta que el ledger forward diga que suma.

## Lo que todavia NO se mide
- Conteo Elliott "claro / no ambiguo": lo decide el trader al escribir el contexto.
- Calidad del OB (swing, 50%, OTE): parcial; el motor no detecta OBs por si mismo en vivo.
- Mapa de liquidez estimado (heatmaps): son estimaciones de terceros, no datos; no se usan.
