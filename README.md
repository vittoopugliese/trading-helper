# Trading Helper

Analisis asistido de crypto futures — Elliott Waves + Order Flow + scoring A+.

## Superficies

| Comando | Uso |
|---------|-----|
| `python main.py analyze HYPEUSDT 15m scalp` | Analisis CLI |
| `python main.py repl` | Terminal interactivo |
| `python main.py mcp` | Servidor MCP para Cursor |
| `scripts/hype_watch.py HYPEUSDT --scalp` | Watcher terminal |
| **`python main.py web`** | **Trading Cockpit Web (localhost)** |
| `python main.py history-download` | Historico 1m completo HYPE/BTC (resume + checksums) |
| `python main.py history-status` | Cobertura y gaps del dataset local |
| `python main.py backtest-suite` | Grilla de setups de volumen + export ATAS |

## Como levantar el proyecto (guia rapida)

Desde la raiz del repo, en Windows:

```powershell
# 1. Activar el venv (Python 3.14)
.venv\Scripts\activate

# 2. Instalar dependencias (necesario tras cada cambio en requirements.txt)
python -m pip install -r requirements.txt

# 3. Levantar el Trading Cockpit Web
python main.py web
```

Abre solo el browser en `http://127.0.0.1:8780/`. Si no queres que abra:
`python main.py web --no-browser`. Puerto/host se cambian en `config.yaml` → `web`.

**Primer uso:**

1. En **Cockpit** elegi el simbolo (ej. `HYPEUSDT`) y toca **Refresh**.
2. Toca **Start** en el panel Watcher para las alertas live (SSE, cada 30s).
3. En **Settings** pega tu API key de Claude (`sk-ant-...`) → se guarda en `.env`
   local (ya ignorado por git, nunca sale de tu PC) y habilita el tab **Chat**.
4. El contexto Elliott (POI, invalidacion, targets, screenshot del chart) se
   edita desde el mismo Cockpit o directo en `context/SYMBOL.yaml`.

Para frenar el servidor: `Ctrl+C` en la terminal.

## Trading Cockpit Web

Interfaz visual complementaria al CLI. No reemplaza el terminal ni Cursor MCP.

Tabs disponibles en `http://127.0.0.1:8780/`:

- **Cockpit**: precio live, veredicto A+ / ENTRADA MECANICA, multi-TF (1m/5m/15m/1h), banner de correlacion BTC, sizing, watcher Start/Stop con feed de alertas (SSE), contexto Elliott editable, upload de chart, journal (read-only), reglas del gatillo (4 señales)
- **Manual**: manual maestro (Trading.html)
- **Referencia**: Order Flow + Elliott roadmaps
- **Chat**: Claude BYOK con botones rapidos ("Analiza 15m", "Estoy ready", "Calcular sizing", "Registrar trade") — modelo `claude-opus-4-8`
- **Settings**: cuenta $300 x10, max riesgo $21, API key

Config en `config.yaml` → seccion `web`:

```yaml
web:
  host: 127.0.0.1
  port: 8780
  open_browser: true
  notify_desktop: false   # true = toast de Windows ademas de alertas en la web
```

### Endpoints principales de la API

| Endpoint | Que hace |
|----------|----------|
| `GET /api/health` | Ping del servidor |
| `GET /api/ticker/{sym}` | Precio live |
| `GET /api/analyze/{sym}?timeframe=15m&trade_type=scalp` | Analisis + veredicto A+ |
| `GET /api/multi-tf/{sym}` | Order flow por timeframe |
| `GET /api/btc-context` | Contexto macro BTC 1h |
| `POST /api/watcher/start` / `stop` / `GET status` / `GET last-scan` | Control del watcher |
| `GET /api/events` | Stream SSE (scans + alertas) |
| `GET/PUT /api/context/{sym}` + `POST .../chart` | Contexto Elliott + screenshot |
| `GET /api/journal/stats` / `recent` / `POST /api/journal/trade` | Journal |
| `POST /api/chat` (SSE) | Chat BYOK con Claude |
| `POST /api/atas/ingest` | Solo el indicador ATAS local (header `X-Atas-Bridge: 1`) |
| `GET /api/atas/status` / `snapshot/{sym}` / `bars/{sym}` | Feed ATAS: estado, footprint derivado, velas |
| `GET /api/liquidations/{sym}` | Liquidaciones Bybit + Binance por lado, z-score 15m |
| `GET /api/signals/stats` / `recent` | Ledger forward: WR (IC 95%) y expectancy por feature |
| `GET/PUT /api/settings` | Cuenta, riesgo, API key |

## Estado del proyecto (verificacion 2026-07-03)

El dashboard web del planning (`notes/cursor_hype_an_lisis_y_watcher.md`) quedo
**completo y verificado end-to-end** con datos live de Binance. Lo que se hizo
para terminarlo:

1. **Dependencias web instaladas** — `fastapi`, `uvicorn` y `python-multipart`
   estaban en `requirements.txt` pero nunca se habian instalado en el venv,
   por lo que `python main.py web` fallaba con `ModuleNotFoundError`.
2. **Modelo del chat actualizado** — `web/chat.py` usaba
   `claude-sonnet-4-20250514` (deprecado, se retira en junio 2026); se migro a
   `claude-opus-4-8`.

Verificado funcionando con el servidor corriendo de verdad:

- **Cockpit**: precio live de HYPE, contexto BTC ("viento a favor"), analisis
  con veredicto A+ y multi-timeframe contra Binance.
- **Watcher**: arrancado desde la API, completo un ciclo de scan real (detecto
  `liquidity_sweep`, score 87% short en la zona 70.65–70.95 del contexto) y se
  detuvo correctamente. Las alertas fluyen por SSE a la web.
- **Contexto Elliott**: lee `context/HYPEUSDT.yaml` con los POI del planning
  (LONG 69.35–69.65, SHORT 70.65–70.95, invalidacion 69.10).
- **Journal**: muestra los trades registrados con win rate y avg R.
- **Chat BYOK**: sin API key responde con el mensaje de "configurala en
  Settings" en vez de romperse.
- **Compatibilidad terminal**: `main.py` (los 12 comandos) y
  `scripts/hype_watch.py` siguen funcionando tras el refactor — la web es
  complementaria, comparten `agent/watcher.py`.
- **Estaticos**: index, manual, referencias OrderFlow/Elliott, JS y CSS sirven 200.

**Pendiente opcional**: registrar trades desde la web es via chat o
`POST /api/journal/trade` — no hay formulario dedicado en la UI (el planning lo
dejaba como pregunta abierta).

## Base de conocimiento — notas de libros

`notes/libros/` contiene 21 notas destiladas de la biblioteca técnica
(`C:\Users\torib\Desktop\Imagenes\Trading\Libros`): Elliott, Wyckoff, Douglas
(psicología), Tendler (juego mental), Elder (Vivir del Trading), Fractales y el plan
SMC de BEIG. Índice humano en `notes/libros/_indice-libros.md`.

Estas notas se indexan en ChromaDB (`agent/knowledge.py`) y el pipeline de análisis
las recupera por relevancia semántica (`build_search_query` → `search_notes`) y las
inyecta en el prompt de `run_deep_analysis`. Resultado: los análisis citan Elliott +
Wyckoff + order flow + psicología de forma coherente (ver `sintesis-confluencias.md`,
que mapea las confluencias al checklist A+ y al scoring de `config.yaml`).

Reindexar tras editar/agregar notas:

```powershell
python -c "from agent import knowledge; print(knowledge.index_notes(force=True))"
```

(La extracción de texto de los PDFs se hizo con `pymupdf`, instalado en el venv sólo
para esa tarea puntual; no es dependencia de runtime.)

## ATAS + liquidaciones + confiabilidad (2026-10-12)

La plataforma **no ejecuta ordenes**: analiza, alerta y mide. (La ejecucion BingX se quito;
queda en el historial, commit `30e6351`.)

**Puente ATAS** (`atas-bridge/`, indicador C# `Trading Helper Bridge`):

```powershell
cd atas-bridge
dotnet build -c Release   # compila y copia la DLL a %APPDATA%\ATAS\Indicators
```

En ATAS: abrir el chart (ej. HYPEUSDT 5m, conector Binance) → Indicadores → *Trading Helper
Bridge* (o "Add custom indicator" y elegir `bin/Release/TradingHelperBridge.dll`). Manda cada
2 s footprint por nivel, delta, max/min delta, OI, POC/VA, big trades (>= 250 contratos,
configurable) y DOM ±1% a `http://127.0.0.1:8780/api/atas/ingest`. Con el cockpit cerrado
guarda hasta 5000 velas cerradas y las reenvia al volver. Todo queda en `data/atas.db`.

**Liquidaciones**: con `python main.py web` corriendo se graban liquidaciones de Bybit
(completas) y Binance (muestreadas) en `data/liquidations.db`. El z-score de 15 min necesita
>= 6 h de historial.

**Motor A+**: con ATAS vivo, un A+ en POI exige gatillo footprint (absorcion / delta reversal /
delta flip) o baja a B. Liquidaciones se muestran y se registran, no puntuan todavia.

**Signal ledger** (`data/signals.db`): cada alerta se guarda con su evidencia y se resuelve
sola (TP / SL / 4 h, 7 bps por lado). Panel "Señales forward" en el cockpit. Las alertas
llegan por SSE, notificacion del browser (pestaña en segundo plano) y toast de Windows
(`web.notify_desktop`).

Notas: `notes/setup-rules/datos-requeridos.md` (cada regla → dato y umbral) y
`notes/research/confiabilidad-win-rate.md` (investigacion, hipotesis H1-H3, muestra necesaria).
Guia HYPE: tab "07 · Datos & confiabilidad".

## Contexto Elliott

Editar en la web o en `context/SYMBOL.yaml`. Screenshots en `context/assets/SYMBOL/chart.*`.

## Backtesting de volumen

El research esta separado del analisis live y del journal real:

```powershell
python main.py history-download
python main.py history-status
python main.py backtest-suite
```

Los resultados quedan en `reports/backtests/` con ranking y timestamps UTC para
ATAS. Las reglas anti-lookahead y los limites de cada proxy estan documentados en
[`notes/backtesting-methodology.md`](notes/backtesting-methodology.md).
Los resultados del primer barrido completo estan en
[`notes/backtest-results-2026-09-02.md`](notes/backtest-results-2026-09-02.md).

## Cuenta y riesgo

Ver `notes/setup-rules/risk-sizing-cuenta.md` — margen != riesgo, tope $21/trade.
