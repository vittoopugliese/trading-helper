"""Rich terminal output for evidence bundles."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.analyst import EvidenceBundle

console = Console()


def _grade_style(grade: str) -> str:
    if grade == "A+":
        return "bold green"
    if grade == "B":
        return "yellow"
    return "dim"


def print_bundle(bundle: EvidenceBundle, as_json: bool = False) -> None:
    """Render evidence bundle to terminal."""
    if as_json:
        console.print_json(json.dumps(bundle.to_dict(), indent=2, default=str))
        return

    verdict = bundle.verdict
    grade = verdict.get("grade", "NO_TRADE")
    direction = verdict.get("direction", "none").upper()

    header = Text()
    header.append(f"{grade} ", style=_grade_style(grade))
    header.append(f"- {bundle.symbol} - {bundle.timeframe} - {direction} - {bundle.trade_type.upper()}")

    console.print(Panel(header, title="Trading Helper Agent", border_style="blue"))
    console.print(verdict.get("summary", ""))

    if bundle.trade_context:
        ctx = bundle.trade_context
        console.print(
            f"\n[bold]Elliott Context:[/] {ctx.get('wave', '')} | bias={ctx.get('bias')} | "
            f"POI=[{ctx.get('poi', {}).get('low')}, {ctx.get('poi', {}).get('high')}]"
        )

    levels = verdict.get("levels", {})
    if levels:
        console.print(
            f"\n[bold]Levels:[/] Entry {levels.get('entry')} | "
            f"Stop {levels.get('stop')} | Target {levels.get('target')} | "
            f"R:R {levels.get('risk_reward')} [{levels.get('source', 'atr')}]"
        )

    pos = verdict.get("position_size", {})
    if pos and pos.get("position_size"):
        console.print(
            f"[bold]Sizing (2%):[/] Risk ${pos.get('risk_amount')} | "
            f"Size {pos.get('position_size')} units | per-unit risk {pos.get('per_unit_risk')}"
        )

    if verdict.get("at_poi"):
        console.print("[green]Price at POI[/green]")
    if verdict.get("of_patterns"):
        console.print(f"[bold]OF patterns:[/] {', '.join(verdict.get('of_patterns', []))}")

    table = Table(title="Condition Checklist", show_header=True)
    table.add_column("Condition", style="cyan")
    table.add_column("Pass", justify="center")
    table.add_column("Weight", justify="right")
    table.add_column("Detail")

    for cond in verdict.get("conditions", []):
        passed = "Y" if cond.get("passed") else "N"
        style = "green" if cond.get("passed") else "red"
        crit = " *" if cond.get("critical") else ""
        table.add_row(
            cond.get("name", "") + crit,
            Text(passed, style=style),
            f"{cond.get('weight', 0):.0%}",
            cond.get("detail", ""),
        )
    console.print(table)

    snap = bundle.snapshot
    console.print("\n[bold]Order Flow[/]")
    of = snap.get("order_flow", {})
    delta_val = of.get("recent_delta_sum") or of.get("recent_delta_sum_5")
    console.print(
        f"  Bias: {of.get('bias')} ({of.get('source', 'klines')}) | Delta: {delta_val} | "
        f"buy_ratio: {of.get('buy_ratio', 'n/a')} | CVD div: {of.get('cvd_divergence', 'none')}"
    )
    if of.get("book_ratio"):
        console.print(
            f"  Book: bid/ask ratio={of.get('book_ratio')} bias={of.get('book_bias')}"
        )

    console.print("[bold]Structure[/]")
    st = snap.get("structure", {})
    console.print(f"  Trend: {st.get('trend')} | Swings: {st.get('swing_count')}")

    deriv = snap.get("derivatives", {})
    if "error" not in deriv:
        console.print("[bold]Derivatives[/]")
        console.print(
            f"  OI: {deriv.get('open_interest')} | OI chg 1h: {deriv.get('oi_change_1h')} | "
            f"Funding: {deriv.get('funding_rate')} | L/S: {deriv.get('long_short_ratio')}"
        )

    if bundle.relevant_notes:
        console.print("\n[bold]Relevant Notes[/]")
        for note in bundle.relevant_notes[:5]:
            title = note.get("title") or note.get("source_file")
            rel = note.get("relevance")
            console.print(f"  - [{rel}] {title}")
            preview = (note.get("content") or "")[:200].replace("\n", " ")
            console.print(f"    {preview}...")

    if bundle.deep_analysis:
        console.print(Panel(bundle.deep_analysis, title="Deep Analysis (Claude)", border_style="magenta"))

    console.print(f"\n[dim]Saved at {bundle.created_at}[/dim]")


def print_alert(bundle: EvidenceBundle) -> None:
    """Print prominent A+ alert."""
    console.print()
    console.print(
        Panel(
            f"[bold green]A+ SETUP DETECTED[/bold green]\n\n"
            f"{bundle.symbol} | {bundle.timeframe} | {bundle.trade_type.upper()} | "
            f"{bundle.verdict.get('direction', '').upper()}\n\n"
            f"{bundle.verdict.get('summary', '')}",
            title="ALERT",
            border_style="green",
        )
    )
    console.print()


def print_scan_results(results: list[dict[str, Any]]) -> None:
    """Print scheduler scan summary."""
    table = Table(title="Watchlist Scan")
    table.add_column("Symbol")
    table.add_column("TF")
    table.add_column("Grade")
    table.add_column("Direction")
    table.add_column("Score")
    table.add_column("POI")
    table.add_column("OF")

    for r in results:
        grade = r.get("grade", "")
        style = "green" if grade == "A+" else "dim"
        table.add_row(
            r.get("symbol", ""),
            r.get("timeframe", ""),
            Text(grade, style=style),
            r.get("direction", ""),
            f"{r.get('score', 0):.0%}",
            "Y" if r.get("at_poi") else "N",
            ", ".join(r.get("of_patterns", [])) or "-",
        )
    console.print(table)
