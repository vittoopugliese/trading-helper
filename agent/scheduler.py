"""Background scheduler for watchlist scanning — alerts only at POI + OF confirm."""

from __future__ import annotations

from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from agent.config import get_config
from agent.context import get_context
from agent import analyst, display


class SchedulerService:
    """Runs periodic watchlist scans in background."""

    def __init__(self, on_alert: Callable | None = None):
        cfg = get_config()
        sched_cfg = cfg.get("scheduler", {})
        self.enabled = sched_cfg.get("enabled", True)
        self.interval_minutes = sched_cfg.get("interval_minutes", 15)
        self.default_trade_type = sched_cfg.get("default_trade_type", "swing")
        self.alert_only_at_poi = sched_cfg.get("alert_only_at_poi", True)
        self.watchlist = cfg.get("watchlist", [])
        self.timeframes = cfg.get("timeframes", {})
        self.on_alert = on_alert or display.print_alert
        self._scheduler: BackgroundScheduler | None = None
        self._running = False

    def _should_alert(self, bundle) -> bool:
        """Alert only when grade is A+ and (if configured) price at POI with OF confirmation."""
        grade = bundle.verdict.get("grade", "NO_TRADE")
        if grade != "A+":
            return False

        if not self.alert_only_at_poi:
            return True

        at_poi = bundle.verdict.get("at_poi", False)
        of_patterns = bundle.verdict.get("of_patterns", [])
        return at_poi and len(of_patterns) > 0

    def run_once(self) -> list[dict[str, Any]]:
        """Scan all watchlist pairs once."""
        results: list[dict[str, Any]] = []

        for symbol in self.watchlist:
            ctx = get_context(symbol)
            trade_type = ctx.trade_type if ctx and ctx.trade_type else self.default_trade_type
            timeframe = self.timeframes.get(trade_type, "4h")

            try:
                bundle = analyst.analyze(symbol, timeframe, trade_type)
                grade = bundle.verdict.get("grade", "NO_TRADE")
                results.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "grade": grade,
                        "direction": bundle.verdict.get("direction"),
                        "score": bundle.verdict.get("score"),
                        "at_poi": bundle.verdict.get("at_poi", False),
                        "of_patterns": bundle.verdict.get("of_patterns", []),
                    }
                )
                if self._should_alert(bundle):
                    self.on_alert(bundle)
            except Exception as exc:
                results.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "grade": "ERROR",
                        "direction": str(exc),
                        "score": 0,
                    }
                )

        return results

    def _job(self) -> None:
        self.run_once()

    def start(self) -> None:
        if self._running or not self.enabled:
            return
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._job,
            trigger=IntervalTrigger(minutes=self.interval_minutes),
            id="watchlist_scan",
            replace_existing=True,
        )
        self._scheduler.start()
        self._running = True

    def stop(self) -> None:
        if self._scheduler and self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
