# Elder — Sistema de la Triple Pantalla

Fuente: Vivir del Trading (Alexander Elder), cap. sistemas de trading. Método propio de
Elder que combina varias temporalidades y filtra indicadores contradictorios. Es la
base conceptual del multi-timeframe del helper.

## La metáfora de las tres olas (marea / ola / onda)

Robert Rhea (Teoría de Dow) distinguía marea, ola y onda. Elder: operá en la dirección
de la MAREA (tendencia mayor), usando la OLA (contra-tendencia intermedia) para
temporizar, y la ONDA para el gatillo fino. Cada pantalla usa una temporalidad ~5x
mayor/menor que la siguiente.

## Primera pantalla — la marea (tendencia)

En la temporalidad superior (ej. 1h respecto a 15m; semanal respecto a diario) definí
la tendencia con un indicador seguidor: pendiente de la EMA o del histograma del MACD.
- Marea sube → sólo buscás LONGS.
- Marea baja → sólo buscás SHORTS.
Nunca operar contra la marea. (= filtro `trend_alignment` del motor).

## Segunda pantalla — la ola (oscilador para temporizar)

En la temporalidad intermedia, usá un OSCILADOR (estocástico, Force Index, %R) para
encontrar el retroceso CONTRA la marea:
- En marea alcista: comprar cuando el oscilador está SOBREVENDIDO (la ola baja da la
  oportunidad). NO comprar sobrecomprado.
- En marea bajista: vender cuando el oscilador está SOBRECOMPRADO.
Esto = comprar en el POI/retroceso, no perseguir. Enlaza con el golden pocket de
Elliott ([[elliott-03-fibonacci-ratios]]) y el LPS de Wyckoff.

## Tercera pantalla — la onda (gatillo de entrada)

En la temporalidad menor (ej. 1m), entrada fina con un trailing buy-stop (long): colocás
la orden un tick por encima del máximo de la vela previa, para que sólo se active si el
precio confirma en tu dirección. En cripto/OF = el gatillo de order flow (sweep+reclaim,
delta a favor). Define entry + stop ajustado.

## Traducción al helper

| Pantalla | Elder | Helper |
|----------|-------|--------|
| 1ª marea | tendencia TF superior | 1h / `trend_alignment`, contexto BTC |
| 2ª ola | oscilador en retroceso | POI / fib / RSI en 15m-5m |
| 3ª onda | gatillo buy-stop | order flow 1m (`of_confirmation_at_poi`) |

Alineación de las 3 = setup de máxima probabilidad (igual que Wyckoff: 3 plazos a favor).
