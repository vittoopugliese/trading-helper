# Fractales en los mercados financieros

Fuente: Tesina "Fractales en los mercados financieros". Marco teórico que justifica por
qué el análisis por estructura (Elliott, Wyckoff, order flow) funciona en TODAS las
temporalidades: el mercado es auto-similar.

## Autosimilitud y auto-afinidad

Un objeto fractal revela un parecido con la figura de conjunto en cualquier escala:
la estructura se repite. Los precios son **auto-afines**: al cambiar la escala de tiempo,
el patrón se parece pero no es idéntico (escalamiento anisotrópico: el eje precio y el
eje tiempo escalan distinto). Por eso una vela de 1m y una de 1h muestran las mismas
estructuras (impulsos, rangos, sweeps) → el patrón de Elliott es fractal por definición.

## El mercado NO es gaussiano

El modelo clásico (paseo aleatorio, distribución normal) es falso empíricamente. Los
retornos reales presentan:
- **Distribuciones leptocúrticas con colas gruesas:** los eventos extremos (crashes,
  pumps) ocurren mucho más seguido de lo que predice la normal → el riesgo de cola es
  real. Justifica stops duros y gestión estricta (regla 2%/6%).
- **Invarianza a escala (Pareto-Lévy):** la volatilidad se agrupa (clustering); tras
  movimientos grandes vienen movimientos grandes.

## Exponente de Hurst (H) y memoria del mercado

Análisis R/S (rango reescalado) estima H, que mide la MEMORIA de la serie:
- **H = 0.5:** serie aleatoria, sin memoria (mercado eficiente puro).
- **H > 0.5:** serie PERSISTENTE / con tendencia: si subió, tiende a seguir subiendo.
  Los mercados tendenciales tienen H > 0.5 → seguir tendencia tiene edge.
- **H < 0.5:** serie ANTIPERSISTENTE / reversora a la media: buena para operar rangos
  (reversión en los bordes).

## Dimensión fractal

Relacionada con H (D ≈ 2 − H). Mide cuán "rugosa" es la serie. Más rugosidad = más
ruido = más difícil operar (mejor bajar tamaño o subir de temporalidad).

## Aplicación al helper

- Justifica el análisis MULTI-TIMEFRAME: la misma lógica de setup aplica en 1m..1h.
- Colas gruesas → nunca subestimar el riesgo de cola: el tope $21 y los stops por
  estructura no son opcionales.
- Idea avanzada: un H estimado por símbolo indicaría si conviene modo "tendencia" (H>0.5,
  seguir el impulso) o modo "rango" (H<0.5, fade de los extremos). Posible métrica futura.
