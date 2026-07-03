# Notas de conversación previa — handoff para sesión nueva

> **Fecha:** 20–22 jun 2026 · **Par principal:** HYPEUSDT · **Objetivo del trader:** ser rentable con sistema (Elliott + OF), superar bloqueos psicológicos en ejecución.

---

## 1. Quién sos y cómo operás

- **Fortaleza:** Elliott Waves + Fib (nivel ~5/10, en aprendizaje). Conteos, golden pocket, invalidaciones.
- **En desarrollo:** Order flow / smart money (~menos experiencia). Usás **ATAS** (footprint, cluster stats, CVD velas, OI) + **TradingView** (price action / Elliott).
- **El asistente complementa:** order flow vía Binance API (aggTrades, depth, OI, funding, L/S ratio), scoring A+, R:R, sizing, psicología, watcher con beep.
- **Workflow acordado:**
  - Vos: conteo Elliott + Fib en TV; footprint en ATAS cuando estés en el POI.
  - Asistente: OF agregado, alertas, **ENTRADA MECÁNICA** cuando hay confluencia, no suavizar con “tal vez”.
  - Imágenes del chart: seguir enviándolas — el asistente las usa para validar conteo.
- **Idioma:** español. **No commitear** salvo que lo pidas.

---

## 2. Cuenta y gestión de riesgo

| Parámetro | Valor acordado |
|-----------|----------------|
| Cuenta | **$10.000** |
| Riesgo por trade | **3%** ($300) — en `config.yaml` default 2%; en práctica usás 3% |
| Leverage | x10 o x5 |
| R:R mínimo | 2 · objetivo 3 |
| Fórmulas | `Notional = Riesgo$ / SL%` · `Margin = Notional / leverage` |
| SL | **Siempre estructural** (swing high/low). Si duele → **bajar size**, no acortar stop |
| TP1 | Parcial rápido → trailing SL a BE |

**Regla general (lección de los últimos días):** la lectura del mercado es buena; falla la **ejecución** (mover stop, no entrar por miedo, salir por ruido 1m). Plan mecánico antes, ejecución mecánica después.

---

## 3. Evolución del conteo HYPE (importante — varios conteos invalidados)

| Fecha / fase | Conteo | Qué pasó |
|--------------|--------|----------|
| Inicio conv. | Onda 2 → long GP 67.5–68.5 hacia W3 | Precio no retrocedió tan profundo |
| Revisión | Triángulo W4 → thrust W5 | Invalidado al romper abajo 68.30 |
| Post-flush | ABC bajista desde 71.43 (C → 66.58) | Reconteo |
| **Conteo vigente (22-jun)** | **W1 macro 65.20 → 69.55** · **sub-W2 de W3** · long en **GP menor 66.8–67.0** | Mínimo real del retroceso **~67.01** (borde GP, no 66.8 exacto). Subió a **69.55** (+3.5%) — long no tomado (Error #4) |
| Short 22-jun tarde | Pullback desde techo **~69.25** hacia GP | **Dirección correcta** → dump a **~67.91 (-2%)**. Stop movido / salida por ruido 15:31 BA (Error #5) |

### Niveles Fib clave (W1: 65.20 → 69.55)

| Nivel | Precio |
|-------|--------|
| GP menor (entry long planificado) | **66.80 – 67.00** |
| GP mayor | 66.647 – 66.828 |
| 0.5 | ~67.16 – 67.34 |
| 0.382 | **67.853** |
| W1 high | **69.55** |
| Invalidación MCO (analista externo) | **65.66** |
| SL long sugerido | 66.45 (estructural) o 65.66 (MCO hard) |
| Targets long | 67.85 · 69.55 · 72+ |
| Resistencias macro | 75 · 76–77 · 87 |

### Contexto analista MCO (externo, no verificado en vivo al cierre)

- **HYPE:** 50% en $66.29; soporte $66.29 / $65.66 / $53; bull necesita **>$75**; break **<$65.66** → extensión hacia $53.
- **BTC:** 1-2 válido; soporte $62,656–63,610; break **>$65,498** → W3 probable ($67,245 / $70,000).
- **No hay** `context/BTCUSDT.yaml` todavía — crear si operás BTC.

### Estado al **cierre de esta conversación**

- Precio HYPE **~67.9** tras dump desde 69.25.
- **Acercándose al GP 66.8–67.0** — posible zona de flip long (sub-W2 / fin de pullback).
- **`context/HYPEUSDT.yaml` está desactualizado** en `wave`/`notes` (watcher aún mostraba texto viejo “ABC-C / W2 profunda”). **Actualizar al abrir sesión nueva** con precio, conteo y bias actuales.

---

## 4. Errores documentados (leer siempre)

Archivo completo: `notes/setup-rules/errores-comunes.md`

| # | Fecha | Resumen |
|---|-------|---------|
| 1 | 20-jun | Mover stop hacia adentro (short onda C ~70.75) |
| 2 | — | No trazar targets Fib de la onda antes de entrar |
| 3 | 21-jun | Stop scalp-tight en short macro 68.50 → sacado antes de -5% a 65.20 |
| 4 | 22-jun | No entrar long válido en GP por miedo / tick exacto (+3.5% perdidos) |
| 5 | 22-jun | Mover stop / salir short 69.25 por ruido 1m (-2% perdidos; dirección OK) |

**Patrón a romper:** stop/salida prematura → parálisis en el siguiente setup válido → ver el movimiento irse **sin vos**. Tres días seguidos con **dirección correcta, gestión incorrecta**.

Checklist psicológico actualizado al final de `errores-comunes.md`.

---

## 5. Regla ENTRADA MECÁNICA (persistente)

Archivo: `.cursor/rules/entrada-mecanica.mdc` (`alwaysApply: true`)

Decir **ENTRADA MECÁNICA** cuando ≥3 de:
- Precio en POI/GP (zona, no tick)
- buy_ratio > 0.6 long / < 0.4 short + delta
- Patrón OF: absorción, sweep_reclaim, delta_confirmation
- Multi-TF alineado
- Elliott a favor
- SL estructural, R:R ≥ 2

**Dentro de un trade:** NO recomendar salir por flip de 1m. Solo invalidación real (ruptura SL estructural).

---

## 6. Herramientas del proyecto (trading-helper)

### Comandos útiles

```bash
# Watcher scalp + beep (ajustar bias/timeframe)
python scripts/hype_watch.py HYPEUSDT --scalp --bias long --timeframe 1m --interval 30
python scripts/hype_watch.py HYPEUSDT --scalp --bias both --timeframe 1m --interval 30

# Análisis CLI
python main.py analyze HYPEUSDT 15m scalp

# Journal
python main.py journal
python main.py log-trade ...
```

### Watcher (`scripts/hype_watch.py`)

- Multi-TF: 1m / 5m / 15m
- Beep + toast Windows + `data/alerts.log`
- Lee `context/HYPEUSDT.yaml` para POI/invalidación/targets
- **Alertas scalp:** `absorption_at_support`, `sweep_reclaim_bottom`, `rejection_at_resistance`, `delta_flip_after_bounce`, etc.
- **Nuevo (22-jun):** `trapped_shorts`, `trapped_shorts_building`, `trapped_longs` (OI 5m + barrido + delta)
- Muestra **Open Interest 20m** en cada tick
- **Limitación conocida:** alertas 15m (`sweep_reclaim`, `trapped_shorts`) pueden ser **rezagadas** (refieren a barridos de horas atrás). No usar para salir de un scalp en otro nivel.

### MCP / contexto

- `context/HYPEUSDT.yaml` — bias, wave, POI, invalidation, targets, fib
- MCP tools: `get_context`, `set_context`, `analyze_with_levels`, `log_trade`, `journal_stats`
- Notas indexadas en ChromaDB (`notes/`)

### Mapeo ATAS ↔ sistema

| ATAS | Sistema |
|------|---------|
| Footprint / clusters en mecha | Absorción, POC atrapado, traders atrapados |
| Cluster Statistics (delta por vela) | `recent_delta`, d1/d2/d3 |
| CVD velas | `cvd`, `cvd_slope`, `cvd_divergence` |
| OI velas azules | `recent_oi_rise()` — posiciones nuevas vs cierres |

**Señal ATAS en low onda 2 (22-jun):** vela roja barre GP + delta muy negativo en el mínimo + OI sube (shorts nuevos) + CVD gira + reclaim → **shorts atrapados → long**. El sistema detectó `sweep_reclaim_bottom` pero **no entraste**; el detector `trapped_shorts` se agregó después.

---

## 7. Trades registrados en journal (SQLite)

| ID | Dir | Entry | Result | Notas |
|----|-----|-------|--------|-------|
| — | short | 68.50 | loss | Error #3, stop corto |
| — | long | 66.92 | missed | Error #4a |
| — | long | 67.01 | missed | Error #4b, +3.5% |
| — | short | 69.25 | loss (gestión) | Error #5, -2% movimiento correcto |

Consultar: `python main.py journal` o MCP `journal_stats`.

---

## 8. Qué NO está documentado en otros archivos (solo en esta conv.)

1. **Decisión de abrir ATAS en paralelo** mientras chateás — footprint en POI para ver atrapados tick a tick; el asistente cubre OF agregado + OI cada 30s.
2. **GP “shallow” en W3 fuerte:** sub-W2 puede defender **67.0–67.34 (0.5–0.618)** sin llegar a 66.8–67.0 — debatido, no resuelto si mover POI del watcher.
3. **Short 69.25 era contra-tendencia macro (W3 long)** pero válido como scalp de pullback hacia GP; plan era cerrar short y **flip long en 66.8–67.0**.
4. **Error del asistente:** recomendó cortar short por flip buy_ratio 1m — quedó prohibido en regla `.cursor/rules/entrada-mecanica.mdc`.
5. **Debate Cursor vs VS Code + API barata** — sin resolver; no afecta trading.
6. **Fase del trader:** aprendizaje, no rentable aún; prioridad = sistema + journaling + psicología (ver `notes/setup-rules/a-plus-checklist.md` fases 1–5).
7. **Watcher al cierre:** puede estar corriendo o no — verificar procesos python antes de arrancar otro (evitar duplicados).

---

## 9. Checklist al iniciar conversación nueva

1. Pedir **precio actual** o mirar chart — actualizar `context/HYPEUSDT.yaml`.
2. Confirmar **conteo Elliott vigente** (¿seguimos sub-W2 de W3 hacia GP? ¿invalidó 69.55?).
3. Revisar `notes/setup-rules/errores-comunes.md` checklist pre-trade.
4. Arrancar watcher si querés alertas: `--bias both` si operás short pullbacks + long en GP.
5. Recordar: **POI = zona** · **SL estructural** · **ENTRADA MECÁNICA** cuando checklist completo.

---

## 10. Archivos clave (rutas)

```
context/HYPEUSDT.yaml          # Contexto activo (ACTUALIZAR)
notes/setup-rules/errores-comunes.md
notes/setup-rules/a-plus-checklist.md
notes/order-flow/entry-patterns.md
.cursor/rules/entrada-mecanica.mdc
scripts/hype_watch.py
agent/scalp_alerts.py          # trapped_shorts / trapped_longs
data/alerts.log
config.yaml
```

---

*Generado al cierre de sesión 22-jun-2026 para handoff a chat nuevo.*
