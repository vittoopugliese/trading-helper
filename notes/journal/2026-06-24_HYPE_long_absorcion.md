# [2026-06-24] HYPE — LONG — absorcion en POI

> Estado: ESTUDIO (caso de referencia, documentado)  ·  Resultado: n/a (analisis posterior)

## 1. Contexto
- Bias HTF: bajista — onda C de (4) (B flat toco 1.05 ~77, ver context/HYPEUSDT.yaml).
- POI / zona: ~59.6 (caja verde / pdLow + POC previo).
- Long contra-tendencia HTF -> habria sido scalp de rebote, TP rapido, size reducido.

## 2. Gatillo / Order Flow (5m, hora local UTC-3)
- Patron: ABSORCION en el borde del POI.
- Las 4 señales:
  1. Precio en mi linea: SI, aterrizo justo en la verde ~59.6.
  2. Mecha pincha y vuelve: SI, low 59.25 y cierre arriba.
  3. Barra de volumen > media: SI (177k, 169k, 183k — las mas altas del tramo).
  4. Delta flip: SI, -74,536 -> +15,970 -> +18,828.
- Datos clave:
```
5m  13:40  C=59.42  d=-74,536  v=177k   <- climax vendedor
5m  13:45  L=59.25  C=59.28  d=+15,970  <- DELTA FLIP en el minimo
5m  13:55  C=59.63  d=+18,828           <- continuacion
1m  13:47  C=59.29  d=+29,705  v=80k VOLSPIKE  <- confirmacion buyers
```

## 3. Plan (que habria sido)
- Entry: ~59.5-59.6 (reclaim / vela de confirmacion con delta+).
- SL estructural: bajo 59.25 (debajo de la mecha de absorcion). Distancia ~0.5%.
- TP1: liquidez/resistencia cercana arriba. R:R aceptable.
- Sizing: scalp contra-tendencia -> size reducido (~50%).

## 4-5. Ejecucion / Psicologia
- N/A (caso documentado en analisis posterior, no operado en vivo).

## 6. Revision
- Que ENSEÑA: el patron de absorcion conservador del SETUP_RULE funciono limpio.
  Esperar la vela con delta+ (no adivinar el minimo) era la entrada correcta.
- Comparar con el 25 (sweep+reclaim): el 24 fue absorcion en el BORDE; el 25 fue barrido
  POR DEBAJO -> el 25 es de mayor calidad.
- Ver detalle visual en `order-flow/gatillo-long-visual-atas.md`.
