"""Elliott/Fib trade context per symbol — file + ephemeral override."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agent.config import PROJECT_ROOT, get_config, resolve_path

# symbol -> (file mtime or None when missing, parsed context). Re-read when the file changes,
# so an edit from the web, the CLI or a text editor reaches a running watcher.
_cache: dict[str, tuple[float | None, "TradeContext | None"]] = {}

DIRECTIONS = ("long", "short")


@dataclass
class TradeContext:
    """User-defined Elliott/Fib context for a symbol."""

    symbol: str
    bias: str = "none"  # long, short, none
    wave: str = ""
    trade_type: str = "swing"
    poi_low: float | None = None
    poi_high: float | None = None
    invalidation: float | None = None
    targets: list[float] = field(default_factory=list)
    fib: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    account_size: float | None = None
    risk_pct: float = 2.0
    chart_image: str = ""
    as_of: str = ""  # ISO timestamp of the last edit; age limit in thresholds.context_max_age_days
    scenarios_raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bias": self.bias,
            "wave": self.wave,
            "trade_type": self.trade_type,
            "poi": {"low": self.poi_low, "high": self.poi_high},
            "invalidation": self.invalidation,
            "targets": self.targets,
            "fib": self.fib,
            "notes": self.notes,
            "account_size": self.account_size,
            "risk_pct": self.risk_pct,
            "chart_image": self.chart_image,
            "as_of": self.as_of,
            **({"scenarios": self.scenarios_raw} if self.scenarios_raw else {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], symbol: str | None = None) -> "TradeContext":
        poi = data.get("poi") or {}
        if isinstance(poi, list) and len(poi) >= 2:
            poi_low, poi_high = float(poi[0]), float(poi[1])
        else:
            poi_low = poi.get("low")
            poi_high = poi.get("high")
            if poi_low is not None:
                poi_low = float(poi_low)
            if poi_high is not None:
                poi_high = float(poi_high)

        targets = data.get("targets") or []
        if isinstance(targets, (int, float)):
            targets = [float(targets)]
        else:
            targets = [float(t) for t in targets]

        fib = data.get("fib") or {}
        fib = {str(k): float(v) for k, v in fib.items()}

        sym = (symbol or data.get("symbol") or "").upper()
        return cls(
            symbol=sym,
            bias=str(data.get("bias", "none")).lower(),
            wave=str(data.get("wave", "")),
            trade_type=str(data.get("trade_type", "swing")).lower(),
            poi_low=poi_low,
            poi_high=poi_high,
            invalidation=float(data["invalidation"]) if data.get("invalidation") is not None else None,
            targets=targets,
            fib=fib,
            notes=str(data.get("notes", "")),
            account_size=float(data["account_size"]) if data.get("account_size") is not None else None,
            risk_pct=float(data.get("risk_pct", 2.0)),
            chart_image=str(data.get("chart_image", "")),
            as_of=str(data.get("as_of") or ""),
            scenarios_raw=dict(data.get("scenarios") or {}),
        )

    def has_poi(self) -> bool:
        return self.poi_low is not None and self.poi_high is not None

    def poi_mid(self) -> float | None:
        if not self.has_poi():
            return None
        return (self.poi_low + self.poi_high) / 2  # type: ignore[operator]

    def price_in_poi(self, price: float, tolerance_pct: float = 0.0) -> bool:
        if not self.has_poi():
            return False
        low = self.poi_low * (1 - tolerance_pct)  # type: ignore[operator]
        high = self.poi_high * (1 + tolerance_pct)  # type: ignore[operator]
        return low <= price <= high

    def price_near_poi(self, price: float, proximity_pct: float) -> bool:
        """True if price is inside POI or within proximity_pct of its edges."""
        if not self.has_poi():
            return False
        if self.price_in_poi(price):
            return True
        mid = self.poi_mid()
        if mid is None:
            return False
        band = mid * proximity_pct
        return (self.poi_low - band) <= price <= (self.poi_high + band)  # type: ignore[operator]

    def invalidation_respected(self, price: float) -> bool:
        if self.invalidation is None or self.bias == "none":
            return True
        if self.bias == "long":
            return price > self.invalidation
        if self.bias == "short":
            return price < self.invalidation
        return True

    def poi_drift_pct(self, price: float) -> float | None:
        """Distancia relativa del precio al borde mas cercano del POI (0.0 si esta adentro)."""
        if not self.has_poi() or price <= 0:
            return None
        low = float(self.poi_low)  # type: ignore[arg-type]
        high = float(self.poi_high)  # type: ignore[arg-type]
        if low <= price <= high:
            return 0.0
        gap = low - price if price < low else price - high
        return gap / price

    def is_stale(self, price: float, max_drift_pct: float | None = None) -> bool:
        """True cuando el precio se fue lejos del POI: el contexto guardado quedo viejo.

        Los niveles (POI, invalidacion, targets) dejan de describir el mercado actual, asi
        que cualquier entry/stop/RR derivado de ellos es ficcion. Ver notes/setup-rules.
        """
        drift = self.poi_drift_pct(price)
        if drift is None:
            return False
        if max_drift_pct is None:
            max_drift_pct = float(get_config()["thresholds"].get("context_stale_pct", 0.08))
        return drift > max_drift_pct

    # ------------------------------------------------------------------ scenarios

    def main_geometry(self) -> str | None:
        """Direction implied by the main POI / invalidation / targets, None if incoherent."""
        if not self.has_poi() or self.invalidation is None:
            return None
        lo, hi, inv = float(self.poi_low), float(self.poi_high), float(self.invalidation)  # type: ignore[arg-type]
        if inv < lo and all(t > hi for t in self.targets):
            return "long"
        if inv > hi and all(t < lo for t in self.targets):
            return "short"
        return None

    def scenarios(self) -> dict[str, dict[str, Any]]:
        """Long/short plans with coherent geometry, from (in priority order) the explicit
        `scenarios:` block, the main levels (by their geometry) and the `fib` long_/short_ keys.
        A scenario with levels on the wrong side is dropped, never "fixed"."""
        out: dict[str, dict[str, Any]] = {}

        def add(direction: str, lo: Any, hi: Any, inv: Any, targets: list[Any], source: str) -> None:
            if direction in out or None in (lo, hi, inv):
                return
            lo, hi, inv = float(lo), float(hi), float(inv)
            tgs = [float(t) for t in targets if t is not None]
            if not tgs or lo > hi:
                return
            if direction == "long":
                ok = inv < lo and all(t > hi for t in tgs)
            else:
                ok = inv > hi and all(t < lo for t in tgs)
            if ok:
                out[direction] = {"poi_low": lo, "poi_high": hi, "invalidation": inv, "targets": tgs, "source": source}

        for direction in DIRECTIONS:
            sc = self.scenarios_raw.get(direction) or {}
            poi = sc.get("poi") or [sc.get("poi_low"), sc.get("poi_high")]
            if isinstance(poi, dict):
                poi = [poi.get("low"), poi.get("high")]
            add(direction, poi[0], poi[1], sc.get("invalidation"), list(sc.get("targets") or []), "scenarios")
        geometry = self.main_geometry()
        if geometry:
            add(geometry, self.poi_low, self.poi_high, self.invalidation, self.targets, "principal")
        fib = self.fib or {}
        add("short", fib.get("short_poi_low"), fib.get("short_poi_high"), fib.get("short_invalidation"),
            [fib.get("short_target_1"), fib.get("short_target_2")], "fib.short_*")
        add("long", fib.get("long_scalp_low"), fib.get("long_scalp_high"), fib.get("long_scalp_invalidation"),
            [fib.get("long_scalp_target_1"), fib.get("long_scalp_target_2")], "fib.long_scalp_*")
        return out

    def scenario(self, direction: str) -> dict[str, Any] | None:
        return self.scenarios().get(direction)

    def age_days(self, now: datetime | None = None) -> float | None:
        if not self.as_of:
            return None
        try:
            ts = datetime.fromisoformat(self.as_of)
        except ValueError:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ((now or datetime.now(timezone.utc)) - ts).total_seconds() / 86400

    def is_expired(self, now: datetime | None = None) -> bool:
        age = self.age_days(now)
        max_days = float(get_config()["thresholds"].get("context_max_age_days", 7))
        return age is not None and age > max_days

    def validate(self) -> list[str]:
        """Contradictions a human must resolve. Empty list = coherent context."""
        issues: list[str] = []
        if self.has_poi() and float(self.poi_low) > float(self.poi_high):  # type: ignore[arg-type]
            issues.append("POI low > POI high")
        geometry = self.main_geometry()
        if self.has_poi() and self.invalidation is not None and geometry is None:
            issues.append("niveles principales incoherentes: invalidacion y targets no quedan en lados opuestos del POI")
        if self.bias in DIRECTIONS and geometry and geometry != self.bias:
            issues.append(f"bias '{self.bias}' pero los niveles principales describen un {geometry}")
        if self.bias in DIRECTIONS and self.has_poi() and self.bias not in self.scenarios():
            issues.append(f"no hay escenario {self.bias} coherente para el bias declarado")
        return issues

    def nearest_fib(self, price: float) -> str | None:
        if not self.fib:
            return None
        best = min(self.fib.items(), key=lambda kv: abs(kv[1] - price))
        return f"{best[0]} ({best[1]:.4f})"

    def merge(self, override: "TradeContext | dict[str, Any] | None") -> "TradeContext":
        """Return a copy with override fields applied (ephemeral chat override)."""
        if override is None:
            return TradeContext.from_dict(self.to_dict(), self.symbol)

        if isinstance(override, TradeContext):
            od = override.to_dict()
        else:
            od = override

        base = self.to_dict()
        for key, val in od.items():
            if key == "poi" and isinstance(val, dict):
                if val.get("low") is not None:
                    base["poi"]["low"] = val["low"]
                if val.get("high") is not None:
                    base["poi"]["high"] = val["high"]
            elif val is not None and val != "" and val != [] and val != {}:
                base[key] = val
        return TradeContext.from_dict(base, self.symbol)


def _context_dir() -> Path:
    cfg = get_config()
    path = resolve_path(cfg["paths"].get("context_dir", "context"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _context_path(symbol: str) -> Path:
    return _context_dir() / f"{symbol.upper()}.yaml"


def _load(sym: str) -> TradeContext | None:
    path = _context_path(sym)
    mtime = path.stat().st_mtime if path.exists() else None
    cached = _cache.get(sym)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    ctx = None
    if mtime is not None:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        ctx = TradeContext.from_dict(data, sym)
        if not ctx.as_of:
            # Files written before `as_of` existed: the last edit is the best evidence of age.
            ctx.as_of = datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")
    _cache[sym] = (mtime, ctx)
    return ctx


def get_context(symbol: str, override: dict[str, Any] | None = None) -> TradeContext | None:
    """Load context from file (re-read when it changes), optionally merged with an override."""
    sym = symbol.upper()
    base = _load(sym)
    if base is None and override is None:
        return None
    if base is None:
        return TradeContext.from_dict(override or {}, sym)
    return base.merge(override)


class ContextValidationError(ValueError):
    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues))
        self.issues = issues


def set_context(symbol: str, data: dict[str, Any], *, allow_invalid: bool = False) -> TradeContext:
    """Persist context to context/SYMBOL.yaml. Refuses contradictory levels unless forced."""
    sym = symbol.upper()
    existing = get_context(sym)
    if existing:
        ctx = existing.merge(data)
    else:
        ctx = TradeContext.from_dict(data, sym)
    issues = ctx.validate()
    if issues and not allow_invalid:
        raise ContextValidationError(issues)
    ctx.as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")

    path = _context_path(sym)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(ctx.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    _cache.pop(sym, None)
    return ctx


def clear_context_cache() -> None:
    _cache.clear()


def list_contexts() -> list[str]:
    folder = _context_dir()
    return sorted(p.stem for p in folder.glob("*.yaml"))
