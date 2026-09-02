# Síntesis — Confluencia de los libros en el setup A+

Nota "pegamento": integra Elliott + Wyckoff + Order Flow + Elder + psicología en un
único marco de decisión, mapeado al checklist A+ y al scoring del motor. Es la nota que
más conviene que Claude cite para dar veredictos coherentes.

## La tesis unificada

Los cuatro marcos técnicos describen LO MISMO desde ángulos distintos, porque el mercado
es fractal ([[fractales-01-mercados]]):

| Concepto | Elliott | Wyckoff | Order Flow | Elder |
|----------|---------|---------|------------|-------|
| Fin del retroceso, zona de compra | fin onda 2 / 4, golden pocket | spring / LPS | sweep+reclaim, absorción | oscilador sobrevendido en marea alcista |
| Impulso principal | onda 3 | fase D/E, SOS, salto del arroyo | delta/CVD fuerte, volumen spike | marea + Force Index a favor |
| Agotamiento / giro | onda 5 + divergencia | UTAD / clímax, esfuerzo≠resultado | divergencia CVD, absorción en extremo | divergencia histograma MACD |
| Trampa de liquidez | plana expandida (B) | spring / upthrust | barrido de stops + reclaim | falsa ruptura |

Cuando VARIOS marcos apuntan al mismo nivel y dirección → confluencia alta → A+.

## Checklist A+ enriquecido (confluencia máxima)

Un setup A+ idealmente cumple, en el POI:
1. **Estructura Elliott:** onda y fib claros; reglas inviolables intactas
   ([[elliott-01-estructura-impulso]]). Invalidación definida.
2. **Contexto Wyckoff:** el POI coincide con spring/LPS (long) o UTAD/LPSY (short)
   dentro de un rango con "causa" suficiente ([[wyckoff-02-acumulacion]]).
3. **Alineación multi-TF:** las 3 temporalidades a favor (marea/ola/onda de Elder =
   línea de menor resistencia de Wyckoff = multi-TF del motor).
4. **Gatillo de order flow en el POI:** sweep+reclaim+absorción, delta/CVD confirmando
   ([[wyckoff-04-spring-upthrust-tests]]).
5. **Divergencia coherente:** presente si es fin de onda 5 / clímax; ausente si es onda 3.
6. **R:R ≥ 2** con stop por estructura (invalidación), no por monto.
7. **Gestión:** riesgo ≤ tope $21; sizing desde el stop ([[elder-03-gestion-dinero]]).
8. **Psicología OK:** sin FOMO/revancha; si falta setup, no operar
   ([[psico-douglas-01-probabilidades]]).

Cuantas más casillas, mayor grado. Faltar la 6, 7 u 8 = NO TRADE aunque la técnica sea linda.

## Mapa a las condiciones del motor (config.yaml → weights)

- `price_in_poi` + `invalidation_respected` → puntos 1 y 2.
- `trend_alignment` (multi-TF) → punto 3.
- `of_confirmation_at_poi`, `delta_cvd`, `volume_spike` → punto 4.
- `cvd_divergence` → punto 5.
- `risk_reward` → punto 6.
- Reglas de riesgo (no ponderadas, son filtro duro) → puntos 7 y 8.

## Jerarquía de decisión (orden de lectura)

1. ¿Hay contexto/causa? (Wyckoff rango + Elliott onda). Si no, no hay trade.
2. ¿El precio está EN el POI y respeta la invalidación? Si no, esperar (alerta).
3. ¿El order flow confirma el gatillo en el POI? Si no, esperar el reclaim.
4. ¿R:R y sizing cumplen las reglas duras? Si no, NO TRADE.
5. ¿Estoy mentalmente en juego A? Si no, no operar ([[psico-tendler-01-juego-mental]]).

Este orden = los 5 pasos de Schroeder ([[wyckoff-05-checklist-operativa]]) fusionados con
la triple pantalla de Elder y la gestión de riesgo. Es el corazón del veredicto A+.
