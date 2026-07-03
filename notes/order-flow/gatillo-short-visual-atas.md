# Gatillo SHORT visual en ATAS — checklist mental (espejo del long)

## Que es
Las 4 señales visuales para que mi mente diga "estamos en gatillo de short".
Es el espejo de `gatillo-long-visual-atas.md`. Patron base: drenaje de delta /
rechazo en resistencia / sweep de maximos + reclaim bajista (longs atrapados).
Ver `entry-patterns.md` y `sources/SETUP_RULE_PATRON_ENTRADA.txt`.

REGLA DE BIAS: nunca short en soporte. Short SOLO en resistencia HTF, a favor de
tendencia bajista (o fade de rebote contra-tendencia con TP rapido).

## Las 4 señales visuales (EN ORDEN)

1) **PRECIO — llego a MI linea de arriba**
   - El precio sube y toca la resistencia (techo de caja, pdHigh, POC superior, swing high).
   - "No shorteo hasta que el precio toca mi techo." Sin esto, no hay trade.

2) **LA MECHA — pincha arriba y vuelve**
   - Una vela clava POR ENCIMA de la linea y CIERRA abajo otra vez (mecha larga arriba).
   - "Fue a robar stops de los shorts / liquidar longs y volvio." = sweep de maximos.

3) **VOLUMEN — barra gigante**
   - Barra de volumen MUY por encima de la media, justo en la mecha del techo.
   - "Sin barra gigante en el techo, no me lo creo."

4) **DELTA — cambia de VERDE a ROJO** (drenaje de delta)
   - Lo mas importante. Venia verde fuerte (compra subiendo) y de golpe el delta deja
     de crecer y se pone rojo (vendedores toman control).
   - Visual del DRENAJE: los numeros de delta verde se achican (1900, 2000 -> 400, 600
     -> negativos). Doble techo en el delta: precio sube pero no hay compradores nuevos.

## La frase para memorizar
> "Toca mi techo -> pincha arriba y vuelve -> barra de volumen gigante -> el delta se pone rojo."

Cuando las 4 pasan JUNTAS y EN MI RESISTENCIA = ENTRO SHORT.

## Lo que me dice que NO (frenar)
- Pincha el techo pero VOLUMEN CHICO -> ruptura debil, puede seguir subiendo. Esperar.
- El delta sigue VERDE fuerte aunque toque la linea -> compradores mandan. NO shortear (knife).
- Precio en el aire, lejos de la resistencia -> no hay POI. No hay trade.
- "Delta leading" a favor de la compra en el techo -> el precio puede romper al alza.

## La logica (trampa al alza)
El mercado SUBE a buscar liquidez sobre la resistencia (mecha + volumen), liquida
longs / saca shorts, y cuando termino SE DA VUELTA (delta rojo). Entro cuando la
trampa ya se cerro, no mientras sigue subiendo.

## Patron especial: LONGS ATRAPADOS (trapped longs)
- Barrido del maximo + OI SUBIENDO (longs nuevos entrando arriba) + perdida del nivel
  + delta rojo / buy_ratio < 0.45 -> liquidacion a la baja. Es el short de mayor calidad.
- Espejo del "trapped shorts" del lado long.

## Gestion
- Entrada conservadora: esperar vela que cierre con delta- confirmando el rechazo.
- SL estructural ARRIBA del swing high / de la mecha del sweep (no scalp-tight — ver Error #3).
- Target: liquidez del otro lado (soporte / low previo). TP1 rapido.
- A favor de tendencia bajista (onda C/3) = conviccion alta. Fade de rebote = size reducido.

## Datos OF que confirman short (umbrales del sistema)
- buy_ratio < ~0.45 (venta agresiva).
- delta de la vela negativo y creciente en rojo.
- book_bias bearish / mas asks que bids.
- OI subiendo mientras el precio cae = shorts nuevos con conviccion (bearish real).
- CVD divergencia bearish: precio higher high pero CVD lower high = long squeeze a la baja.

## Casos de referencia (de errores-comunes.md)
- 21-jun: short techo box ~68.50 con rechazo + delta flip 1m/5m/15m -> cayo a 65.20 (-5%).
  Direccion PERFECTA. Fallo: SL scalp-tight (Error #3). El gatillo visual estuvo OK.
- 22-jun: short 69.25, techo zona 69.55, delta negativo -> cayo a 67.91 (-2%).
  Gatillo OK; fallo: salida prematura por ruido 1m (Error #5).
Leccion cruzada: el gatillo SHORT viene funcionando; lo que falla es la GESTION del SL.
