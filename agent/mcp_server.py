"""MCP server exposing Trading Helper tools to Cursor agent."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from agent import analyst, context, knowledge, metrics

mcp = FastMCP("Trading Helper Agent")


@mcp.tool()
def analyze_pair(symbol: str, timeframe: str = "4h", trade_type: str = "swing", deep: bool = False) -> str:
    """Analyze a Binance Futures pair and return A+ setup evidence bundle as JSON."""
    bundle = analyst.analyze(symbol.upper(), timeframe, trade_type, deep=deep)
    return json.dumps(bundle.to_dict(), indent=2, default=str)


@mcp.tool()
def analyze_with_levels(
    symbol: str,
    timeframe: str,
    bias: str,
    poi_low: float,
    poi_high: float,
    invalidation: float,
    targets: str,
    trade_type: str = "swing",
    wave: str = "",
    notes: str = "",
    deep: bool = False,
    persist: bool = True,
) -> str:
    """Analyze with Elliott/Fib levels from chat. targets = comma-separated prices (e.g. '69.4,69.23')."""
    target_list = [float(t.strip()) for t in targets.split(",") if t.strip()]
    bundle = analyst.analyze_with_levels(
        symbol.upper(),
        timeframe,
        bias=bias,
        poi_low=poi_low,
        poi_high=poi_high,
        invalidation=invalidation,
        targets=target_list,
        trade_type=trade_type,
        wave=wave,
        notes=notes,
        deep=deep,
        persist=persist,
    )
    return json.dumps(bundle.to_dict(), indent=2, default=str)


@mcp.tool()
def get_context(symbol: str) -> str:
    """Get saved Elliott/Fib context for a symbol from context/SYMBOL.yaml."""
    ctx = context.get_context(symbol.upper())
    if ctx is None:
        return json.dumps({"message": f"No context file for {symbol.upper()}. Use set_context to create one."})
    return json.dumps(ctx.to_dict(), indent=2, ensure_ascii=False)


@mcp.tool()
def set_context(
    symbol: str,
    bias: str = "none",
    wave: str = "",
    trade_type: str = "swing",
    poi_low: float | None = None,
    poi_high: float | None = None,
    invalidation: float | None = None,
    targets: str = "",
    notes: str = "",
    account_size: float | None = None,
) -> str:
    """Save Elliott/Fib context to context/SYMBOL.yaml. targets = comma-separated prices."""
    data: dict = {
        "bias": bias,
        "wave": wave,
        "trade_type": trade_type,
        "notes": notes,
    }
    if poi_low is not None and poi_high is not None:
        data["poi"] = {"low": poi_low, "high": poi_high}
    if invalidation is not None:
        data["invalidation"] = invalidation
    if targets:
        data["targets"] = [float(t.strip()) for t in targets.split(",") if t.strip()]
    if account_size is not None:
        data["account_size"] = account_size

    ctx = context.set_context(symbol.upper(), data)
    return json.dumps({"saved": ctx.to_dict()}, indent=2, ensure_ascii=False)


@mcp.tool()
def get_market_snapshot(symbol: str, timeframe: str = "4h", trade_type: str = "swing") -> str:
    """Get raw market snapshot (indicators, real order flow, structure) without full verdict."""
    snapshot = metrics.build_snapshot(symbol.upper(), timeframe, trade_type)
    return json.dumps(snapshot.to_dict(), indent=2, default=str)


@mcp.tool()
def search_notes(query: str, top_k: int = 5, category: str | None = None) -> str:
    """Semantic search over indexed trading notes."""
    results = knowledge.search_notes(query, top_k=top_k, category=category)
    return json.dumps(results, indent=2, ensure_ascii=False)


@mcp.tool()
def reindex_notes(force: bool = False) -> str:
    """Re-index all note files from notes/ folder."""
    stats = knowledge.index_notes(force=force)
    return json.dumps(stats, indent=2)


@mcp.tool()
def get_last_analysis(symbol: str, timeframe: str | None = None) -> str:
    """Get the most recent saved analysis for a symbol."""
    result = analyst.get_last_analysis(symbol.upper(), timeframe)
    if result is None:
        return json.dumps({"message": "No previous analysis found"})
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def log_trade(
    symbol: str,
    timeframe: str,
    trade_type: str,
    direction: str,
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    rr_planned: float | None = None,
    wave: str = "",
    result: str = "open",
    rr_real: float | None = None,
    emotion: str = "",
    notes: str = "",
) -> str:
    """Log a trade for journaling (setup, R, emotion). result: open|win|loss|breakeven."""
    trade_id = analyst.log_trade(
        symbol.upper(),
        timeframe,
        trade_type,
        direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_planned=rr_planned,
        wave=wave,
        result=result,
        rr_real=rr_real,
        emotion=emotion,
        notes=notes,
    )
    return json.dumps({"trade_id": trade_id, "logged": True})


@mcp.tool()
def journal_stats(symbol: str | None = None) -> str:
    """Get win rate, avg R, stats by wave from logged trades."""
    stats = analyst.journal_stats(symbol.upper() if symbol else None)
    return json.dumps(stats, indent=2)


def run_mcp() -> None:
    """Run MCP server (stdio transport for Cursor)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_mcp()
