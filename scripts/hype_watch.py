"""Symbol watcher — fast scalp alerts + status."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import market_data, metrics, scalp_alerts, setup_engine
from agent.config import get_config
from agent.context import TradeContext, get_context
from agent.scheduler import SchedulerService

ALERT_LOG = ROOT / "data" / "alerts.log"


def notify_user(title: str, message: str) -> None:
    """Real desktop notification: sound + Windows toast + alert log file."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {title} — {message}"

    # 1. Append to alert log (so nothing is lost)
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

    # 2. Sound + Windows toast notification
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
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('HYPE Watcher').Show($n);"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    print(f"\n  >>> ALERTA: {line} <<<", flush=True)


def _scalp_levels_from_context(ctx: TradeContext | None) -> dict[str, tuple[float, float, float, list[float]]]:
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


def _build_scalp_ctx(symbol: str, bias: str, base_ctx: TradeContext | None = None) -> TradeContext:
    """Ephemeral scalp context based on desired bias."""
    levels = _scalp_levels_from_context(base_ctx)
    poi_lo, poi_hi, inv, targets = levels[bias]
    wave = (base_ctx.wave if base_ctx and base_ctx.wave else None) or (
        "ABC-C / W2 profunda — rebote en soporte"
        if bias == "long"
        else "Onda C de (4) — short en resistencia"
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


def _pick_active_bias(price: float, base_ctx: TradeContext | None) -> str:
    """Choose scalp bias by proximity to long/short POI zones."""
    levels = _scalp_levels_from_context(base_ctx)
    long_lo, long_hi, _, _ = levels["long"]
    short_lo, short_hi, _, _ = levels["short"]
    long_mid = (long_lo + long_hi) / 2
    short_mid = (short_lo + short_hi) / 2
    if price <= long_hi + 0.15:
        return "long"
    if price >= short_lo - 0.15:
        return "short"
    return "long" if abs(price - long_mid) <= abs(price - short_mid) else "short"


def _scan_timeframes(
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
            eval_ctx = _build_scalp_ctx(symbol, b, base_ctx)
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
            for a in alerts:
                a.detail = f"[{tf}] {a.detail}"
            tf_data["alerts"].extend(alerts)
            all_alerts.extend(alerts)
        summary[tf] = tf_data
    return all_alerts, summary


def print_status(
    symbol: str,
    timeframe: str | None = None,
    scalp_mode: bool = False,
    bias: str = "short",
) -> list:
    ctx = get_context(symbol)
    trade_type = "scalp" if scalp_mode else (ctx.trade_type if ctx else "scalp")
    if timeframe is None:
        timeframe = "1m" if scalp_mode else get_config()["timeframes"].get(trade_type, "1m")

    eval_ctx = ctx
    if scalp_mode:
        active = _pick_active_bias(market_data.fetch_ticker_price(symbol), ctx)
        if bias == "both":
            hunt_biases = ["long", "short"]
            primary_bias = active
        else:
            hunt_biases = [bias]
            primary_bias = bias
        eval_ctx = _build_scalp_ctx(symbol, primary_bias, ctx)
    else:
        hunt_biases = [bias]
        primary_bias = bias

    market_data.update_cache(symbol, timeframe, limit=200)
    snap = metrics.build_snapshot(symbol, timeframe, trade_type, trade_context=eval_ctx)
    snap.price = market_data.fetch_ticker_price(symbol)
    v = setup_engine.evaluate_setup(snap, trade_context=eval_ctx)
    df = market_data.load_klines_df(symbol, timeframe, limit=25)
    avg20 = df["volume"].iloc[-22:-2].mean() if len(df) >= 22 else df["volume"].mean()
    lc = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    delta = lc["taker_buy_base"] * 2 - lc["volume"]
    vol_x = lc["volume"] / avg20 if avg20 else 0

    price = snap.price
    ts = datetime.now().strftime("%H:%M:%S")
    sym_short = symbol.replace("USDT", "")

    wave = eval_ctx.wave if eval_ctx else "sin contexto"
    levels = _scalp_levels_from_context(ctx) if ctx else {}
    if scalp_mode and bias == "both" and levels:
        long_poi = levels["long"]
        short_poi = levels["short"]
        poi_str = f"LONG [{long_poi[0]:.2f}-{long_poi[1]:.2f}] | SHORT [{short_poi[0]:.2f}-{short_poi[1]:.2f}]"
        dir_label = f"NEAR {primary_bias.upper()}" if primary_bias else "HUNT"
    else:
        poi_str = f"[{eval_ctx.poi_low}, {eval_ctx.poi_high}]" if eval_ctx and eval_ctx.has_poi() else "n/a"
        dir_label = v.direction.upper()

    print(f"\n[{ts}] {sym_short} {price:.4f} | {v.grade} {dir_label} {v.score*100:.0f}%", flush=True)
    print(f"  Elliott: {wave} | POI {poi_str}", flush=True)
    print(
        f"  {timeframe} trend={snap.structure['trend']} OF={snap.order_flow['bias']} "
        f"| vol {vol_x:.2f}x delta {delta:+.0f}",
        flush=True,
    )
    of = snap.order_flow
    if of.get("buy_ratio"):
        print(
            f"  Real OF: buy_ratio={of.get('buy_ratio')} book={of.get('book_bias', 'n/a')} "
            f"recent_delta={of.get('recent_delta_sum')}",
            flush=True,
        )
    oi_rising, oi_pct, oi_abs = scalp_alerts.recent_oi_rise(symbol)
    if oi_pct != 0.0:
        arrow = "SUBE" if oi_rising else "BAJA"
        print(
            f"  Open Interest 20m: {arrow} {oi_pct*100:+.2f}% ({oi_abs:+.0f}) "
            f"{'-> posiciones nuevas entrando' if oi_rising else '-> cierre de posiciones'}",
            flush=True,
        )
    if v.of_patterns:
        print(f"  Patterns: {', '.join(v.of_patterns)}", flush=True)

    alerts: list = []
    if scalp_mode:
        scan_tfs = ["1m", "5m", "15m"]
        alerts, tf_summary = _scan_timeframes(symbol, scan_tfs, ctx, hunt_biases)
        for tf in scan_tfs:
            td = tf_summary[tf]
            parts = []
            for b in hunt_biases:
                row = td[b]
                if row:
                    poi = _scalp_levels_from_context(ctx)[b]
                    parts.append(
                        f"{b.upper()} {row['score']*100:.0f}% poi={poi[0]:.2f}-{poi[1]:.2f} "
                        f"trend={row['trend']} buy={row['buy_ratio']}"
                    )
            print(f"  [{tf}] " + " | ".join(parts), flush=True)
        msg = scalp_alerts.format_alerts(alerts)
        if msg:
            print(msg, flush=True)

    now_alerts = [a for a in alerts if a.urgency == "now"]
    fast_setup = v.score >= 0.75 and v.at_poi and v.direction in hunt_biases
    if scalp_mode and bias == "both" and not now_alerts:
        for b in hunt_biases:
            bctx = _build_scalp_ctx(symbol, b, ctx)
            bsnap = metrics.build_snapshot(symbol, timeframe or "1m", "scalp", trade_context=bctx)
            bsnap.price = price
            bv = setup_engine.evaluate_setup(bsnap, trade_context=bctx)
            if bv.score >= 0.75 and bv.at_poi and bv.of_patterns:
                notify_user(
                    f"{b.upper()} SETUP {symbol} {bv.score*100:.0f}%",
                    f"@ {price:.4f} POI {bctx.poi_low}-{bctx.poi_high} patterns={bv.of_patterns}",
                )
                break

    if now_alerts:
        for a in now_alerts:
            notify_user(
                f"{a.direction.upper()} {symbol} {a.trigger}",
                f"@ {a.price:.4f} entry {a.entry:.4f} stop {a.stop:.4f} target {a.target:.4f}",
            )
    elif fast_setup:
        notify_user(
            f"{primary_bias.upper()} SETUP {symbol} {v.score*100:.0f}%",
            f"@ {price:.4f} POI {eval_ctx.poi_low}-{eval_ctx.poi_high} patterns={v.of_patterns}",
        )
    elif v.grade == "A+" and v.at_poi and v.direction in hunt_biases:
        notify_user(f"A+ {symbol} {v.direction.upper()}", f"@ {price:.4f} patterns={v.of_patterns}")
    elif scalp_mode and bias == "both":
        for b in hunt_biases:
            bctx = _build_scalp_ctx(symbol, b, ctx)
            if not bctx.price_near_poi(price, get_config()["thresholds"]["poi_proximity_pct"]):
                continue
            bsnap = metrics.build_snapshot(symbol, timeframe or "1m", "scalp", trade_context=bctx)
            bsnap.price = price
            bv = setup_engine.evaluate_setup(bsnap, trade_context=bctx)
            if bv.grade == "A+" and bv.at_poi:
                notify_user(f"A+ {symbol} {b.upper()}", f"@ {price:.4f} patterns={bv.of_patterns}")
                break

    return alerts


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch a symbol with fast scalp alerts")
    parser.add_argument("symbol", nargs="?", default="HYPEUSDT", help="Binance Futures symbol")
    parser.add_argument("--timeframe", "-t", default=None, help="Override timeframe (e.g. 1m)")
    parser.add_argument(
        "--scalp",
        action="store_true",
        help="Fast scalp mode: 30s scans + rejection/breakdown triggers",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Scan interval seconds (default: 30 scalp, 60 normal)",
    )
    parser.add_argument(
        "--bias",
        default="both",
        choices=["long", "short", "both"],
        help="Scalp bias: long (66.58), short (68.30 retest), or both",
    )
    args = parser.parse_args()
    symbol = args.symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    timeframe = args.timeframe or ("1m" if args.scalp else None)
    interval_sec = args.interval or (30 if args.scalp else 60)
    bias = args.bias

    print(
        f"{symbol} watcher — cada {interval_sec}s (tf={timeframe or 'ctx'}) "
        f"scalp_mode={'ON' if args.scalp else 'OFF'} bias={bias}",
        flush=True,
    )
    print(f"Alertas: sonido + toast Windows + log en {ALERT_LOG}", flush=True)
    print_status(symbol, timeframe, scalp_mode=args.scalp, bias=bias)

    scheduler = SchedulerService() if not args.scalp else None
    if scheduler:
        scheduler.start()

    try:
        while True:
            time.sleep(interval_sec)
            print_status(symbol, timeframe, scalp_mode=args.scalp, bias=bias)
            if scheduler:
                scheduler.run_once()
    except KeyboardInterrupt:
        if scheduler:
            scheduler.stop()
        print("\nWatcher stopped.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
