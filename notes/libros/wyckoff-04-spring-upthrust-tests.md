# Wyckoff — Spring, Upthrust y Tests (los eventos gatillo)

Fuente: Método Wyckoff de Trading, cap. 2.3–2.5. Son las sacudidas (shakeouts) que
generan las entradas de mayor probabilidad. Traducen directamente al concepto de
"liquidity sweep + reclaim" del order flow.

## Spring (giro alcista)

Penetración breve por debajo del soporte del rango de acumulación seguida de un
reingreso rápido. Objetivo: barrer stops de venta y capturar liquidez antes de subir.
Clasificación por intensidad de oferta:
- **Terminal shakeout / spring #1:** penetración profunda con volumen alto (aún hay
  oferta) → necesita test antes de entrar.
- **Spring #2:** penetración con volumen medio → oferta moderada.
- **Spring #3:** penetración mínima con volumen bajo → sin oferta, el más alcista,
  permite entrada directa.

## Test del spring

Retroceso posterior que revisita la zona del mínimo del spring **con volumen bajo y
sin hacer nuevo mínimo**. Confirma que la oferta se agotó. Es el momento de menor
riesgo para entrar: stop justo debajo del mínimo del spring.

## Upthrust (giro bajista)

Espejo del spring: penetración breve por encima de la resistencia del rango de
distribución + reingreso. Barre stops de cortos y atrapa compradores. El **UTAD** es
el upthrust final. Test posterior con volumen bajo confirma ausencia de demanda.

## Cómo confirmarlo con order flow (clave para el motor)

Un spring/upthrust VÁLIDO tiene esta firma en el order flow:
1. **Sweep:** mecha que perfora el nivel y barre liquidez (stops).
2. **Reclaim:** el precio vuelve dentro del rango rápido (cierre del otro lado del nivel).
3. **Absorción:** en el extremo, volumen alto pero delta que no acompaña la ruptura
   (esfuerzo sin resultado → ley de esfuerzo-resultado).
4. **Confirmación:** CVD gira a favor del reingreso; el book muestra desequilibrio en
   la dirección del giro.

Sin los 4, es sólo una ruptura y puede seguir. Con los 4, es el gatillo A+.

## Regla operativa

- No anticipar el spring: esperar el reingreso confirmado (reclaim) + señal de order
  flow. Entrar en el test, no en la mecha.
- Stop SIEMPRE al otro lado del extremo del spring/upthrust (donde se invalida).
- Es el mismo patrón que `notes/order-flow/entry-patterns` y el journal de
  "sweep-reclaim" y "absorcion".
