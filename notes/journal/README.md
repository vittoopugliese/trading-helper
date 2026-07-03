# JOURNAL DE TRADES — el corazon del sistema (Fase 3-4)

## Para que sirve
Coleccionar TODOS los trades (ganados y perdidos) para salir del trading
discrecional y construir estadisticas. Sin journal no hay backtesting ni confianza,
y la psicologia toma el control (ver `setup-rules/a-plus-checklist.md` Fase 3-4).

Objetivo: llegar a un sample de >=100 trades de UN solo sistema y revisarlo mensual.

## Como uso esta carpeta
1. Cada trade = un archivo: `AAAA-MM-DD_PAR_direccion_patron.md`.
   Ej: `2026-06-25_HYPE_long_sweep-reclaim.md`.
2. Copio `_template-trade.md` y lo relleno. Si no entre por miedo, IGUAL lo registro
   (los "no-trades" por psicologia son los mas valiosos para corregir — ver lecciones).
3. Anoto la EJECUCION y la PSICOLOGIA, no solo el analisis. El analisis suele estar
   bien; lo que falla es la ejecucion.
4. Mensual: leer todo, sacar metricas (win rate, R promedio, errores repetidos).

## Que registrar SIEMPRE
- Setup y patron (absorcion, sweep+reclaim, drenaje delta, etc.).
- Order flow exacto en la entrada (delta, buy_ratio, volumen, OI).
- Niveles: entry, SL estructural, TPs, R:R planeado.
- Resultado en R (no en $): +2R, -1R, etc. El R normaliza el tamaño.
- Estado emocional antes/durante/despues.
- Que hice bien y que error cometi (cruzar con `errores-comunes.md`).

## Metricas a calcular (cuando haya >=20 trades)
- Win rate (%), R promedio por trade, expectativa = (winrate*avgWin) - (lossrate*avgLoss).
- % de trades donde respete el SL estructural (deberia tender a 100%).
- % de setups validos que NO tome por miedo (deberia tender a 0%).
- Mejor patron por win rate (en que soy bueno) y peor (que evitar).

## Indice de trades
| Fecha | Par | Dir | Patron | Resultado | Leccion clave |
|---|---|---|---|---|---|
| 2026-06-20 a 22 | HYPE | varios | (resumen lecciones) | mixto | Stop corto / miedo = no ejecutar |
| 2026-06-24 | HYPE | long | absorcion en POI | no tomado (estudio) | Esperar delta flip confirmado |
| 2026-06-25 | HYPE | long | sweep+reclaim | no tomado (estudio) | El A+ visual de referencia |
