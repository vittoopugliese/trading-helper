# RISK & SIZING — cuenta $300, leverage x10 (HYPE / BTC)

## Parametros reales de mi cuenta (jun 2026)
- Capital: **$300 USD** (cuenta de prueba para demostrarme constancia varios meses).
- Leverage: **x10** (fijo; pasare a x5 cuando tenga mas capital).
- **TOPE DURO de riesgo por trade: $21 (7% de la cuenta).** Nunca pasarlo.
- Pares: **HYPE** y **BTC** (Binance Futures USDT-M).
- Entradas: **1-3 por trade** segun confianza (ver gestion-trade-activo.md).
- Objetivo: subir esta cuenta -> validar -> pasar a $1000 -> escalar.
- Antecedente: ya me fundi 2 veces sobreapalancando sin tomar ganancia (oct-dic 2024).
  Esta nota existe para NO repetirlo. La cuenta chica se cuida MAS, no menos.

## OJO — el error de nombre que casi cometo (margen != riesgo)
- "SL 1.5% x10 = 15% = $45" es aritmeticamente correcto PERO eso es arriesgar 15% de la
  cuenta por trade (all-in $300 de margen). 3-4 perdidas seguidas = cuenta destruida.
- 1.5% es la DISTANCIA del SL, no el riesgo. El riesgo es lo que pierdo si salta = Notional x SL%.
- Por eso el tope es $21 (7%), no $45 (15%). Si para el size que quiero el riesgo pasa $21,
  BAJO EL SIZE, no agrando el SL ni voy all-in.

================================================================
LA VERDAD QUE NO PUEDO OLVIDAR: MARGEN != RIESGO
================================================================
(Consenso de la industria 2026 — Kraken, KuCoin, etc.)
- El RIESGO real de un trade = Notional x distancia_al_SL (%). NO es el margen.
- El leverage solo cambia el MARGEN requerido y acerca la LIQUIDACION. No cambia el riesgo
  si tengo SL puesto.
- Formula: **Notional = Margen x Leverage** ; **Riesgo_$ = Notional x (distancia SL %)**.
- A x10 la liquidacion esta a ~9-10% en contra. Mi SL (1-2%) debe estar MUY adentro de eso.
  Si el SL queda cerca de la liquidacion, una mecha me liquida antes del stop -> JAMAS.

================================================================
MIS NIVELES DE SIZING (margen comprometido por trade)
================================================================
Repartido en 1-3 entradas dentro de la zona; el margen total es el de la fila.

| Conviccion | Margen total | Notional (x10) | Cuando |
|---|---|---|---|
| Estandar    | $100 | $1,000 | setup B+/A normal |
| Alta        | $200 | $2,000 | A+ claro, las 3 banderas |
| Maximo (raro)| $300 | $3,000 | "all-in", A+ excepcional con todo alineado |

================================================================
LO QUE CUESTA DE VERDAD (tabla riesgo real = % de la cuenta $300)
================================================================
Riesgo_$ = Notional x SL%.  (Mirar SIEMPRE esta tabla antes de elegir size.)

| SL dist | $100 margen ($1k notional) | $200 ($2k) | $300 ($3k) |
|---|---|---|---|
| 0.5% | $5  (1.7%)  | $10 (3.3%)  | $15 (5%)    |
| 1.0% | $10 (3.3%)  | $20 (6.7%)  | $30 (10%)   |
| 1.5% | $15 (5%)    | $30 (10%)   | $45 (15%)   |
| 2.0% | $20 (6.7%)  | $40 (13.3%) | $60 (20%)   |
| 3.0% | $30 (10%)   | $60 (20%)   | $90 (30%)   |

LECTURA CLAVE:
- **TOPE DURO: el riesgo_$ NUNCA pasa $21 (7%).** Las celdas de la tabla por encima de $21
  estan PROHIBIDAS (ej. $200 o $300 margen con SL 1.5-3%).
- Con $200-300 de margen y un SL de 2%, arriesgo 13-20% de la cuenta en UN trade. PROHIBIDO.
  Mis blow-ups pasados fueron exactamente esto.
- El size "Alto/Maximo" SOLO es sensato si el SL es CORTO (0.5-1%) Y la conviccion A+, y
  SIEMPRE respetando el tope $21. Ej: $200 margen con SL 0.5% = $10 riesgo. OK.
- Si para entrar al size que quiero el riesgo se dispara > $21, BAJO EL SIZE, no el SL.

RANGO OPERATIVO OBJETIVO: arriesgar ~$15-21 por trade (5-7%). Por debajo si la conviccion es media.

================================================================
COMO DECIDO EL SIZE (proceso correcto, no al reves)
================================================================
1. Encuentro el SL estructural (donde se rompe la tesis). NO lo elijo por el size.
2. Mido la distancia entry->SL en %.
3. Elijo cuanto $ quiero arriesgar (objetivo ~$10-20, segun conviccion).
4. Notional = Riesgo_$ / SL% ; Margen = Notional / 10.
5. Si ese margen supera mi tope ($300) o el riesgo supera lo aceptable -> reduzco.
6. Reparto en 1-3 entradas dentro de la zona.
NUNCA al reves (elegir margen primero e inventar el SL para que entre). Eso es Error #3.

Ejemplo HYPE (~$62): SL estructural a 1.2% (entry 62.0, SL 61.25).
- Quiero arriesgar $21 (tope) -> Notional = 21/0.012 = $1,750 -> Margen = $175.
- Riesgo real $21 = 7% de la cuenta. R:R 2 -> +$42 (+14%) si pega TP.
- Si la conviccion es media, arriesgo $15 -> Notional $1,250 -> Margen $125.

Tabla rapida con el tope $21 (lo que NO debo pasar):
- SL 0.5% -> notional $4,200 pero margen $420 > $300: capear a margen $300 ($3k notional),
  riesgo real = $15. (all-in solo aca, con SL bien corto.)
- SL 1.0% -> notional $2,100 -> margen $210.
- SL 1.5% -> notional $1,400 -> margen $140.
- SL 2.0% -> notional $1,050 -> margen $105.
- SL 3.0% -> notional $700  -> margen $70.

================================================================
GESTION DE PERDIDAS / RACHAS (proteger la cuenta chica)
================================================================
- Maximo riesgo por trade: TOPE DURO $21 (7%). Objetivo operativo $15-21. NUNCA exceder.
- Limite de perdida diaria: -2 trades perdedores seguidos = STOP por hoy (apagar, no revenge).
- Drawdown semanal: si caigo ~-20% en la semana, bajo a size minimo ($100) hasta recuperar.
- 1-3 trades por dia maximo. Calidad > cantidad. "No operar tambien es una posicion."
- TP1 parcial siempre (ver gestion-trade-activo.md): asegura verde y baja la varianza.

================================================================
MULTIPLES ENTRADAS / EXPOSICION TOTAL
================================================================
- Si tengo 2 trades abiertos a la vez (ej HYPE + BTC), sumar el riesgo: el total no
  deberia pasar ~10% de la cuenta combinado.
- HYPE y BTC estan CORRELACIONADOS (ver smart-money/correlacion-btc-eth-hype.md): dos longs
  a la vez = NO es diversificar, es DOBLE size del mismo riesgo direccional. Tenerlo en cuenta.

================================================================
META DE CRECIMIENTO (interes compuesto, no apuestas)
================================================================
- $300 -> $1000 NO se hace con un all-in afortunado; se hace con consistencia (mi error pasado).
- A ~5% por trade ganador y win rate decente, el compuesto hace el trabajo. Paciencia.
- El objetivo de esta cuenta es DEMOSTRARME CONSTANCIA varios meses, no maximizar un trade.
- Cuando valide el sistema con journal (>=100 trades) y stats positivas -> $1000 -> escalar.
