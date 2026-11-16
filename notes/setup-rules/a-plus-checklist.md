# SETUP RULES - Sistema, Risk Management y Checklist A+ maestro

================================================================
SISTEMA POR FASES (punto A -> ser rentable -> punto B)
================================================================
- Fase 1 Entrenamiento: entender como analizar. Accion de precio (Elliott) + lectura
  de volumen (Order Flow).
- Fase 2 Sistema: lo mas importante. Salir del trading discrecional, que el SISTEMA
  senale las posiciones. Usar estrategias existentes o crearlas.
- Fase 3 Backtesting: comprobar el sistema. Confianza = buenas estadisticas. Si es
  negativo, generar otro; si no, seguir.
- Fase 4 Campo: con sistema backtesteado y sample de >=100 trades, operar SOLO ese
  sistema y hacer journaling de todos los trades. Un solo sistema a la vez.
- Fase 5 Mejoras: monthly review de operativa, psicologia y gestion leyendo journals.
  Objetivo: una estrategia, un mentor, perfeccionarse en una cosa a la vez.

================================================================
RISK MANAGEMENT Y PSICOLOGIA
================================================================
- Riesgo por posicion: POLITICA ACTIVA en config.yaml -> risk (7% / $21 tope duro, x10,
  2 perdidas seguidas = stop del dia). El 2% de esta lista es la regla clasica del curso
  (teoria de referencia), no el tope vigente.
- Consistencia: small wins por un largo periodo de tiempo.
- "El mercado hara lo que quiera, no lo que queremos." El trading es una profesion/empresa
  con ganancias y perdidas: es parte del juego.
- Aceptar el riesgo desde la entrada (una ballena puede invalidar el analisis con un market order).
- Foco en charts y setups, NO en la plata.
- Con sistema: tomar TODOS los trades validos (si no, entran las emociones).
- Journal para analizar tambien emociones. Trend within trend: seguir el timeframe de
  la estrategia, no moverse de TFs.
- SIEMPRE SL. Antes de entrar, definir donde se invalida la teoria y poner el SL ahi.
- Ver donde estan los SL de los demas (donde haran mecha para liquidar) y no ser parte.
- Tip emocional: hacer TP1 rapido para dejar de preocuparse.
- NO operar tambien es una posicion. El trader tiene 2 trabajos: analisis tecnico
  y tomar o no trades. La idea es tener las probabilidades de mi lado.

================================================================
MANUAL DE BIAS - identificar tendencias (HTF Market Structure)
================================================================
- Priorizar y seguir la tendencia. Nunca short en soporte ni long en resistencia.
- Buscar long en soportes HTF, short en resistencias HTF.
- Si se opera contra tendencia, bookear TP rapido para proteger.
- No cambiar el bias sin signo de debilidad de la tendencia actual, o sin confirmacion en HTF.
- Si rompe el ultimo Higher High: indicio de cambio de tendencia, pero lo correcto es
  operar con la tendencia hasta confirmar.
- Tip: Volume Profile en HTF.

================================================================
CHECKLIST A+ MAESTRO (como combino Elliott macro + Order Flow micro)
================================================================
El agente solo deberia marcar A+ si se cumple la mayoria de esto:

CONTEXTO (HTF: 1D / 4H / 1H) - Elliott + estructura
[] Tendencia HTF clara (estructura de altos/bajos define direccion).
[] Conteo Elliott claro (no ambiguo). En que onda estoy?
[] Entrada buscada en zona de interes: fin de onda 2 (-> onda 3) o fin de onda 4 (-> onda 5),
   o fin de onda C (-> nuevo impulso). Confluencia con Fib (golden pocket 0.618-0.66).
[] POI HTF identificado (onda 2/4, cluster, OB, zona de oferta/demanda).
[] Operar A FAVOR de la tendencia HTF. No long en resistencia, no short en soporte.

EJECUCION (LTF: 15m) - Order Flow
[] Patron de entrada en el POI: absorcion / drenaje de delta / toma de liquidez / retest OB.
[] Delta y CVD a favor de la direccion.
[] Volumen > media en la zona clave (sin volumen = posible fakeout).
[] OI confirmando (sube con el movimiento direccional). Funding no extremo.
[] SFP confirmado si aplica (OI baja, delta negativo, volumen en mecha, traders atrapados).

GESTION
[] SL donde se invalida el conteo / bajo la zona de absorcion u OB.
[] R:R minimo aceptable (idealmente >= 2). TP1 rapido, resto a la liquidez del otro lado.

REGLA DE ORO: si el conteo no es claro o el order flow no confirma, NO operar.

================================================================
DATOS DEL CHECKLIST (que mide la plataforma y que no)
================================================================
Medido: tendencia (swings), POI e invalidacion (contexto), delta/CVD (ATAS o aggTrades),
volumen vs media, OI y funding (Binance), R:R, gatillo footprint ATAS (absorcion / delta
reversal / flip), liquidaciones (hipotesis, no puntua). NO medido: claridad del conteo
Elliott (lo decide el trader). Diccionario: `datos-requeridos.md`.
Confiabilidad: cada alerta queda en el signal ledger y se resuelve sola; ver
`notes/research/confiabilidad-win-rate.md` antes de cambiar umbrales.
