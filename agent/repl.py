"""Interactive REPL for Trading Helper Agent."""

from __future__ import annotations

import re
import shlex

from rich.console import Console

from agent import analyst, display, knowledge
from agent.scheduler import SchedulerService

console = Console()


def _parse_analyze_command(text: str) -> tuple[str, str, str] | None:
    """Parse natural-ish analyze commands."""
    text_lower = text.lower().strip()

    patterns = [
        r"(?:analiz[aá]|analyze|scan)\s+(\w+)\s+(?:en\s+)?(\d+[mhdw])\s*(?:,?\s*(?:para\s+)?(swing|scalp))?",
        r"(\w+)\s+(\d+[mhdw])\s+(swing|scalp)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if match:
            symbol = match.group(1).upper()
            if not symbol.endswith("USDT"):
                symbol += "USDT"
            timeframe = match.group(2)
            trade_type = (match.group(3) or "swing").lower()
            return symbol, timeframe, trade_type
    return None


def _handle_command(line: str, scheduler: SchedulerService | None) -> bool:
    """Handle one REPL command. Returns False to exit."""
    line = line.strip()
    if not line:
        return True

    if line.lower() in ("exit", "quit", "q"):
        return False

    if line.lower() in ("help", "?"):
        console.print(
            "[bold]Commands:[/bold]\n"
            "  analiza BTC en 4h swing\n"
            "  analyze ETHUSDT 15m scalp\n"
            "  search onda 3 extendida\n"
            "  reindex\n"
            "  scan\n"
            "  history\n"
            "  scheduler on|off\n"
            "  exit"
        )
        return True

    if line.lower().startswith("search "):
        query = line[7:].strip()
        results = knowledge.search_notes(query)
        for r in results:
            console.print(f"[cyan]{r.get('title')}[/] ({r.get('source_file')}) - rel={r.get('relevance')}")
            console.print(f"  {(r.get('content') or '')[:300]}...\n")
        analyst.save_repl_message("user", line)
        analyst.save_repl_message("assistant", f"Found {len(results)} notes")
        return True

    if line.lower() == "reindex":
        stats = knowledge.index_notes(force=True)
        console.print(f"Reindexed: {stats}")
        return True

    if line.lower() == "scan":
        if scheduler:
            results = scheduler.run_once()
            display.print_scan_results(results)
        else:
            console.print("[yellow]Scheduler not available[/yellow]")
        return True

    if line.lower().startswith("scheduler "):
        if not scheduler:
            console.print("[yellow]Scheduler not available[/yellow]")
            return True
        action = line.split()[1].lower()
        if action == "on":
            scheduler.start()
            console.print("[green]Scheduler started[/green]")
        elif action == "off":
            scheduler.stop()
            console.print("[yellow]Scheduler stopped[/yellow]")
        return True

    if line.lower() == "history":
        history = analyst.get_repl_history()
        for msg in history[-10:]:
            console.print(f"[dim]{msg['created_at']}[/dim] [{msg['role']}] {msg['content'][:120]}")
        return True

    parsed = _parse_analyze_command(line)
    if parsed:
        symbol, timeframe, trade_type = parsed
        compare = "compar" in line.lower() or "compare" in line.lower()
        deep = "--deep" in line.lower() or "profundo" in line.lower()
        console.print(f"[dim]Analyzing {symbol} {timeframe} ({trade_type})...[/dim]")
        try:
            bundle = analyst.analyze(symbol, timeframe, trade_type, deep=deep, compare_previous=compare)
            display.print_bundle(bundle)
            analyst.save_repl_message("user", line)
            analyst.save_repl_message("assistant", bundle.verdict.get("summary", ""))
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")
        return True

    console.print("[yellow]Unknown command. Type 'help' for options.[/yellow]")
    return True


def run_repl(with_scheduler: bool = True) -> None:
    """Start interactive REPL loop."""
    console.print("[bold blue]Trading Helper Agent[/bold blue] - type 'help' or 'exit'")
    knowledge.index_notes()

    scheduler = SchedulerService(on_alert=display.print_alert) if with_scheduler else None
    if scheduler and scheduler.enabled:
        scheduler.start()
        console.print(f"[dim]Background scan every {scheduler.interval_minutes} min[/dim]")

    try:
        while True:
            try:
                line = console.input("[bold green]> [/bold green]")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye[/dim]")
                break

            if not _handle_command(line, scheduler):
                break
    finally:
        if scheduler:
            scheduler.stop()
