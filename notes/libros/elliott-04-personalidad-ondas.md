# Elliott — Personalidad y volumen de cada onda

Fuente: Librocompleto Elliott (personalidad de las ondas). Clave para cruzar Elliott
con ORDER FLOW: cada onda tiene una firma de volumen/sentimiento característica.

## Onda 1

Nace del pesimismo extremo tras la corrección previa. Movimiento inicial, poca
convicción, volumen moderado. Suele confundirse con un rebote más de la tendencia
bajista. Difícil de operar en tiempo real.

## Onda 2

Retrocede gran parte de la 1 con poco volumen (la venta se agota). El sentimiento
sigue bajista → "esto vuelve a caer". Es donde se arma el POI de compra. **Order
flow: el volumen decrece hacia el final de la 2; buscar absorción / secado de venta**
en el golden pocket antes de entrar a la 3.

## Onda 3 — la mejor

La más fuerte, extensa y con MAYOR volumen. Rompe niveles, aparecen gaps, el
sentimiento gira a alcista. Aquí el order flow confirma con delta/CVD fuerte a favor,
volumen spike y OI subiendo. Es la onda que hay que capturar. No pelearla.

## Onda 4

Corrección lateral y aburrida, volumen bajo. Consolidación. Alterna en forma con la
2. Superficial (23.6%–38.2%). Zona para reposicionar hacia la 5.

## Onda 5 — cuidado

Nuevo máximo pero con MENOS fuerza que la 3: volumen inferior y **divergencia bajista
de momentum/CVD** (precio hace máximo, el CVD/RSI no). Sentimiento eufórico, retail
comprando arriba. Señal de agotamiento → preparar la corrección A-B-C. En scalp/swing:
si estás en onda 5, exigir divergencia de CVD como confirmación de que se agota.

## Aplicación directa al motor

- El campo `wave` del contexto ya modula la búsqueda: onda 2 → "fin onda 2 entrada
  onda 3 golden pocket"; onda 5 → "divergencia CVD distribución". Esta nota da el
  "por qué" que Claude cita en el análisis.
- Regla práctica: **volumen/CVD debe crecer en 1-3-5 a favor y secarse en 2-4.** Si
  no pasa, el conteo es sospechoso.
