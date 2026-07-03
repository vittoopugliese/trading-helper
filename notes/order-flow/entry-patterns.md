# ORDER FLOW - Patrones de entrada (Green Flags) y Red Flags

REGLA MAESTRA: siempre buscar estos patrones DENTRO de un POI. Nunca operar en el aire.
SIEMPRE: precio + volumen. No operar a ciegas.

================================================================
GREEN FLAGS - patrones de entrada
================================================================

1) ABSORCION / TRADERS ATRAPADOS
   Que es: los sellers intentan romper un nivel pero quedan mal posicionados;
   institucionales ponen ordenes limite que absorben la presion y el precio
   revierte bruscamente.
   Como identificarlo (order flow):
   - Delta negativo incrementando (muchos numeros rojos altos en footprint).
   - A pesar de la presion vendedora, la vela CIERRA por fuera del soporte.
   - El POC (volumen mas fuerte) queda atrapado en la mecha inferior.
   Entradas:
   - Agresiva: en el retest del POC de la vela de absorcion (fast entry, no siempre ocurre).
   - Conservadora: esperar que la siguiente vela cierre con cambio a delta positivo
     (buyers toman control) y ejecutar.
   Gestion: SL por debajo de la zona de absorcion. Target: liquidez mas cercana al otro lado.

2) DRENAJE DE DELTA (perdida de interes / posible reversal)
   Que es: el precio sube hacia el POI pero el delta deja de crecer o disminuye,
   avisando que el movimiento se agota.
   Como identificarlo: en contexto alcista el delta deja de ser consistente
   (1900, 2000, 1700 -> 400, 600, negativos). Se forma una especie de doble techo
   en las velas de delta: el precio sube pero no hay compradores agresivos.
   Entradas:
   - Identificar el falso rompimiento (fakeout) en el POI: el precio intenta superar
     el nivel pero falla.
   - Esperar el cambio de delta confirmado (de positivo a negativo).
   - Ejecutar cuando precio Y volumen confirman. Siempre dentro de un POI.

3) TOMA DE LIQUIDEZ (sweep)
   Que es: movimiento que busca activar stop loss (bajo minimos o sobre maximos)
   para luego revertir. Foco principal: el VOLUMEN. Sin volumen no hay sweep optimo.
   Como identificarlo: al alcanzar el nivel, el volumen debe ser MAS ALTO que la media,
   confirmando que las ordenes fueron absorbidas/liquidadas (ver si el POI acompana).
   Entradas:
   - Confirmacion del precio: nunca operar solo por volumen; esperar el rechazo de
     la zona tras ver el pico de volumen.
   - No operar a ciegas: volumen > media + confirmacion en precio = validez.

4) RETROCESO / RETESTEO DE OB
   Bloque de ordenes = mucho volumen. Sirve para entradas de reversal con SL por
   respeto de las areas. Marcar con caja todo el OB y entrar al retest.

5) FINISHED / UNFINISHED AUCTION
   Disminucion de ordenes hacia highs/lows = no hay interes en testear esa zona.
   Si ademas hay un "FA", puede indicar reversal (mirar que la punta del POC sea 0).

================================================================
RED FLAGS - cosas que NO queremos ver
================================================================
- Finished Auction: funciona como iman para el precio (probable retest a esa zona).
- Toma de liquidez SIN incremento de volumen: si el volumen no incrementa, el precio
  tiende a volver ahi (liquidez falsa, no habia interes genuino).
- Delta "leading" en contra: si al llegar al POI la zona se infecta con mucha
  agresion de delta en contra, el precio retrocede.

================================================================
CONFIRMACION BULLISH / BEARISH (resumen pilares)
================================================================
- BULLISH = +Volumen +Open Interest + Delta POSITIVO (longs).
- BEARISH = +Volumen +Open Interest + Delta NEGATIVO (shorts).
- Movimiento rompiendo rango con volumen BAJO vs el del rango = posible fakeout.
- CVD para divergencias:
  - Alcista: precio higher lows + CVD lower lows = short squeeze (continuacion al alza).
  - Bajista: precio lower highs + CVD higher highs = long squeeze (continuacion a la baja).

================================================================
SANTO GRIAL PARA CONFIRMAR UN SFP
================================================================
[] Open Interest disminuye.
[] Delta negativo.
[] Incremento de OI contrario.
[] Traders atrapados.
[] Volumen en la mecha (no en el cuerpo).
