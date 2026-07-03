"""Live Cursor on-demand usage dashboard — polls cursor.com every second."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

_dashboard_dir = Path(__file__).parent
load_dotenv(_dashboard_dir / ".env")
if not os.getenv("CURSOR_SESSION_TOKEN", "").strip():
    load_dotenv(_dashboard_dir / ".env.example")

CURSOR_SESSION_TOKEN = os.getenv("CURSOR_SESSION_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8765"))
POLL_INTERVAL_SEC = 1.0
MAX_HISTORY = 3600  # ~1 hour at 1 poll/sec

STATIC_DIR = Path(__file__).parent / "static"

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": "cursor-usage-dashboard/1.0",
        "Origin": "https://cursor.com",
    }
)
if CURSOR_SESSION_TOKEN:
    _session.cookies.set("WorkosCursorSessionToken", CURSOR_SESSION_TOKEN, domain="cursor.com")

_lock = threading.Lock()
_state: dict = {
    "ok": False,
    "error": None,
    "updated_at": None,
    "on_demand_cents": None,
    "on_demand_dollars": None,
    "delta_cents": 0,
    "plan_used": None,
    "plan_limit": None,
    "plan_percent": None,
    "membership_type": None,
    "billing_cycle_start": None,
    "billing_cycle_end": None,
    "history": [],
}


def _cents_to_dollars(cents: float | int | None) -> float | None:
    if cents is None:
        return None
    return round(float(cents) / 100, 4)


def _fetch_usage_summary() -> dict:
    resp = _session.get("https://cursor.com/api/usage-summary", timeout=15)
    resp.raise_for_status()
    return resp.json()


def _poll_loop() -> None:
    global _state
    prev_cents: float | None = None

    while True:
        tick = datetime.now(timezone.utc).isoformat()
        try:
            if not CURSOR_SESSION_TOKEN:
                raise ValueError(
                    "Falta CURSOR_SESSION_TOKEN. Copiá la cookie WorkosCursorSessionToken "
                    "desde cursor.com/dashboard/usage y pegala en cursor-usage-dashboard/.env"
                )

            summary = _fetch_usage_summary()
            individual = summary.get("individualUsage") or {}
            on_demand = individual.get("onDemand") or {}
            plan = individual.get("plan") or {}

            cents = on_demand.get("used")
            if cents is not None:
                cents = float(cents)

            delta = 0.0
            if cents is not None and prev_cents is not None:
                delta = cents - prev_cents
            if cents is not None:
                prev_cents = cents

            plan_used = plan.get("used")
            plan_limit = plan.get("limit")
            plan_percent = plan.get("totalPercentUsed")

            point = {
                "t": tick,
                "cents": cents,
                "dollars": _cents_to_dollars(cents),
                "delta_cents": delta,
            }

            with _lock:
                history: deque = deque(_state.get("_deque", []), maxlen=MAX_HISTORY)
                history.append(point)
                _state = {
                    "ok": True,
                    "error": None,
                    "updated_at": tick,
                    "on_demand_cents": cents,
                    "on_demand_dollars": _cents_to_dollars(cents),
                    "delta_cents": delta,
                    "plan_used": plan_used,
                    "plan_limit": plan_limit,
                    "plan_percent": plan_percent,
                    "membership_type": summary.get("membershipType"),
                    "billing_cycle_start": summary.get("billingCycleStart"),
                    "billing_cycle_end": summary.get("billingCycleEnd"),
                    "on_demand_enabled": on_demand.get("enabled"),
                    "on_demand_limit_cents": on_demand.get("limit"),
                    "history": list(history),
                    "_deque": history,
                }
        except Exception as exc:
            with _lock:
                history = list(_state.get("_deque", []))
                _state = {
                    **{k: v for k, v in _state.items() if not k.startswith("_")},
                    "ok": False,
                    "error": str(exc),
                    "updated_at": tick,
                    "history": history,
                    "_deque": deque(history, maxlen=MAX_HISTORY),
                }

        time.sleep(POLL_INTERVAL_SEC)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        pass

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        if path == "/api/usage":
            with _lock:
                payload = {k: v for k, v in _state.items() if not k.startswith("_")}
            self._send_json(payload)
            return

        if path.startswith("/static/"):
            rel = path.removeprefix("/static/")
            self._send_file(STATIC_DIR / rel, "application/octet-stream")
            return

        self.send_error(404)


def main() -> None:
    thread = threading.Thread(target=_poll_loop, daemon=True, name="cursor-poll")
    thread.start()

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Cursor usage dashboard: http://127.0.0.1:{PORT}")
    if not CURSOR_SESSION_TOKEN:
        print("WARN: CURSOR_SESSION_TOKEN no configurado — editá cursor-usage-dashboard/.env")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
