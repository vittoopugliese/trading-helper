"""Symbol watcher CLI — uses shared agent.watcher."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.scheduler import SchedulerService
from agent.watcher import ALERT_LOG, notify_desktop, run_scan


def notify_user(title: str, message: str) -> None:
    notify_desktop(title, message, enabled=True)


def print_status(
    symbol: str,
    timeframe: str | None = None,
    scalp_mode: bool = False,
    bias: str = "short",
) -> list:
    result = run_scan(
        symbol,
        timeframe,
        scalp_mode=scalp_mode,
        bias=bias,
        notify=notify_user,
        desktop_notify=True,
    )
    ts = result.ts[11:19] if len(result.ts) > 19 else result.ts
    sym_short = symbol.replace("USDT", "")
    dir_label = result.direction.upper()
    if scalp_mode and bias == "both":
        dir_label = f"NEAR {result.direction.upper()}"

    print(f"\n[{ts}] {sym_short} {result.price:.4f} | {result.grade} {dir_label} {result.score*100:.0f}%", flush=True)
    if result.context_stale:
        drift = result.verdict.get("context_drift_pct")
        drift_str = f" ({drift*100:.1f}% del POI)" if isinstance(drift, (int, float)) else ""
        print(
            f"  !! CONTEXTO DESACTUALIZADO{drift_str} — rehace el conteo en context/{symbol}.yaml. "
            f"Entry/stop/targets de las alertas NO son operables.",
            flush=True,
        )
    print(f"  Elliott: {result.wave} | POI {result.poi_str}", flush=True)
    snap = result.snapshot
    print(
        f"  {result.timeframe} trend={snap.get('structure', {}).get('trend')} "
        f"OF={snap.get('order_flow', {}).get('bias')} "
        f"| vol {snap.get('vol_x', 0):.2f}x delta {snap.get('last_delta', 0):+.0f}",
        flush=True,
    )
    of = snap.get("order_flow", {})
    if of.get("buy_ratio"):
        print(
            f"  Real OF: buy_ratio={of.get('buy_ratio')} book={of.get('book_bias', 'n/a')} "
            f"recent_delta={of.get('recent_delta_sum')}",
            flush=True,
        )
    oi = result.oi
    if oi.get("pct", 0) != 0.0:
        arrow = "SUBE" if oi.get("rising") else "BAJA"
        print(
            f"  Open Interest 20m: {arrow} {oi.get('pct', 0)*100:+.2f}% ({oi.get('abs', 0):+.0f})",
            flush=True,
        )
    if result.patterns:
        print(f"  Patterns: {', '.join(result.patterns)}", flush=True)

    if scalp_mode and result.multi_tf:
        for tf, parts in result.multi_tf.items():
            lines = []
            for b, row in parts.items():
                if row:
                    lines.append(
                        f"{b.upper()} {row['score']*100:.0f}% poi={row['poi_low']:.2f}-{row['poi_high']:.2f} "
                        f"trend={row['trend']} buy={row['buy_ratio']}"
                    )
            if lines:
                print(f"  [{tf}] " + " | ".join(lines), flush=True)

    from agent import scalp_alerts
    from agent.scalp_alerts import ScalpAlert

    alert_objs = [
        ScalpAlert(
            direction=a["direction"],
            trigger=a["trigger"],
            urgency=a["urgency"],
            price=a["price"],
            entry=a["entry"],
            stop=a["stop"],
            target=a["target"],
            detail=a["detail"],
        )
        for a in result.alerts
    ]
    msg = scalp_alerts.format_alerts(alert_objs)
    if msg:
        print(msg, flush=True)

    return alert_objs


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch a symbol with fast scalp alerts")
    parser.add_argument("symbol", nargs="?", default="HYPEUSDT", help="Binance Futures symbol")
    parser.add_argument("--timeframe", "-t", default=None, help="Override timeframe (e.g. 1m)")
    parser.add_argument("--scalp", action="store_true", help="Fast scalp mode")
    parser.add_argument("--interval", type=int, default=None, help="Scan interval seconds")
    parser.add_argument("--bias", default="both", choices=["long", "short", "both"])
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
