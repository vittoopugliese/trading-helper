# SMART MONEY - POI, Clusters, Liquidez, Imbalance y Mitigacion

================================================================
OFERTA Y DEMANDA
================================================================
- OFERTA -> se ofrece -> se vende -> precio baja.
- DEMANDA -> se quiere comprar -> precio sube.
- Marcar con cajitas. Demanda en verde.
- Detectar zona de demanda: ruptura previa con cuerpo de vela + el pullback/pausa
  previa a esa ruptura (esa pausa suele ser un Order Block). Probablemente retestee.

================================================================
POI, CLUSTERS y VOLUME PROFILE
================================================================
- Buscar el Order Block dentro del cluster, y que este dentro de un POI.
- POI = sumatoria de flujos previos / suma de clusters. Mas clusters = mas validado.
- Proceso: marcar clusters (compresiones/rangos) -> tomar perfil de volumen del rango.
- Si el precio rebota en el Value Area High y vuelve, es probable que vuelva a
  meterse al rango: eso anula el rebote, y hay que esperar que vaya a la parte baja.
- POI = zona de interes donde el precio historicamente lateralizo (rangos, ondas 4 y 2).

================================================================
LIQUIDEZ, IMBALANCE y MITIGACION
================================================================
- Detectar liquidez: ver bajos NO mitigados (bajos no tomados). Al tomarlos, el
  precio tiende a explotar al alza.
- Imbalance: precio se mueve con explosividad; dentro existen los Fair Value Gaps.
  Se mide con el nivel 0.5 Fib midiendo SOLO la vela explosiva.
- Marcar POIs y clusters, y colocar blocks por debajo de la liquidez (para compras).
- INDUCEMENT: cuando el precio aun no retesteo correctamente una zona (ej: solo
  mitigo un FVG y faltaba un POI). El precio "amaga" y sube para inducir liquidez,
  liquida el restante y la usa de fuerza hasta el POI faltante.

================================================================
ORDER BLOCK - como saber su efectividad
================================================================
- Bullish OB = ultima vela bajista antes de un movimiento alcista impulsivo.
- Bearish OB = ultima vela alcista antes de un movimiento bajista.
Checklist:
[] Forma un Swing Low/High.
[] Generado en zona de soporte/resistencia importante (cluster).
[] Se confirma cuando el precio cierra por encima/debajo del 50% del OB.
[] Idealmente cambia la estructura a partir de ese movimiento.
[] Idealmente genera impulso con imbalances.
[] Si coincide con 0.618 u Optimal Trade Entry, PERFECTO.
Order Block = bloque de ordenes = volumen esperando ser tomado.
Revisar en ATAS el volumen acumulado en la vela del OB.
