"""Shared symbol watcher — scalp alerts, scan cycles, optional desktop notify."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent import market_data, metrics, scalp_alerts, setup_engine
from agent.config import PROJECT_ROOT, get_config
from agent.context import TradeContext, get_context

ALERT_LOG = PROJECT_ROOT / "data" / "alerts.log"


def notify_desktop(title: str, message: str, *, enabled: bool = True) -> None:
    """Append to alert log; optional Windows sound + toast."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {title} — {message}"

    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

    if not enabled:
        return

    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        winsound.Beep(880, 250)
        winsound.Beep(1175, 250)
    except Exception:
        print("\a", end="", flush=True)

    try:
        safe_title = title.replace("'", "")
        safe_msg = message.replace("'", "")[:200]
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null;"
            "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$x=$t.GetElementsByTagName('text');"
            f"$x.Item(0).AppendChild($t.CreateTextNode('{safe_title}')) | Out-Null;"
            f"$x.Item(1).AppendChild($t.CreateTextNode('{safe_msg}')) | Out-Null;"
            "$n=[Windows.UI.Notifications.ToastNotification]::new($t);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Trading Helper').Show($n);"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def scalp_levels_from_context(ctx: TradeContext | None) -> dict[str, tuple[float, float, float, list[float]]]:
    """Return long/short POI, invalidation, targets from context file or defaults."""
    if not ctx:
        return {
            "long": (66.50, 67.00, 66.20, [67.80, 68.30]),
            "short": (68.00, 68.50, 68.80, [67.25, 66.58]),
        }

    fib = ctx.fib or {}
    long_lo = ctx.poi_low if ctx.poi_low is not None else float(fib.get("abc_C_low", 66.58)) - 0.08
    long_hi = ctx.poi_high if ctx.poi_high is not None else long_lo + 0.50
    inv_long = ctx.invalidation if ctx.invalidation is not None else float(fib.get("abc_C_low", 66.58)) - 0.38
    long_targets = ctx.targets if ctx.targets else [67.80, 68.30]

    short_lo = float(fib.get("short_poi_low", fib.get("broken_support", 68.30) - 0.30))
    short_hi = float(fib.get("short_poi_high", short_lo + 0.50))
    inv_short = float(fib.get("short_invalidation", short_hi + 0.30))
    short_targets = [
        float(fib.get("short_target_1", 67.25)),
        float(fib.get("short_target_2", 66.58)),
    ]

    return {
        "long": (long_lo, long_hi, inv_long, long_targets),
        "short": (short_lo, short_hi, inv_short, short_targets),
    }


def build_scalp_ctx(symbol: str, bias: str, base_ctx: TradeContext | None = None) -> TradeContext:
    """Ephemeral scalp context based on desired bias."""
    levels = scalp_levels_from_context(base_ctx)
    poi_lo, poi_hi, inv, targets = levels[bias]
    wave = (base_ctx.wave if base_ctx and base_ctx.wave else None) or (
        "ABC-C / W2 profunda — rebote en soporte" if bias == "long" else "Onda C de (4) — short en resistencia"
    )
    return TradeContext(
        symbol=symbol,
        bias=bias,
        wave=wave,
        trade_type="scalp",
        poi_low=poi_lo,
        poi_high=poi_hi,
        invalidation=inv,
        targets=targets,
    )


def pick_active_bias(price: float, base_ctx: TradeContext | None) -> str:
    """Choose scalp bias by proximity to long/short POI zones."""
    levels = scalp_levels_from_context(base_ctx)
    long_lo, long_hi, _, _ = levels["long"]
    short_lo, short_hi, _, _ = levels["short"]
    long_mid = (long_lo + long_hi) / 2
    short_mid = (short_lo + short_hi) / 2
    if price <= long_hi + 0.15:
        return "long"
    if price >= short_lo - 0.15:
        return "short"
    return "long" if abs(price - long_mid) <= abs(price - short_mid) else "short"


def scan_timeframes(
    symbol: str,
    timeframes: list[str],
    base_ctx: TradeContext | None,
    biases: list[str],
) -> tuple[list, dict[str, dict]]:
    """Scan multiple TFs and biases; return alerts + summary per TF."""
    all_alerts: list = []
    summary: dict[str, dict] = {}
    for tf in timeframes:
        tf_data: dict = {"long": None, "short": None, "alerts": []}
        for b in biases:
            eval_ctx = build_scalp_ctx(symbol, b, base_ctx)
            snap = metrics.build_snapshot(symbol, tf, "scalp", trade_context=eval_ctx)
            v = setup_engine.evaluate_setup(snap, trade_context=eval_ctx)
            tf_data[b] = {
                "grade": v.grade,
                "score": v.score,
                "direction": v.direction,
                "at_poi": v.at_poi,
                "patterns": v.of_patterns,
                "trend": snap.structure.get("trend"),
                "buy_ratio": snap.order_flow.get("buy_ratio"),
                "vol_ratio": snap.indicators.get("volume_ratio"),
            }
            if b == "long":
                alerts = scalp_alerts.detect_scalp_alerts(
                    symbol,
                    tf,
                    short_resistance=None,
                    long_support=(eval_ctx.poi_low, eval_ctx.poi_high),
                    long_invalidation=eval_ctx.invalidation,
                    long_targets=tuple(eval_ctx.targets),
                )
            else:
                alerts = scalp_alerts.detect_scalp_alerts(
                    symbol,
                    tf,
                    short_resistance=(eval_ctx.poi_low, eval_ctx.poi_high),
                    short_invalidation=eval_ctx.invalidation,
                    short_targets=tuple(eval_ctx.targets),
                    long_support=None,
                )
            # POI viejo: los targets/invalidacion salen de un contexto que ya no describe
            # el mercado. La alerta se muestra pero NUNCA como "*** ENTRAR ***".
            stale = eval_ctx.has_poi() and eval_ctx.is_stale(snap.price)
            for a in alerts:
                if stale:
                    a.urgency = "watch"
                a.detail = f"[{tf}] {'[contexto viejo] ' if stale else ''}{a.detail}"
            tf_data["alerts"].extend(alerts)
            all_alerts.extend(alerts)
        summary[tf] = tf_data
    return all_alerts, summary


def mechanical_entry_flag(verdict: setup_engine.SetupVerdict, hunt_biases: list[str]) -> bool:
    """True when checklist supports ENTRADA MECANICA."""
    of = verdict.order_flow if hasattr(verdict, "order_flow") else {}
    buy_ratio = of.get("buy_ratio", 0.5) if isinstance(of, dict) else 0.5
    snap_of = getattr(verdict, "_of", None)
    if snap_of is None:
        pass
    direction = verdict.direction
    if direction == "long":
        of_ok = buy_ratio > 0.6
    elif direction == "short":
        of_ok = buy_ratio < 0.4
    else:
        of_ok = False
    return (
        verdict.score >= 0.75
        and verdict.at_poi
        and direction in hunt_biases
        and bool(verdict.of_patterns)
        and of_ok
    )


def enrich_verdict_dict(verdict_dict: dict[str, Any], hunt_biases: list[str]) -> dict[str, Any]:
    """Add mechanical_entry flag to verdict JSON for API/UI."""
    direction = verdict_dict.get("direction", "none")
    buy_ratio = verdict_dict.get("_buy_ratio")
    score = verdict_dict.get("score", 0)
    at_poi = verdict_dict.get("at_poi", False)
    patterns = verdict_dict.get("of_patterns") or []
    of_ok = True
    if buy_ratio is not None:
        if direction == "long":
            of_ok = buy_ratio > 0.6
        elif direction == "short":
            of_ok = buy_ratio < 0.4
        else:
            of_ok = False
    verdict_dict["mechanical_entry"] = (
        score >= 0.75 and at_poi and direction in hunt_biases and len(patterns) >= 1 and of_ok
    )
    verdict_dict.pop("_buy_ratio", None)
    return verdict_dict


@dataclass
class ScanResult:
    symbol: str
    price: float
    timeframe: str
    scalp_mode: bool
    bias: str
    grade: str
    score: float
    direction: str
    at_poi: bool
    patterns: list[str]
    mechanical_entry: bool
    wave: str
    poi_str: str
    snapshot: dict[str, Any]
    verdict: dict[str, Any]
    context_stale: bool = False
    multi_tf: dict[str, Any] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    oi: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "timeframe": self.timeframe,
            "scalp_mode": self.scalp_mode,
            "bias": self.bias,
            "grade": self.grade,
            "score": self.score,
            "direction": self.direction,
            "at_poi": self.at_poi,
            "patterns": self.patterns,
            "mechanical_entry": self.mechanical_entry,
            "wave": self.wave,
            "poi_str": self.poi_str,
            "snapshot": self.snapshot,
            "verdict": self.verdict,
            "context_stale": self.context_stale,
            "multi_tf": self.multi_tf,
            "alerts": self.alerts,
            "oi": self.oi,
            "ts": self.ts,
        }


def run_scan(
    symbol: str,
    timeframe: str | None = None,
    scalp_mode: bool = False,
    bias: str = "both",
    *,
    notify: Callable[[str, str], None] | None = None,
    desktop_notify: bool = False,
) -> ScanResult:
    """Single watcher scan cycle — returns structured result."""
    ctx = get_context(symbol)
    trade_type = "scalp" if scalp_mode else (ctx.trade_type if ctx else "scalp")
    if timeframe is None:
        timeframe = "1m" if scalp_mode else get_config()["timeframes"].get(trade_type, "1m")

    eval_ctx = ctx
    if scalp_mode:
        active = pick_active_bias(market_data.fetch_ticker_price(symbol), ctx)
        if bias == "both":
            hunt_biases = ["long", "short"]
            primary_bias = active
        else:
            hunt_biases = [bias]
            primary_bias = bias
        eval_ctx = build_scalp_ctx(symbol, primary_bias, ctx)
    else:
        hunt_biases = [bias] if bias != "both" else ["long", "short"]
        primary_bias = bias if bias != "both" else (ctx.bias if ctx and ctx.bias in ("long", "short") else "long")

    market_data.update_cache(symbol, timeframe, limit=200)
    snap = metrics.build_snapshot(symbol, timeframe, trade_type, trade_context=eval_ctx)
    snap.price = market_data.fetch_ticker_price(symbol)
    v = setup_engine.evaluate_setup(snap, trade_context=eval_ctx)

    df = market_data.load_klines_df(symbol, timeframe, limit=25)
    avg20 = df["volume"].iloc[-22:-2].mean() if len(df) >= 22 else df["volume"].mean()
    lc = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    delta = float(lc["taker_buy_base"] * 2 - lc["volume"])
    vol_x = float(lc["volume"] / avg20) if avg20 else 0.0

    price = snap.price
    levels = scalp_levels_from_context(ctx) if ctx else {}
    if scalp_mode and bias == "both" and levels:
        long_poi = levels["long"]
        short_poi = levels["short"]
        poi_str = f"LONG [{long_poi[0]:.2f}-{long_poi[1]:.2f}] | SHORT [{short_poi[0]:.2f}-{short_poi[1]:.2f}]"
    else:
        poi_str = (
            f"[{eval_ctx.poi_low}, {eval_ctx.poi_high}]"
            if eval_ctx and eval_ctx.has_poi()
            else "n/a"
        )

    wave = eval_ctx.wave if eval_ctx else "sin contexto"
    of = snap.order_flow
    oi_rising, oi_pct, oi_abs = scalp_alerts.recent_oi_rise(symbol)

    alerts: list = []
    tf_summary: dict[str, dict] = {}
    if scalp_mode:
        scan_tfs = ["1m", "5m", "15m"]
        alerts, tf_summary = scan_timeframes(symbol, scan_tfs, ctx, hunt_biases)

    verdict_dict = v.to_dict()
    verdict_dict["_buy_ratio"] = of.get("buy_ratio")
    verdict_dict = enrich_verdict_dict(verdict_dict, hunt_biases)

    def _notify(title: str, msg: str) -> None:
        if notify:
            notify(title, msg)
        notify_desktop(title, msg, enabled=desktop_notify)

    context_stale = bool(v.context_stale) or bool(ctx and ctx.has_poi() and ctx.is_stale(price))

    now_alerts = [a for a in alerts if a.urgency == "now"]
    fast_setup = v.score >= 0.75 and v.at_poi and v.direction in hunt_biases and not context_stale

    if now_alerts:
        for a in now_alerts:
            _notify(
                f"{a.direction.upper()} {symbol} {a.trigger}",
                f"@ {a.price:.4f} entry {a.entry:.4f} stop {a.stop:.4f} target {a.target:.4f}",
            )
    elif fast_setup and v.of_patterns:
        _notify(
            f"{primary_bias.upper()} SETUP {symbol} {v.score*100:.0f}%",
            f"@ {price:.4f} POI {eval_ctx.poi_low}-{eval_ctx.poi_high} patterns={v.of_patterns}",
        )
    elif v.grade == "A+" and v.at_poi and v.direction in hunt_biases:
        _notify(f"A+ {symbol} {v.direction.upper()}", f"@ {price:.4f} patterns={v.of_patterns}")

    multi_tf_out: dict[str, Any] = {}
    for tf, td in tf_summary.items():
        parts = {}
        for b in hunt_biases:
            row = td.get(b)
            if row:
                poi = scalp_levels_from_context(ctx)[b]
                parts[b] = {
                    **row,
                    "poi_low": poi[0],
                    "poi_high": poi[1],
                }
        multi_tf_out[tf] = parts

    snap_dict = snap.to_dict()
    snap_dict["vol_x"] = round(vol_x, 2)
    snap_dict["last_delta"] = round(delta)

    return ScanResult(
        symbol=symbol,
        price=price,
        timeframe=timeframe,
        scalp_mode=scalp_mode,
        bias=bias,
        grade=v.grade,
        score=v.score,
        direction=v.direction,
        at_poi=v.at_poi,
        patterns=v.of_patterns,
        mechanical_entry=verdict_dict.get("mechanical_entry", False),
        wave=wave,
        poi_str=poi_str,
        snapshot=snap_dict,
        verdict=verdict_dict,
        context_stale=context_stale,
        multi_tf=multi_tf_out,
        alerts=[a.to_dict() for a in alerts],
        oi={"rising": oi_rising, "pct": oi_pct, "abs": oi_abs},
    )


@dataclass
class WatcherStatus:
    running: bool = False
    symbol: str | None = None
    timeframe: str | None = None
    scalp_mode: bool = False
    bias: str = "both"
    interval_sec: int = 30
    started_at: str | None = None
    last_scan_at: str | None = None
    last_price: float | None = None
    last_grade: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "scalp_mode": self.scalp_mode,
            "bias": self.bias,
            "interval_sec": self.interval_sec,
            "started_at": self.started_at,
            "last_scan_at": self.last_scan_at,
            "last_price": self.last_price,
            "last_grade": self.last_grade,
        }


class WatcherService:
    """Background watcher with optional event callbacks (SSE, CLI)."""

    _instance: WatcherService | None = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = WatcherStatus()
        self._last_scan: ScanResult | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        cfg = get_config()
        web_cfg = cfg.get("web", {})
        self._desktop_notify = bool(web_cfg.get("notify_desktop", False))

    @classmethod
    def instance(cls) -> WatcherService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {"type": event_type, "payload": payload or {}, "ts": datetime.now(timezone.utc).isoformat()}
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception:
                pass

    def status(self) -> WatcherStatus:
        with self._lock:
            return WatcherStatus(**self._status.to_dict())

    def last_scan(self) -> ScanResult | None:
        with self._lock:
            return self._last_scan

    def start(
        self,
        symbol: str,
        timeframe: str = "15m",
        *,
        scalp: bool = True,
        bias: str = "both",
        interval_sec: int = 30,
    ) -> WatcherStatus:
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"

        with self._lock:
            if (
                self._status.running
                and self._status.symbol == sym
                and self._status.timeframe == timeframe
                and self._status.scalp_mode == scalp
                and self._status.bias == bias
                and self._status.interval_sec == interval_sec
            ):
                return self._status

        self.stop()
        self._stop_event.clear()

        with self._lock:
            self._status = WatcherStatus(
                running=True,
                symbol=sym,
                timeframe=timeframe,
                scalp_mode=scalp,
                bias=bias,
                interval_sec=interval_sec,
                started_at=datetime.now(timezone.utc).isoformat(),
            )

        def _loop() -> None:
            self.emit("watcher_started", self._status.to_dict())
            result = self.run_once()
            if result:
                self.emit("scan", result.to_dict())
            while not self._stop_event.wait(interval_sec):
                result = self.run_once()
                if result:
                    self.emit("scan", result.to_dict())
                    for alert in result.alerts:
                        if alert.get("urgency") == "now":
                            self.emit("alert", alert)

        self._thread = threading.Thread(target=_loop, daemon=True, name="watcher")
        self._thread.start()
        return self.status()

    def stop(self) -> WatcherStatus:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        with self._lock:
            was_running = self._status.running
            self._status.running = False
        if was_running:
            self.emit("watcher_stopped", self.status().to_dict())
        return self.status()

    def run_once(self) -> ScanResult | None:
        with self._lock:
            if not self._status.running and self._status.symbol is None:
                sym = "HYPEUSDT"
                tf = "15m"
                scalp = True
                bias = "both"
            else:
                sym = self._status.symbol or "HYPEUSDT"
                tf = self._status.timeframe or "15m"
                scalp = self._status.scalp_mode
                bias = self._status.bias

        def _event_notify(title: str, msg: str) -> None:
            self.emit("alert", {"title": title, "message": msg})

        try:
            result = run_scan(
                sym,
                tf,
                scalp_mode=scalp,
                bias=bias,
                notify=_event_notify,
                desktop_notify=self._desktop_notify,
            )
            with self._lock:
                self._last_scan = result
                self._status.last_scan_at = result.ts
                self._status.last_price = result.price
                self._status.last_grade = result.grade
            return result
        except Exception as exc:
            self.emit("error", {"message": str(exc)})
            return None
