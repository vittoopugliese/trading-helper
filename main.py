"""Trading Helper Agent — CLI entrypoint."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console

from agent import analyst, context as trade_context, display, knowledge, market_data
from agent.mcp_server import run_mcp
from agent.repl import run_repl
from agent.scheduler import SchedulerService

app = typer.Typer(
    name="trading-helper",
    help="Analisis asistido de crypto futures — Elliott + Order Flow + A+ setups",
    no_args_is_help=True,
)
console = Console()


@app.command()
def analyze(
    symbol: str = typer.Argument(..., help="Par Binance Futures, ej: BTCUSDT"),
    timeframe: str = typer.Argument("4h", help="Timeframe: 15m, 1h, 4h"),
    trade_type: str = typer.Argument("swing", help="swing o scalp"),
    deep: bool = typer.Option(False, "--deep", help="Usar Claude API para analisis profundo"),
    compare: bool = typer.Option(False, "--compare", help="Comparar con analisis anterior"),
    json_output: bool = typer.Option(False, "--json", help="Salida JSON"),
) -> None:
    """Analizar un par y obtener veredicto A+ setup."""
    try:
        bundle = analyst.analyze(
            symbol.upper(),
            timeframe,
            trade_type.lower(),
            deep=deep,
            compare_previous=compare,
        )
        display.print_bundle(bundle, as_json=json_output)
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def snapshot(
    symbol: str = typer.Argument(...),
    timeframe: str = typer.Argument("4h"),
) -> None:
    """Mostrar snapshot de mercado sin scoring."""
    from agent import metrics

    try:
        snap = metrics.build_snapshot(symbol.upper(), timeframe)
        console.print_json(json.dumps(snap.to_dict(), indent=2, default=str))
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def fetch(
    symbol: str = typer.Argument(...),
    timeframe: str = typer.Argument("4h"),
    limit: int = typer.Option(10, help="Velas a mostrar"),
) -> None:
    """Fetch y cache de velas (Fase 1 test)."""
    market_data.init_cache_db()
    count = market_data.update_cache(symbol.upper(), timeframe)
    df = market_data.load_klines_df(symbol.upper(), timeframe, limit=limit)
    console.print(f"[green]Cached {count} new candles[/green]")
    if not df.empty:
        console.print(df.tail(limit).to_string(index=False))


@app.command("reindex")
def reindex_cmd(
    force: bool = typer.Option(False, "--force", help="Forzar reindexado completo"),
) -> None:
    """Re-indexar notas en ChromaDB."""
    stats = knowledge.index_notes(force=force)
    console.print(f"[green]Indexado:[/green] {stats}")


@app.command()
def search(
    query: str = typer.Argument(...),
    top_k: int = typer.Option(5, "--top-k", "-k"),
) -> None:
    """Buscar en notas indexadas."""
    results = knowledge.search_notes(query, top_k=top_k)
    for r in results:
        console.print(f"[cyan]{r.get('title')}[/] - {r.get('source_file')} (rel={r.get('relevance')})")
        console.print(f"  {(r.get('content') or '')[:400]}\n")


@app.command()
def scan() -> None:
    """Escanear watchlist una vez."""
    scheduler = SchedulerService(on_alert=display.print_alert)
    results = scheduler.run_once()
    display.print_scan_results(results)


@app.command()
def repl(
    no_scheduler: bool = typer.Option(False, "--no-scheduler", help="Desactivar scan en background"),
) -> None:
    """Modo interactivo REPL."""
    run_repl(with_scheduler=not no_scheduler)


@app.command()
def journal(
    symbol: str | None = typer.Option(None, help="Filtrar por simbolo"),
) -> None:
    """Mostrar estadisticas del journal de trades."""
    stats = analyst.journal_stats(symbol.upper() if symbol else None)
    console.print_json(json.dumps(stats, indent=2))


@app.command("log-trade")
def log_trade_cmd(
    symbol: str = typer.Argument(...),
    timeframe: str = typer.Argument(...),
    direction: str = typer.Argument(..., help="long o short"),
    trade_type: str = typer.Option("swing", help="swing o scalp"),
    entry: float | None = typer.Option(None),
    stop: float | None = typer.Option(None),
    target: float | None = typer.Option(None),
    rr: float | None = typer.Option(None, help="R:R planeado"),
    wave: str = typer.Option("", help="Onda Elliott"),
    result: str = typer.Option("open", help="open|win|loss|breakeven"),
    emotion: str = typer.Option(""),
    notes: str = typer.Option(""),
) -> None:
    """Registrar un trade en el journal."""
    trade_id = analyst.log_trade(
        symbol.upper(),
        timeframe,
        trade_type,
        direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_planned=rr,
        wave=wave,
        result=result,
        emotion=emotion,
        notes=notes,
    )
    console.print(f"[green]Trade #{trade_id} logged[/green]")


@app.command("show-context")
def show_context_cmd(symbol: str = typer.Argument(...)) -> None:
    """Mostrar contexto Elliott/Fib guardado para un simbolo."""
    ctx = trade_context.get_context(symbol.upper())
    if ctx is None:
        console.print(f"[yellow]No context for {symbol.upper()}. Create context/{symbol.upper()}.yaml[/yellow]")
        raise typer.Exit(1)
    console.print_json(json.dumps(ctx.to_dict(), indent=2, ensure_ascii=False))


@app.command()
def mcp() -> None:
    """Iniciar servidor MCP para Cursor."""
    run_mcp()


@app.callback()
def main() -> None:
    """Trading Helper Agent."""
    market_data.init_cache_db()
    analyst.init_sessions_db()


if __name__ == "__main__":
    app()
