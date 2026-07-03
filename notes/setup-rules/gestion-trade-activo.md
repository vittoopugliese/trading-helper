# GESTION DEL TRADE ACTIVO — entradas, parciales, trailing, salidas

## Que es
Como me comporto DESPUES de entrar. Es donde mas dinero perdi (Errores #1, #3, #5:
los 3 fueron de gestion, no de analisis). Esta nota existe para que la ejecucion sea
mecanica y no emocional. Cruzar con `errores-comunes.md` y `a-plus-checklist.md`.

REGLA MADRE: el SL estructural se define ANTES de entrar y NO se mueve en contra. Nunca.
Solo se mueve A FAVOR (trailing) despues de TP1.

================================================================
1. ENTRADAS ESCALONADAS (1-3 segun confianza)
================================================================
El POI es una ZONA, no un tick. Reparto el size en hasta 3 entradas dentro de la zona:
- Confianza ALTA (A+, las 3 banderas): puedo entrar full en 1 o repartir 2 (borde + confirmacion).
- Confianza MEDIA (B+): 2-3 entradas escalonadas para promediar dentro de la zona.
- El SL estructural es UNICO para toda la posicion (debajo/arriba del swing), no por entrada.
- Si la 1ra entrada ya corre a favor y no volvio a la zona: NO perseguir, opero con lo que entre.

================================================================
2. TAKE PROFIT PARCIAL (la clave psicologica)
================================================================
- TP1 RAPIDO siempre: cerrar 30-50% en el primer target / liquidez cercana.
  "Hacer TP1 rapido para dejar de preocuparse" (mi propia regla). Saca la presion emocional.
- Tras TP1: mover SL a BREAKEVEN (entry) -> el trade ya no puede perder. Recien AHI respiro.
- TP2: otro 30-40% en la liquidez media / siguiente nivel Fib.
- Runner: dejar 20-30% con trailing hacia la liquidez del otro lado (target de la onda).

================================================================
3. TRAILING DEL STOP (solo a favor, tras TP1)
================================================================
- Antes de TP1: el SL NO se toca. Aguanta el ruido el ancho de >=1 vela del TF operado.
- Despues de TP1: SL a breakeven.
- Despues de TP2: trailing por estructura -> bajo el ultimo higher low (long) / sobre el
  ultimo lower high (short) del TF que opero. No por ticks.
- Nunca trailing tan ajustado que el ruido normal me saque (repetir Error #1/#3).

================================================================
4. CUANDO SI SALGO ANTES DEL SL (invalidacion real)
================================================================
Salir antes del SL SOLO si la TESIS se rompe de verdad, no por ruido:
- Cierre de vela del TF operado rompiendo el nivel estructural (no una mecha).
- Cambio de caracter claro (CHOCH) en mi TF contra mi posicion.
- NO salir por: flip de buy_ratio en 1m, delta momentaneo, alerta rezagada de TF alto,
  un rebote/pullback dentro del rango estructural (eso es RUIDO — Error #5).

================================================================
5. EL ASISTENTE DENTRO DEL TRADE (regla para el agente)
================================================================
- NO recomendar cortar antes del SL estructural salvo ruptura REAL del nivel (cierre, no mecha).
- NO cambiar de opinion por ruido de 1m una vez dentro.
- Las alertas sweep_reclaim / trapped en TF altos pueden ser REZAGADAS: no usarlas para
  salir de un scalp en curso en otro nivel.
- Si el trade va a favor: recordar TP1 parcial + mover SL a BE. Reforzar disciplina, no miedo.

================================================================
6. CHECKLIST DENTRO DEL TRADE (leer si me pongo nervioso)
================================================================
[] Mi SL sigue donde lo puse por estructura? (si si -> no hago nada)
[] El precio rompio el nivel con CIERRE o es solo una mecha/ruido?
[] Ya hice TP1? Si si -> SL en BE, no puedo perder, relajo.
[] Lo que siento es la tesis rota o es miedo al rojo? (si es miedo -> aguanto el plan)
[] Acepte el riesgo completo ANTES de entrar. Una mecha al SL es parte del juego.
