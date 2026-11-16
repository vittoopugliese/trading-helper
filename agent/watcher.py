"""Shared symbol watcher — scalp alerts, scan cycles, optional desktop notify."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent import market_data, metrics, scalp_alerts, setup_engine, signal_ledger
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
    """Coherent long/short scenarios of the context as (poi_low, poi_high, invalidation, targets).

    No hardcoded fallback levels: without a context (or without a coherent scenario for a side)
    that side is simply not hunted. Old HYPE numbers used to leak into any symbol here.
    """
    if not ctx:
        return {}
    return {
        direction: (sc["poi_low"], sc["poi_high"], sc["invalidation"], list(sc["targets"]))
        for direction, sc in ctx.scenarios().items()
    }


def build_scalp_ctx(symbol: str, bias: str, base_ctx: TradeContext | None = None) -> TradeContext | None:
    """Ephemeral context for one side, keeping the account, risk and age of the stored one."""
    levels = scalp_levels_from_context(base_ctx)
    if bias not in levels:
        return None
    poi_lo, poi_hi, inv, targets = levels[bias]
    wave = (base_ctx.wave if base_ctx and base_ctx.wave else "") or f"escenario {bias}"
    return TradeContext(
        symbol=symbol,
        bias=bias,
        wave=wave,
        trade_type="scalp",
        poi_low=poi_lo,
        poi_high=poi_hi,
        invalidation=inv,
        targets=targets,
        account_size=base_ctx.account_size if base_ctx else None,
        risk_pct=base_ctx.risk_pct if base_ctx else 2.0,
        as_of=base_ctx.as_of if base_ctx else "",
    )


def pick_active_bias(price: float, base_ctx: TradeContext | None) -> str | None:
    """Side whose POI is closest to the price (None when the context has no scenario)."""
    levels = scalp_levels_from_context(base_ctx)
    if not levels:
        return None

    def distance(direction: str) -> float:
        lo, hi, _, _ = levels[direction]
        return 0.0 if lo <= price <= hi else min(abs(price - lo), abs(price - hi))

    return min(levels, key=distance)


def _alert_block_reasons(alert: scalp_alerts.ScalpAlert, eval_ctx: TradeContext, price: float,
                         guard: dict[str, Any]) -> list[str]:
    """Everything that stops a scalp alert from being an entry: same rules as the engine."""
    reasons = scalp_alerts.validate_alert(alert)
    issues = eval_ctx.validate()
    reasons += [f"contexto: {i}" for i in issues]
    if eval_ctx.is_expired():
        reasons.append(f"contexto vencido ({eval_ctx.age_days():.0f} dias)")
    if eval_ctx.has_poi() and eval_ctx.is_stale(price):
        reasons.append("contexto desactualizado (precio lejos del POI)")
    if guard.get("blocked"):
        reasons.append(guard["detail"])
    return reasons


def scan_timeframes(
    symbol: str,
    timeframes: list[str],
    base_ctx: TradeContext | None,
    biases: list[str],
) -> tuple[list, dict[str, dict]]:
    """Scan multiple TFs and biases. One snapshot per TF serves both sides (the evidence is
    the same market), and the aggTrades/book read is reused by the scalp detectors.

    Returns (alerts, summary); summary[tf]["_eval"][bias] = (snapshot, verdict) so each alert
    can be recorded with the evidence of the timeframe and side that produced it.
    """
    from agent import policy

    guard = policy.daily_guard()
    all_alerts: list = []
    summary: dict[str, dict] = {}
    for tf in timeframes:
        tf_data: dict = {"long": None, "short": None, "alerts": [], "_eval": {}}
        snap = None
        for b in biases:
            eval_ctx = build_scalp_ctx(symbol, b, base_ctx)
            if eval_ctx is None:
                continue
            if snap is None:
                snap = metrics.build_snapshot(symbol, tf, "scalp", trade_context=eval_ctx, include_context=True)
            snap.order_flow["volume_spike_at_poi"] = bool(
                eval_ctx.price_in_poi(snap.price) and snap.indicators.get("volume_spike")
            )
            v = setup_engine.evaluate_setup(snap, trade_context=eval_ctx)
            tf_data["_eval"][b] = (snap, v)
            tf_data[b] = {
                "grade": v.grade,
                "score": v.score,
                "direction": v.direction,
                "eligible": v.eligible,
                "blocked_reasons": v.blocked_reasons,
                "at_poi": v.at_poi,
                "patterns": v.of_patterns,
                "trend": snap.structure.get("trend"),
                "buy_ratio": snap.order_flow.get("buy_ratio"),
                "vol_ratio": snap.indicators.get("volume_ratio"),
            }
            if b == "long":
                alerts = scalp_alerts.detect_scalp_alerts(
                    symbol, tf, short_resistance=None,
                    long_support=(eval_ctx.poi_low, eval_ctx.poi_high),
                    long_invalidation=eval_ctx.invalidation,
                    long_targets=tuple(eval_ctx.targets),
                    order_flow=snap.order_flow,
                )
            else:
                alerts = scalp_alerts.detect_scalp_alerts(
                    symbol, tf, short_resistance=(eval_ctx.poi_low, eval_ctx.poi_high),
                    short_invalidation=eval_ctx.invalidation,
                    short_targets=tuple(eval_ctx.targets),
                    long_support=None,
                    order_flow=snap.order_flow,
                )
            for a in alerts:
                a.timeframe = tf
                a.blocked_reasons = _alert_block_reasons(a, eval_ctx, snap.price, guard)
                if a.blocked_reasons:
                    # Shown as an observation, never as "*** ENTRAR ***".
                    a.urgency = "watch"
                    a.detail = f"[{tf}] [no elegible: {a.blocked_reasons[0]}] {a.detail}"
                else:
                    a.detail = f"[{tf}] {a.detail}"
            tf_data["alerts"].extend(alerts)
            all_alerts.extend(alerts)
        summary[tf] = tf_data
    return all_alerts, summary


def enrich_verdict_dict(verdict_dict: dict[str, Any], hunt_biases: list[str]) -> dict[str, Any]:
    """mechanical_entry = the engine said eligible AND the live flow agrees right now.

    It can only narrow `eligible`, never widen it: NO_TRADE / B / any blocked reason means
    no "ENTRADA MECANICA", whatever the score.
    """
    direction = verdict_dict.get("direction", "none")
    buy_ratio = verdict_dict.get("_buy_ratio")
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
    verdict_dict["mechanical_entry"] = bool(
        verdict_dict.get("eligible") and at_poi and direction in hunt_biases and patterns and of_ok
    )
    verdict_dict.pop("_buy_ratio", None)
    return verdict_dict


def signal_features(
    snap: metrics.MarketSnapshot, verdict: setup_engine.SetupVerdict, direction: str, context_stale: bool
) -> dict[str, Any]:
    """Evidence present when an alert fires; stored in the signal ledger to measure later."""
    from agent import atas_feed, liquidations

    conf = atas_feed.footprint_confirmation(snap.atas or {}, direction)
    flush = liquidations.flush_confirmation(snap.liquidations or {}, direction)
    trend = snap.structure.get("trend")
    oi_change = (snap.derivatives or {}).get("oi_change_1h")
    return {
        "atas_available": conf["available"],
        "atas_confirmed": conf["confirmed"] if conf["available"] else None,
        "atas_contradicted": conf["contradicted"] if conf["available"] else None,
        "atas_signals": conf["signals"],
        "atas_tf": (snap.atas or {}).get("tf"),
        "liq_flush": flush["flush"] if flush["available"] else None,
        "liq_detail": flush["detail"],
        "at_poi": bool(verdict.at_poi),
        "oi_rising": (oi_change > 0) if oi_change is not None else None,
        "funding_extreme": (snap.derivatives or {}).get("funding_extreme"),
        "trend_aligned": (direction == "long" and trend == "bullish") or (direction == "short" and trend == "bearish"),
        "context_fresh": not context_stale,
    }


def evidence_line(features: dict[str, Any]) -> str:
    if not features.get("atas_available"):
        parts = ["sin ATAS"]
    elif features.get("atas_confirmed"):
        parts = ["ATAS OK: " + ", ".join(features.get("atas_signals") or [])]
    elif features.get("atas_contradicted"):
        parts = ["ATAS EN CONTRA"]
    else:
        parts = ["ATAS sin gatillo"]
    if features.get("liq_flush"):
        parts.append("flush " + str(features.get("liq_detail")))
    return " | ".join(parts)


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

    price_now = market_data.fetch_ticker_price(symbol)
    eval_ctx = ctx
    if scalp_mode:
        available = set(scalp_levels_from_context(ctx))
        wanted = ["long", "short"] if bias == "both" else [bias]
        hunt_biases = [b for b in wanted if b in available]
        primary_bias = pick_active_bias(price_now, ctx) if bias == "both" else bias
        eval_ctx = (build_scalp_ctx(symbol, primary_bias, ctx) if primary_bias else None) or ctx
    else:
        hunt_biases = [bias] if bias != "both" else ["long", "short"]
        primary_bias = bias if bias != "both" else (ctx.bias if ctx and ctx.bias in ("long", "short") else "long")

    market_data.update_cache(symbol, timeframe, limit=200)
    snap = metrics.build_snapshot(symbol, timeframe, trade_type, trade_context=eval_ctx)
    snap.price = price_now
    v = setup_engine.evaluate_setup(snap, trade_context=eval_ctx)

    df = market_data.load_klines_df(symbol, timeframe, limit=25)
    avg20 = df["volume"].iloc[-22:-2].mean() if len(df) >= 22 else df["volume"].mean()
    lc = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    delta = float(lc["taker_buy_base"] * 2 - lc["volume"])
    vol_x = float(lc["volume"] / avg20) if avg20 else 0.0

    price = snap.price
    levels = scalp_levels_from_context(ctx)
    if scalp_mode and bias == "both" and levels:
        poi_str = " | ".join(f"{d.upper()} [{lv[0]:.2f}-{lv[1]:.2f}]" for d, lv in levels.items())
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
    if scalp_mode and hunt_biases:
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

    def _record(kind: str, direction: str, tf: str, snap_used: metrics.MarketSnapshot,
                verdict_used: setup_engine.SetupVerdict, stop: float | None, target: float | None,
                entry: float | None, detail: str) -> tuple[bool, dict[str, Any]]:
        """Store the alert; returns (should_emit, features). Inside the ledger cooldown the
        same signal is not emitted again, so the cooldown limits notifications too."""
        feats = signal_features(snap_used, verdict_used, direction, context_stale)
        feats["policy_version"] = verdict_used.policy_version
        feats["context_as_of"] = ctx.as_of if ctx else None
        try:
            signal_id = signal_ledger.record(
                symbol=symbol, tf=tf, kind=kind, direction=direction, price=price, stop=stop,
                target=target, grade=verdict_used.grade, score=verdict_used.score, planned_entry=entry,
                features=feats, detail=detail,
            )
        except Exception:
            return True, feats  # the ledger must never swallow an alert
        return signal_id is not None, feats

    for a in [a for a in alerts if a.urgency == "now"]:
        snap_tf, v_tf = tf_summary.get(a.timeframe, {}).get("_eval", {}).get(a.direction, (snap, v))
        emit, feats = _record(f"scalp:{a.trigger}", a.direction, a.timeframe or timeframe, snap_tf, v_tf,
                              a.stop, a.target, a.entry, a.detail)
        if emit:
            _notify(
                f"{a.direction.upper()} {symbol} {a.trigger} [{a.timeframe}]",
                f"@ {a.price:.4f} entry {a.entry:.4f} stop {a.stop:.4f} target {a.target:.4f} | {evidence_line(feats)}",
            )

    if v.eligible and v.at_poi and v.direction in hunt_biases:
        emit, feats = _record("a_plus", v.direction, timeframe, snap, v, v.levels.get("stop"),
                              v.levels.get("target"), v.levels.get("entry"), v.summary)
        if emit:
            _notify(f"A+ {symbol} {v.direction.upper()}", f"@ {price:.4f} patterns={v.of_patterns} | {evidence_line(feats)}")
    elif v.score >= 0.75 and v.at_poi and v.direction in hunt_biases and v.of_patterns:
        # Observation only: close to a setup but not eligible. Says why, never "entrar".
        emit, _ = _record("setup_watch", v.direction, timeframe, snap, v, v.levels.get("stop"),
                          v.levels.get("target"), v.levels.get("entry"), v.summary)
        if emit:
            reason = v.blocked_reasons[0] if v.blocked_reasons else "no elegible"
            _notify(f"VIGILAR {symbol} {v.direction.upper()} {v.score*100:.0f}%", f"@ {price:.4f} no elegible: {reason}")

    multi_tf_out: dict[str, Any] = {}
    for tf, td in tf_summary.items():
        parts = {}
        for b in hunt_biases:
            row = td.get(b)
            if row and b in levels:
                poi = levels[b]
                parts[b] = {**row, "poi_low": poi[0], "poi_high": poi[1]}
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
                    # Urgent alerts were already emitted (once, deduplicated) inside run_scan.
                    self.emit("scan", result.to_dict())

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

        # Label alerts whose outcome is now known (TP / SL / timeout), at most once a minute.
        now_mono = time.monotonic()
        if now_mono - getattr(self, "_last_resolve", 0.0) >= 60:
            self._last_resolve = now_mono
            try:
                if signal_ledger.resolve_open():
                    self.emit("signals", signal_ledger.stats())
            except Exception:
                pass

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
