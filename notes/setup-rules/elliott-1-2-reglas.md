# Setup 1-2 (fin de onda 2 → onda 3) — reglas congeladas para backtest

Fecha de congelamiento: 2026-11-04. Esta nota consolida las reglas del setup 1-2 que
ya estaban en el repo y las cruza con el cheat sheet de ew-strategy.com. No reemplaza
ninguna nota: las fuentes siguen siendo las originales. Su objetivo es fijar, ANTES de
correr nada, la traducción mecánica que usa `agent/elliott_backtest.py`.

Si se cambia un parámetro de la sección 3 después de ver resultados, el número deja de
ser un backtest y pasa a ser una optimización. Cualquier cambio va con fecha nueva y
una corrida nueva sobre datos que no se hayan mirado.

## 1. Reglas de las fuentes

| Tema | Notas del repo | Cheat sheet ew-strategy | Usado |
|---|---|---|---|
| Regla dura onda 2 | Nunca retrocede más del 100% de la 1 (`elliott-basics`, `elliott-01`) | Igual | Invalidación = origen de la onda 1 |
| Retroceso onda 2 | Normal 0.5-0.618-0.66-0.786; mínimo 0.5, máximo 0.786; 0.886 = peligro, quizás no sea impulso; 0.382 raro (`fib-levels`) | 50 / 61.8 / 78.6, más común 61.8 | Zona válida 0.5-0.786 |
| Zona de entrada | Golden pocket 0.618-0.66; POI = zona, no tick (`fib-levels`, `a-plus-checklist`, `gestion-trade-activo`) | Confluencia de fibs | Límite en 0.618 o confirmación dentro de la zona |
| Onda 1 | Rompe estructura: en impulso hay 2 BOS, con cuerpo de vela; corrección suele tener 1 (`structure-corrections`) | Motive, a favor de la tendencia mayor | Onda 1 debe superar con cierre el swing previo |
| Target onda 3 | 1.618 / 2 / 2.618 / 3...; mínimo 1 a 1 (`fib-levels`) | 161.8 / 200 / 261.8, más común 161.8 | TP = fin de onda 2 + 1.618 × onda 1 |
| Stop | SL estructural donde se invalida el conteo, no se mueve en contra (`a-plus-checklist`, `gestion-trade-activo`) | Descartar conteo que viola una regla absoluta | A: bajo el origen de 1. B: bajo el mínimo de 2 |
| Tiempo onda 2 | Mínimo 0.382 del tiempo de 1; luego 0.618 y 1; máximo 4. No invalida (`fib-levels`) | No lo cubre | Vence a las 4 × duración de onda 1 |
| Volumen onda 2 | Volumen decrece hacia el final de la 2; absorción / secado de venta en el GP (`elliott-04`) | Onda 3 tiene el mayor volumen | Se registra, no filtra (hipótesis V1, V2) |
| Volumen 1-2 vs A-B | Si el volumen es ascendente, más probable 3 que C (`structure-corrections`) | — | Se registra (delta de onda 1) |
| Tendencia mayor | Siempre a favor de la tendencia HTF (`structure-corrections`, `a-plus-checklist`) | Contar de semanal a diario a 4h | Se registra (hipótesis H3) |
| Ambigüedad | 2+ conteos válidos: no operar (`elliott-basics`) | "When unsure, stand aside" | Lo decide el trader en la revisión ciega |
| Scorecard | Journal + signal ledger | Llevar scorecard; revisar método si el acierto baja de 50% | Revisión ciega mide acierto propio contra la base |

## 2. Lo que la máquina no puede ver

- Si la onda 1 tiene 5 subondas o es una diagonal / un 3 (A de un zigzag).
- Si la onda 2 es un 3 limpio o un flat en desarrollo.
- Conteo alternativo y grado de la onda.

Eso es justamente tu edge discrecional. La máquina propone todos los 1-2 geométricos;
vos decidís en ciego cuáles son 1-2 reales. La diferencia entre tu resultado y el de
todos los candidatos es lo que aporta tu lectura.

## 3. Traducción mecánica (congelada)

Datos: Binance USD-M BTCUSDT, velas 1m agregadas a 15m (primario). 1h solo como
robustez. Costos 5 bps fee + 2 bps slippage por lado. Si una vela toca SL y TP, cuenta SL.

Pivots: zigzag por ATR(14). Un máximo/mínimo se confirma cuando el precio se aleja
2.0 × ATR de él. El pivot se conoce recién en la vela que lo confirma (sin mirar el futuro).

Candidato long (short = espejo):
1. Pivots confirmados `H_prev` (máximo) → `L0` (mínimo) → `H1` (máximo).
2. Onda 1 = `H1 - L0` ≥ 5 × ATR en la vela de confirmación.
3. BOS: alguna vela entre `L0` y `H1` cierra por encima de `H_prev`.
4. Se conoce en la vela que confirma `H1`. Desde ahí se sigue el mínimo de la onda 2.
5. Se cancela si: el precio supera `H1` antes de entrar (onda 2 corta, se perdió); el
   mínimo de la onda 2 pasa 0.786 (fuera de la regla general); pasan 4 × duración de 1.

Variante A, "operar la zona": orden límite en 0.618. SL = `L0` - 0.1 ATR (invalidación
dura). TP = entrada + 1.618 × onda 1.

Variante B, "confirmación": con el mínimo de la onda 2 entre 0.5 y 0.786, la primera
vela que cierra verde, por encima del máximo de la vela anterior, con delta positivo y
todavía por debajo de `H1`. Entrada en la apertura siguiente. SL = mínimo de la onda 2
- 0.1 ATR. TP = mínimo de la onda 2 + 1.618 × onda 1.

Salida por tiempo: 3 × duración de la onda 1, con un mínimo de 20 velas.

Etiqueta de lectura (independiente del trade): `wave3` = el precio superó `H1` antes de
perder `L0`. Mide si el 1-2 era real, aunque el trade haya salido por stop.

Train/test: 70% cronológico inicial / 30% final. El número que vale es el del 30% final.

## 4. Hipótesis registradas (solo estas tres)

| Id | Hipótesis | Feature | Fuente |
|---|---|---|---|
| V1 | Onda 2 con menos volumen medio que la onda 1 rinde más | `vol_w2_w1 < 1` | `elliott-04`: el volumen decrece en la 2 |
| V2 | Absorción en el mínimo de la 2 (delta negativo, cierre en la mitad alta de la vela) rinde más | `absorption_w2` | `elliott-04`, `gatillo-long-visual-atas` |
| H3 | A favor de la tendencia 4h (cierre 4h sobre EMA50 para long) rinde más | `htf_aligned` | `structure-corrections`, cheat sheet |

Criterio: expectancy "con" mayor que "sin", y los IC 95% del WR no se pisan. Con
menos de ~100 candidatos por grupo, solo se mira la dirección; no se concluye.

## 5. Revisión ciega

Pantalla `BACKTEST EW` del cockpit. Muestra un candidato al azar cortado en la vela de
decisión de la variante B, sin fecha y con precio normalizado (% contra el cierre de
decisión), para que no se reconozca el momento histórico. Decidís tomo / no tomo, conteo
y confianza. Recién después se revela qué pasó.

Tu edge = resultado de los que tomaste − resultado de todos los que revisaste.
