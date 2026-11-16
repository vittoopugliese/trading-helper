# Elder — Gestión del dinero: reglas del 2% y del 6%

Fuente: Vivir del Trading (Alexander Elder), cap. gestión del dinero. "Un buen trader
cuida su capital como un buzo cuida su oxígeno." La supervivencia va PRIMERO; las
ganancias, después. La gestión del dinero es lo que separa al profesional del apostador.

## La regla del 2% (riesgo por operación)

Nunca arriesgues más del **2% del capital de la cuenta en una sola operación** (riesgo
= entrada − stop, no el margen). Protege de que una racha de pérdidas o un solo error
te destruya. Elder: "si tu stop exige arriesgar más del 2%, ignorá esa operación".
- Es el "ataque de tiburón": una sola mordida grande te saca del juego.
- Define el TAMAÑO de posición a partir del stop, no al revés.

## La regla del 6% (freno mensual)

Si en el mes acumulás **−6% del capital**, dejás de operar hasta el mes siguiente.
Protege del "ataque de pirañas": muchas pérdidas chicas que sangran la cuenta. Frena las
rachas emocionales (revancha) antes de que hagan daño irreversible.

## Cómo se dimensiona una posición

1. Definí el stop por estructura (invalidación), no por el monto que querés arriesgar.
2. Riesgo $ máximo = 2% de la cuenta (o el tope que uses).
3. Tamaño = Riesgo $ / (distancia entrada→stop).
   → Si el stop está lejos, la posición es CHICA. Nunca agrandes el stop para forzar tamaño.

## Traducción a la cuenta del helper

La config del helper es MÁS AGRESIVA que el 2% de Elder (7% > 2%): es una decisión
para cuenta chica apalancada, compensada con frenos diarios (ver
`notes/setup-rules/risk-sizing-cuenta.md`). El 2% queda como referencia teórica:
- Tope duro **$21 por trade** (7% de $300) — el SL define el riesgo, margen ≠ riesgo.
- **2 pérdidas seguidas → stop del día** (anti-revancha, versión intradía de la regla 6%).
- 1–3 entradas escalonadas; exposición combinada ≤ 10% si hay 2 trades correlacionados.

## Principio de fondo

- Bajá el tamaño, nunca el stop.
- La consistencia mensual (+7% ±2, estilo plan BEIG) vence a buscar el home run.
- Enlaza con [[psico-elder-01-psicologia-masas]] (supervivencia primero) y
  [[plan-beig-01-smc-plan]] (reglas de riesgo por tipo de operativa).
