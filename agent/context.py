"""Elliott/Fib trade context per symbol — file + ephemeral override."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent.config import PROJECT_ROOT, get_config, resolve_path

_cache: dict[str, "TradeContext | None"] = {}


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


def get_context(symbol: str, override: dict[str, Any] | None = None) -> TradeContext | None:
    """Load context from file, optionally merged with ephemeral override."""
    sym = symbol.upper()
    if sym not in _cache:
        path = _context_path(sym)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _cache[sym] = TradeContext.from_dict(data, sym)
        else:
            _cache[sym] = None

    base = _cache[sym]
    if base is None and override is None:
        return None
    if base is None:
        return TradeContext.from_dict(override or {}, sym)
    return base.merge(override)


def set_context(symbol: str, data: dict[str, Any]) -> TradeContext:
    """Persist context to context/SYMBOL.yaml."""
    sym = symbol.upper()
    existing = get_context(sym)
    if existing:
        ctx = existing.merge(data)
    else:
        ctx = TradeContext.from_dict(data, sym)

    path = _context_path(sym)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(ctx.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    _cache[sym] = ctx
    return ctx


def clear_context_cache() -> None:
    _cache.clear()


def list_contexts() -> list[str]:
    folder = _context_dir()
    return sorted(p.stem for p in folder.glob("*.yaml"))
