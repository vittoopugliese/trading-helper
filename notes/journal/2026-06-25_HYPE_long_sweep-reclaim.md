# [2026-06-25] HYPE — LONG — sweep + reclaim (shorts atrapados)

> Estado: ESTUDIO (caso de referencia A+, documentado)  ·  Resultado: n/a (analisis posterior)

## 1. Contexto
- Bias HTF: bajista — onda C de (4). Sin Elliott LTF claro; el OF dio la señal solo.
- POI / zona: caja verde + low previo del 24 (~59.6). Liquidez acumulada debajo.

## 2. Gatillo / Order Flow (hora local UTC-3) — A+ (las 3 banderas)
- Patron: SWEEP + RECLAIM con shorts atrapados.
  1. Precio en mi linea: SI (zona verde).
  2. Sweep: SI, barrido a 58.50 POR DEBAJO de la caja (activa stops).
  3. Volumen gigante: SI, 553k (el mas alto del tramo) en el flush.
  4. Delta flip masivo: -88,249 -> +93,579 (5m); -40k/-38k -> +81,856 en 1m.
- Datos clave:
```
5m  10:55  L=58.50  C=58.87  d=-88,249  v=553k   <- capitulacion / sweep
5m  11:00  O=58.88 H=60.48 C=60.00  d=+93,579  v=454k VOLSPIKE  <- REVERSAL
5m  11:05  C=60.74  d=+55,133            <- continuacion
1m  10:59  L=58.50  d=-38,129  v=207k VOLSPIKE  <- barrido del minimo
1m  11:00  O=58.88 H=60.00 C=59.98  d=+81,856  v=172k VOLSPIKE  <- +82k en UNA vela
```

## 3. Plan (que habria sido)
- Entry conservadora: reclaim sobre 59.0-59.5 tras la vela envolvente (11:00).
- SL estructural: bajo 58.50 (debajo del sweep). Distancia ~1-1.5%.
- TP1: 60.7 / 61.1 (highs locales). TP2: 62+. R:R > 2.
- Sizing: aunque contra-tendencia HTF, fue el setup mas limpio -> conviccion alta.

## 4-5. Ejecucion / Psicologia
- N/A (caso documentado en analisis posterior).

## 6. Revision
- Que ENSEÑA: el A+ visual de referencia. Las 3 banderas juntas (sweep real + volumen
  pico + delta flip masivo) = la entrada de mayor probabilidad de mi sistema.
- Diferencia con el 24: aca hubo BARRIDO bajo el soporte antes del reclaim (mas fiable).
- Edge repetible: climax delta- + caja verde/POC + delta flip a + con volumen sobre media.
- Ver detalle visual en `order-flow/gatillo-long-visual-atas.md`.
