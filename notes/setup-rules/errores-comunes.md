# ERRORES COMUNES Y CHECKLIST PRE-TRADE (diario personal)

Registro de errores reales para no repetirlos. Revisar ANTES de cada entrada.

================================================================
ERROR #1 — MOVER EL STOP (20-jun, HYPE short onda C)
================================================================
Que paso:
- Short en onda C del triangulo (onda E), entry ~70.747.
- Stop bien puesto al inicio: 70.854 (arriba del alto de la onda B). CORRECTO.
- Lo MOVI a ~70.78 (demasiado ajustado, dentro del ruido).
- Salto el stop en 70.78 por una mecha y me saco del trade.
- El precio DESPUES cayo limpio a 70.387 (0.618 de los targets de C) y siguio
  hasta zona 69.67-69.95 (1.236-1.618). Movimiento de ~1.25% que me perdi.

Leccion:
- NUNCA ajustar el stop hacia adentro por miedo/impaciencia. El stop se define
  por ESTRUCTURA (arriba del alto de B), no por cuanto "aguanto" ver en rojo.
- Una vez puesto el stop estructural, solo se mueve a favor (trailing tras TP1),
  nunca en contra de la tesis.
- Si el stop estructural "duele", el size esta mal, no el stop. Bajar size.
- Regla: dejar respirar el trade el ancho de al menos 1 vela del TF operado.

Costo del error (si lo hubiera dejado correr):
- $10.000 con 10x = posicion $100.000. Movimiento 1.25% = ~$1.250 de profit perdido.

================================================================
ERROR #2 — NO TRAZAR EL TARGET DE LA ONDA C
================================================================
Que paso:
- No habia proyectado los targets Fib de la onda C antes de entrar.
- El precio reboto PERFECTO en el 0.618 de los targets de C (70.387) y de ahi
  continuo. Si lo hubiera trazado, sabia donde tomar profit parcial y donde
  esperar continuacion.

Leccion:
- ANTES de entrar, trazar SIEMPRE los targets de la onda en curso:
  - Onda C (zigzag): 1.0 - 1.236 - 1.618 de la onda A (proyectada desde B).
  - Marcar 0.618 del recorrido de C como primer nivel de reaccion/TP parcial.
- Tener el mapa de niveles dibujado = saber donde estoy y donde voy.

================================================================
ERROR #3 — STOP DEMASIADO CORTO (SCALP-TIGHT EN UN MOVIMIENTO MACRO) (21-jun, HYPE short techo box)
================================================================
Que paso:
- Short en el techo del box / rechazo en resistencia ~68.50 (rejection_at_resistance
  + delta flip confirmado en 1m/5m/15m). DIRECCION CORRECTA.
- El asistente sugirio SL ajustado 68.65 (scalp) en vez del estructural.
- Una mecha de 1m toco 68.63 y me saco del trade.
- INMEDIATAMENTE en la vela siguiente de 15m (02:45 UTC) el precio se desplomo:
  68.63 -> 67.33 -> 66.58 -> 66.06 -> LOW 65.20. Caida de ~-5% en pocas horas.
- El short era PERFECTO. El stop corto me saco justo antes del movimiento.

Leccion:
- El stop se pone en el ALTO ANTERIOR ESTRUCTURAL (swing high real, ~68.92),
  NO en el borde ajustado del nivel de entrada.
- Diferenciar SCALP de MOVIMIENTO MACRO: si la tesis es un giro grande (fin de onda 4,
  rechazo de resistencia mayor), el stop va arriba del swing estructural y se acepta
  mas riesgo en puntos pero MENOS size. No usar stops de scalp (3-5 ticks) en un setup
  que apunta a un movimiento de varios %.
- Un stop que solo aguanta el ruido de 1m NO sobrevive a la mecha previa al impulso.
- Regla concreta: SL short = alto del swing previo + colchon (0.1-0.2%). Si eso es
  mucho %, BAJAR SIZE, no acortar el stop.

Costo del error:
- Movimiento perdido ~-5% (68.6 -> 65.2). Con size correcto y stop estructural, era
  el trade del dia.

================================================================
ERROR #4 — NO ENTRAR POR MIEDO EN UN LONG VALIDO (22-jun, HYPE long GP / W3)
================================================================
Que paso:
- Despues del flush a 65.20, el conteo paso a W1 macro (65.20 -> 69.55) y se esperaba
  un sub-W2 al GP menor (66.8-67.0) para entrar long en arranque de W3.
- El sistema marco DOS oportunidades validas:
  1) ~16:10 UTC del 22: long en GP ~66.92 con 82% + absorcion + buy_ratio 0.73. NO ENTRE.
     Reboto a ~67.79 (+0.85%).
  2) Retroceso del 22 (~13:00-13:30 hora grafico): minimo real 67.01 (borde superior
     del GP / 0.618), reclaim y arranque. El watcher pito sweep_reclaim_bottom y
     delta_confirmation. NO ENTRE (esperaba que tocara 66.8-67.0 exacto).
- Desde 67.0 el precio subio LIMPIO hasta 69.55. Movimiento de ~+3.5% que dejamos pasar.
- El asistente AVISO que el setup era bueno. No entre por MIEDO / PSICOLOGIA, no por
  falta de senal.

Leccion:
- El miedo despues de un stop (Error #3, el short que me saco) me dejo PARALIZADO para
  el long siguiente. Patron clasico: perder un trade -> congelarse en el proximo valido.
  Esto es revenge-paralysis, la cara opuesta del revenge-trade.
- Una zona (POI) es una ZONA, no un punto. Exigir el tick exacto 66.8-67.0 cuando el
  precio reacciona en 67.0 (borde) es perder entradas validas. En W3 fuerte los
  compradores defienden 0.5-0.618, raramente dejan llegar al GP completo.
- Cuando hay CONFLUENCIA (POI + buy_ratio>0.6 + absorcion + reclaim) la senal ya es
  suficiente. Esperar "la perfecta" = no operar nunca.
- Si tengo plan, niveles y SL estructural definidos ANTES, la entrada debe ser mecanica.
  El miedo aparece cuando NO tengo el plan claro; aca SI lo tenia y aun asi no ejecute.

Costo del error:
- ~+3.5% de movimiento (67.0 -> 69.55). Con $10k a 10x y size correcto (SL estructural
  ~66.45, riesgo ~0.8%), era el trade limpio del dia.

----------------------------------------------------------------
ACIERTOS (lo que SI hicimos bien — reforzar)
----------------------------------------------------------------
- 21-jun: la DIRECCION del short en el techo (68.50) fue correcta; el unico fallo fue el
  stop corto (Error #3), no la lectura. La tesis macro (giro bajista) se cumplio (-5%).
- 22-jun: el reconteo Elliott tras el flush (W1 65.20->69.55, sub-W2 al GP) fue CORRECTO:
  el precio respeto el GP y arranco W3 tal como se mapeo. La LECTURA fue acertada.
- Niveles Fib trazados ANTES (GP, 0.5, 0.382, invalidacion 65.66): el plan estaba bien
  armado y los targets (67.85 / 69.55) se cumplieron al pie de la letra.
- El watcher disparo las alertas correctas (sweep_reclaim_bottom, delta_confirmation,
  absorcion) en tiempo real. La herramienta funciono; fallo la EJECUCION humana.
- SL definido estructural (66.45 / 65.66 MCO) y no scalp: aprendimos del Error #3.

CONCLUSION GENERAL (21 y 22-jun):
- El analisis (Elliott + order flow + niveles) estuvo BIEN los dos dias. Los targets se
  cumplieron. El problema NO es la lectura del mercado: es la EJECUCION y la PSICOLOGIA.
- Patron a romper: stop corto -> me sacan -> miedo -> no entro al siguiente valido ->
  veo el movimiento irse. Dos dias seguidos perdiendo el trade del dia por gestion/miedo,
  no por mal analisis.
- Solucion: entrada MECANICA cuando se cumple el checklist, size que haga el SL
  estructural soportable, y aceptar de antemano que una mecha puede sacarme. No buscar
  el tick perfecto: operar la ZONA.

================================================================
ERROR #5 — MOVER EL STOP / SALIR POR RUIDO DE 1m (22-jun, HYPE short 69.25)
================================================================
Que paso:
- Short en ~69.25 (techo zona 69.55, delta negativo, OF bajista en 1m). Tesis valida:
  pullback hacia GP 66.8-67.0. SL estructural definido: 69.62 (arriba del W1 high).
- El trade iba bien. Precio cayo hacia 68.9 y luego dump limpio a ~67.91 (-2% desde entry).
- A las 15:31 (Buenos Aires) una vela de rebote subio el precio. El asistente cambio de
  opinion: recomendo CORTAR el short porque buy_ratio flipeo a 0.54-0.68 en 1m y salto
  alerta sweep_reclaim_bottom LONG (ruido / senal rezagada del sweep de 66.535 en 15m).
- Por miedo al rebote + consejo del asistente, se MOVIO el stop (mas abajo / mas ajustado)
  en vez de dejar el estructural 69.62. La mecha de las 15:31 saco del trade.
- Inmediatamente despues el precio siguio cayendo hasta ~67.91. Movimiento de ~-2% perdido.

Leccion:
- Una vez puesto el SL ESTRUCTURAL (69.62), NO moverlo por rebotes de 1m. Una mecha
  dentro del rango estructural es RUIDO, no invalidacion.
- El asistente NO debe recomendar salir antes del SL estructural salvo invalidacion REAL
  (romper 69.62 y cerrar arriba). Un flip de buy_ratio en 1m NO invalida un short con
  SL arriba del swing.
- Las alertas sweep_reclaim / trapped_shorts en 15m pueden ser REZAGADAS (refieren a
  barridos de horas atras). No usarlas para salir de un scalp en curso en otro nivel.
- Mismo patron que Error #1 y #3: ajustar stop o salir por miedo -> me sacan -> el trade
  sigue en mi direccion sin mi. Tres veces en tres dias.
- Regla: si el plan dice SL 69.62, aguanto hasta 69.62 o TP. Punto. Si duele, baje size
  ANTES de entrar, no acorte el stop DESPUES.

Costo del error:
- ~-2% de movimiento (69.25 -> 67.91). Con size correcto y SL estructural intacto,
  era el trade ganador del dia despues de dos misses.

Acierto del trade:
- DIRECCION CORRECTA otra vez. Lectura del techo + delta negativo + rechazo en zona
  alta fue acertada. El unico fallo fue GESTION (stop movido / salida prematura).

================================================================
CHECKLIST PSICOLOGICO PRE-ENTRADA (leer cada vez)
================================================================
[] Stop definido por ESTRUCTURA (no por tolerancia emocional al rojo).
[] Size calculado para que el stop estructural sea <= 2-3% de la cuenta.
[] Targets de la onda trazados (Fib) ANTES de entrar.
[] No voy a mover el stop en contra. Solo trailing a favor tras TP1.
[] Acepto el riesgo completo desde la entrada (una mecha puede pasar).
[] Si me saca el stop bien puesto, es parte del juego. NO re-vengarme (revenge trade).
[] TP1 parcial planificado para soltar presion emocional.
[] El POI es una ZONA, no un tick. Si el precio reacciona en el BORDE con OF a favor, ENTRO.
[] Si perdi el trade anterior, NO me congelo en el proximo valido (anti revenge-paralysis).
[] Si se cumple el checklist + confluencia (POI + buy_ratio + absorcion/reclaim): ENTRADA MECANICA.
[] Una vez dentro: NO salir ni mover stop por ruido de 1m. Solo SL estructural o TP.
[] El asistente NO recomienda cortar antes del SL estructural salvo ruptura real del nivel.

================================================================
RECORDATORIOS DE GESTION (de mis notas de setup-rules)
================================================================
- Maximo 2% de la cuenta en SL por posicion.
- "El mercado hara lo que quiera, no lo que queremos."
- TP1 rapido para dejar de preocuparse, resto a la liquidez del otro lado.
- NO operar tambien es una posicion.
- Con palanca alta (10x): un movimiento de 1% = 10% de la cuenta. Respetar el size.
