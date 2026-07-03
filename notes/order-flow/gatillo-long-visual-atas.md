# Gatillo LONG visual en ATAS — checklist mental + casos reales HYPE

## Que es
Las 4 señales visuales que mis ojos tienen que ver en ATAS, en orden, para que mi
mente diga "estamos en gatillo de long". Es la version simple del patron de
absorcion / sweep + reclaim (ver `entry-patterns.md` y `sources/SETUP_RULE_PATRON_ENTRADA.txt`).

## Las 4 señales visuales (EN ORDEN)

1) **PRECIO — llego a MI linea**
   - El precio toca la zona verde (pdLow / mi caja / POC previo).
   - "No hago nada hasta que el precio toca mi zona." Sin esto, no hay trade.

2) **LA MECHA — pincha y rebota**
   - Una vela clava por DEBAJO de la linea y CIERRA arriba otra vez (mecha larga abajo).
   - "Fue a robar stops y volvio." = barrido / toma de liquidez (sweep).

3) **VOLUMEN — barra gigante**
   - En el histograma de volumen, una barra MUCHO mas alta que las demas, justo en la mecha.
   - "Sin barra gigante, no me lo creo." El volumen confirma que hubo pelea real.

4) **DELTA — cambia de ROJO a VERDE**
   - Lo mas importante. Venia rojo fuerte (vendedores) y de golpe aparece verde fuerte (compradores).
   - "El rojo se rinde y entra el verde." Ese flip de color es el gatillo.

## La frase para memorizar
> "Toca mi linea -> pincha y vuelve -> barra de volumen gigante -> el delta se pone verde."

Cuando las 4 pasan JUNTAS y EN MI ZONA = ENTRO LONG.

## Lo que me dice que NO (frenar)
- Pincha la linea pero VOLUMEN CHICO -> liquidez falsa, va a volver. No entrar.
- El delta sigue ROJO aunque el precio aguante -> todavia mandan vendedores. Esperar el verde.
- Precio en el aire, LEJOS de la linea verde -> no hay POI. No hay trade.
- Delta "leading" en contra dentro del POI (mucha agresion en contra) -> el precio retrocede.

## La logica (como trampa)
El mercado BAJA a robar los stops debajo de mi verde (mecha + volumen). Cuando los
agarro, SE DA VUELTA (delta verde). Yo entro cuando la trampa YA se cerro, no antes.

## Gestion
- Entrada conservadora: esperar que la vela siguiente cierre con delta+ confirmando.
- SL por debajo de la mecha de absorcion / del minimo barrido.
- Target: liquidez mas cercana del otro lado. TP1 rapido.
- Contra-tendencia HTF (onda C bajista): size reducido (~50%).

---

## CASOS REALES DOCUMENTADOS (HYPE, order flow Binance)

### Caso 1 — Jun 24 2026, 13:45 (UTC-3): ABSORCION en borde de POI
Rebote desde low 59.25 (borde de la caja verde + POC previo).

5m:
```
13:40L  C=59.42  d=-74,536   v=177k   <- climax vendedor (impulso C)
13:45L  L=59.25  C=59.28  d=+15,970   <- DELTA FLIP en el minimo
13:55L  C=59.63  d=+18,828            <- continuacion compradora
```
1m (detalle del giro):
```
13:42  d=-27,019 | 13:43  d=-23,604   <- venta agresiva rompiendo
13:47  C=59.29  d=+29,705  v=80k VOLSPIKE  <- absorcion confirmada
13:58  d=+25,488 | 13:59  d=+7,601         <- buyers al mando
```
Confluencias: absorcion (cierra y aguanta) + delta flip (-74k -> +16k/+29k) +
volumen > media en el giro + POI previo sostuvo. Calidad: buena.

### Caso 2 — Jun 25 2026, 11:00 (UTC-3): SWEEP + RECLAIM (shorts atrapados) — A+
Flush violento a 58.50 (barrido POR DEBAJO de la caja) y reversal brutal.

5m:
```
10:55L  L=58.50  C=58.87  d=-88,249   v=553k   <- capitulacion / sweep
11:00L  O=58.88 H=60.48 C=60.00 d=+93,579  v=454k VOLSPIKE  <- REVERSAL
11:05L  C=60.74  d=+55,133            <- continuacion
```
1m (corazon del giro):
```
10:58  d=-40,688  v=119k VOLSPIKE   <- venta climax
10:59  L=58.50  d=-38,129  v=207k VOLSPIKE  <- barrido del minimo
11:00  O=58.88 H=60.00 C=59.98  d=+81,856  v=172k VOLSPIKE  <- +82k en UNA vela
11:07  d=+20,634 | 11:11  d=+28,484         <- buyers sostienen
```
Confluencias: sweep real bajo soporte (vol 553k, el mas alto del tramo) +
absorcion/shorts atrapados (-88k -> +93k) + delta flip masivo (-40k -> +82k en 1m) +
vela envolvente alcista sobre volumen pico. Calidad: A+ (las 3 banderas juntas).

### Como se ve en ATAS 5m (lectura visual del Caso 1 — Jun 24)
Asi se veian las 4 señales en el grafico 5m de ATAS (candles + delta/CVD al medio + volumen abajo):

1) PRECIO en la linea verde: el precio cae desde ~63 y ATERRIZA justo en la linea
   verde (pdLow ~59.6). Las velas se frenan en seco contra la raya, no la cruzan de largo.
2) La mecha / el freno: en la zona ves velas con mechas abajo rechazando. La vela roja
   GORDA (13:40, d=-74k, v=177k) es la ultima empujada vendedora -> en la siguiente frena y voltea.
3) VOLUMEN barras gigantes: abajo, las barras MAS ALTAS de todo el tramo justo en el
   fondo (177k, 169k, 183k). Grita "aca hubo pelea real, compran todo lo que venden".
4) DELTA de ROJO a VERDE: en la linea del medio, venia cayendo en rojo toda la bajada
   y EN EL FONDO deja de bajar y arranca a subir. Visual: la linea roja toca fondo y
   se da vuelta hacia arriba, justo cuando el precio esta en la verde.

Donde entrar (conservadora, NO en el minimo exacto):
- Gatillo: vela 5m que cierra con delta+ verde sobre la linea verde (13:45 / 13:55).
- Entry: ~59.5-59.6 (el reclaim). SL: bajo 59.25 (debajo de la mecha de absorcion).
- TP1: liquidez/resistencia mas cercana arriba.

Foto mental del 24: "Precio cae a la verde -> vela roja gigante de climax -> barras de
volumen enormes -> el delta deja de caer y se pone verde -> entro en la vela de
confirmacion, SL bajo la mecha."

### Comparativa
| | Jun 24 13:45 | Jun 25 11:00 |
|---|---|---|
| Patron | Absorcion en borde de POI | Sweep + reclaim (shorts atrapados) |
| Low | 59.25 (borde caja) | 58.50 (barrido bajo la caja) |
| Delta flip | -74k -> +16k/+29k | -88k -> +93k |
| Vol pico | 80k (1m) | 207k -> 172k (1m) |
| Calidad | Buena | A+ |

### Edge repetible (lo que tienen en comun)
Climax de delta negativo + caja verde/POC como POI + DELTA FLIP a positivo con
volumen sobre la media. Las dos veces el mercado fue a buscar liquidez en/bajo mi
zona y revirtio. El 25 fue superior porque hubo BARRIDO REAL bajo el soporte antes
del reclaim. Sin Elliott claro, el order flow por si solo dio la señal: esperar el
delta flip confirmado dentro de la caja era la entrada.

## Nota personal
Contexto HTF en ambos casos: onda C de (4) bajista (ver `context/HYPEUSDT.yaml`).
Por eso fueron longs contra-tendencia -> TP rapido y size reducido. El target macro
seguia siendo mas abajo (~53), pero el rebote de absorcion/sweep era operable.
