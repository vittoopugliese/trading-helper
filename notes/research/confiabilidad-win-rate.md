# Confiabilidad de los setups y win rate — investigacion y plan (2026-10-12)

## Pregunta
Como hacer que las alertas A+ sean mas confiables y suban el win rate, sin repetir el
error que mostro la review del backtest (elegir la mejor de ~1000 variantes).

## Lo que dice la evidencia (y lo que no)

1. **Win rate solo engaña.** Acercar el TP o alejar el SL sube el WR y baja la ganancia por
   trade. El objetivo es WR **y** expectancy en R despues de costos, medidos juntos. El
   backtest ya lo mostro: "Balance fakeout" tenia 36.6% WR y -0.21R.

2. **Seleccion sobre muchas pruebas = sobreajuste.** Bailey y Lopez de Prado proponen el
   Deflated Sharpe Ratio (compara contra el maximo esperado de N pruebas sin edge) y la
   Probability of Backtest Overfitting (que tan seguido el ganador in-sample queda en la
   mitad de abajo out-of-sample). Es exactamente el control que hizo la review. Ademas,
   las correcciones muy duras pueden rechazar edges chicos reales, por eso se combinan con
   confirmacion forward.

3. **Order flow: explica bien, predice poco a horizontes cortos.** El desbalance de flujo
   acompaña al cambio de precio del mismo periodo (BitMEX XBTUSD). En perpetuos de Binance,
   Kim y Hansen (2026) encuentran que el desbalance en minutos 1 y 5 casi no predice
   retornos; el de la apertura de cada cuarto de hora predice a 4-12 h, con reversion en
   la primera media hora. Para scalping: el footprint sirve como **confirmacion al cierre
   de la vela del gatillo**, no como prediccion sola. Asi lo usa el motor.

4. **Liquidaciones: evidencia de practicantes, no de papers.** Lo que se encontro son
   blogs de exchanges y vendors: cascadas = mecha + caida de OI + cambio de funding; un
   flush que se agota en una zona suele revertir. No hay estudio controlado que lo mida.
   Por eso entra como hipotesis registrada, no como regla.

5. **Meta-labeling (Lopez de Prado).** El setup primario decide la direccion; un segundo
   filtro aprende, con los resultados reales, en que contextos ese setup gana, y solo
   deja pasar esos. Esta pensado para subir la precision (WR) sin perder demasiadas
   señales. Los "+5 a +20% de WR" que circulan son reglas practicas, no resultados
   publicados. Requiere muestra grande y validacion con purga/embargo (las señales que
   se solapan en el tiempo filtran informacion entre train y test).

## Que se construyo para esto

- **Datos distintos** (lo que pedia la review): footprint real de ATAS (absorcion, delta
  intra-vela, imbalances apilados, big trades, DOM) y liquidaciones en vivo.
- **Signal ledger** (`agent/signal_ledger.py`): cada alerta se guarda con todas sus
  features y se etiqueta sola con triple barrera (SL primero si TP y SL caen en la misma
  vela de 1m, timeout 4 h, 7 bps por lado). Es la base del meta-labeling y es forward:
  ninguna señal se uso para elegir parametros.
- **Panel "Señales forward"** en el cockpit: WR con intervalo de Wilson 95% y expectancy,
  con y sin cada feature.
- **Umbrales fijados antes** en `config.yaml`. Ver `notes/setup-rules/datos-requeridos.md`.

## Hipotesis registradas (no agregar otras hasta cerrar estas)

| Id | Hipotesis | Feature | Se acepta si |
|---|---|---|---|
| H1 | Un A+/setup en POI con gatillo footprint ATAS rinde mas que sin el | `atas_confirmed` | expectancy "con" > "sin" y los IC 95% del WR no se pisan |
| H2 | Un long en POI despues de un flush de longs (z >= 2.5) rinde mas | `liq_flush` | idem |
| H3 | A favor de tendencia rinde mas que contra-tendencia | `trend_aligned` | idem |

Solo tres, a proposito: cada hipotesis extra es otra moneda tirada.

## Cuanta muestra hace falta (la parte incomoda)

Con WR ~40%, el IC 95% a 100 señales es de ±10 puntos. Para afirmar con 80% de potencia
que una feature sube el WR de 35% a 45% hacen falta **~375 señales por grupo**
(test de dos proporciones, alfa 0.05). Con 2-5 alertas por dia eso es meses.

- A 30 por grupo el panel muestra numeros, pero no se concluye nada (lo marca con `*`).
- A ~100 por grupo se puede mirar la direccion y la expectancy con bootstrap.
- Una feature pasa a pesar en el score solo cuando cumple su criterio con la muestra completa.

## Lo que sube la confiabilidad hoy, sin esperar muestra

1. Operar solo A+ en POI, a favor de tendencia HTF (es lo que ya dicen las notas y el
   backtest: los longs aislados fueron lo unico positivo en train y test).
2. Con ATAS conectado, exigir el gatillo de footprint (ya activo: baja A+ a B si falta).
3. Contexto Elliott al dia: con el POI viejo el motor da NO_TRADE (hoy HYPE esta 14% lejos
   del POI de `context/HYPEUSDT.yaml`).
4. Journal de cada trade real con `of_pattern` y `elliott_alineado`, para cruzarlo con el ledger.

## Siguiente paso de investigacion (cuando haya datos)
- Con ~2-3 meses de `data/atas.db` + `data/liquidations.db`, backtest de H1/H2 sobre esos
  datos grabados con particion temporal (train hasta una fecha, test despues, sin tocar).
- Con >= 200 señales resueltas, probar un meta-filtro simple (regresion logistica sobre
  las features del ledger) con validacion purgada, y compararlo contra "tomar todas".

## Fuentes
- Bailey & Lopez de Prado, The Deflated Sharpe Ratio: https://papers.ssrn.com/abstract=2460551
- Probability of Backtest Overfitting (resumen): https://www.luxalgo.com/library/concept/probability-of-backtest-overfitting.md
- Kim & Hansen, The Quarter-Hour Effect (2026): https://arxiv.org/pdf/2607.09426
- Silantyev, Order flow analysis of cryptocurrency markets: https://www.scribd.com/document/494134352/Order-Flow-Analysis
- Fundamentals of Perpetual Futures: https://arxiv.org/pdf/2212.06888
- Meta-Labeling: https://en.wikipedia.org/wiki/Meta-Labeling
- Meta-labeling (Jesse docs, regla practica de +5-20% WR): https://docs.jesse.trade/docs/research/ml/meta-labeling
- Liquidaciones (practicantes): https://www.luxalgo.com/library/concept/liquidation-clusters.md · https://blog.amberdata.io/leverage-liquidations-the-31b-deleveraging · https://www.blockworks.com/news/crypto-sell-off-open-interest-funding
- Bybit allLiquidation: https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation
