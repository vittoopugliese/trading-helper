"""Complete, resumable Binance USD-M Futures kline history downloads."""

from __future__ import annotations

import sqlite3
import time
import csv
import hashlib
import json
import zipfile
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from agent import market_data
from agent.config import resolve_path

ProgressCallback = Callable[[str], None]


@dataclass
class CoverageReport:
    symbol: str
    interval: str
    rows: int
    first_open_time: str | None
    last_open_time: str | None
    expected_rows_in_span: int
    missing_rows_in_span: int
    gap_count: int
    closed_through: str | None
    complete_to_last_closed_bar: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DownloadResult:
    symbol: str
    interval: str
    first_available_ms: int
    requested_end_ms: int
    rows_received: int
    requests: int
    coverage: CoverageReport

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["coverage"] = self.coverage.to_dict()
        return result


def utc_ms(value: str | datetime | None) -> int | None:
    """Parse an ISO-8601 timestamp/date into UTC epoch milliseconds."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def iso_utc(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _last_closed_open_ms(interval: str, now_ms: int | None = None) -> int:
    step = market_data.INTERVAL_MS[interval]
    now = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    return (now // step) * step - step


def discover_first_open_time(symbol: str, interval: str) -> int:
    """Ask Binance for the first available contract kline, not a guessed listing date."""
    rows = market_data.fetch_klines(symbol, interval, limit=1, start_time=0)
    if not rows:
        raise ValueError(f"Binance returned no {interval} klines for {symbol.upper()}")
    return int(rows[0][0])


def _coverage_bounds(
    symbol: str,
    interval: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> tuple[int | None, int | None, int]:
    market_data.init_cache_db()
    clauses = ["symbol=?", "interval=?"]
    params: list[Any] = [symbol.upper(), interval]
    if start_ms is not None:
        clauses.append("open_time>=?")
        params.append(start_ms)
    if end_ms is not None:
        clauses.append("open_time<=?")
        params.append(end_ms)
    with closing(sqlite3.connect(market_data._db_path())) as conn, conn:  # package-internal research helper
        row = conn.execute(
            f"SELECT MIN(open_time), MAX(open_time), COUNT(*) FROM klines WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
    return (row[0], row[1], int(row[2])) if row else (None, None, 0)


def _gap_ranges(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, int]]:
    """Return absent ranges, including leading/trailing holes, using SQLite windows."""
    step = market_data.INTERVAL_MS[interval]
    ranges: list[tuple[int, int]] = []
    market_data.init_cache_db()
    with closing(sqlite3.connect(market_data._db_path())) as conn, conn:
        bounds = conn.execute(
            """SELECT MIN(open_time), MAX(open_time) FROM klines
               WHERE symbol=? AND interval=? AND open_time BETWEEN ? AND ?""",
            (symbol.upper(), interval, start_ms, end_ms),
        ).fetchone()
        first, last = bounds if bounds else (None, None)
        if first is None:
            return [(start_ms, end_ms)]
        if first > start_ms:
            ranges.append((start_ms, first - step))
        internal = conn.execute(
            """
            SELECT open_time + ? AS gap_start, next_time - ? AS gap_end
            FROM (
                SELECT open_time,
                       LEAD(open_time) OVER (ORDER BY open_time) AS next_time
                FROM klines
                WHERE symbol=? AND interval=? AND open_time BETWEEN ? AND ?
            )
            WHERE next_time IS NOT NULL AND next_time - open_time > ?
            """,
            (step, step, symbol.upper(), interval, start_ms, end_ms, step),
        ).fetchall()
        ranges.extend((int(a), int(b)) for a, b in internal)
        if last < end_ms:
            ranges.append((last + step, end_ms))
    return ranges


def coverage_report(
    symbol: str,
    interval: str,
    expected_start_ms: int | None = None,
    expected_end_ms: int | None = None,
) -> CoverageReport:
    """Summarize cached coverage and distinguish exchange gaps from a complete span."""
    if interval not in market_data.INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")
    first, last, rows = _coverage_bounds(symbol, interval, expected_start_ms, expected_end_ms)
    step = market_data.INTERVAL_MS[interval]
    span_expected = int((last - first) // step + 1) if first is not None and last is not None else 0
    gap_count = 0
    if first is not None and last is not None:
        with closing(sqlite3.connect(market_data._db_path())) as conn, conn:
            gap_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT open_time, LEAD(open_time) OVER (ORDER BY open_time) AS next_time
                        FROM klines WHERE symbol=? AND interval=?
                          AND (? IS NULL OR open_time>=?)
                          AND (? IS NULL OR open_time<=?)
                    ) WHERE next_time IS NOT NULL AND next_time-open_time > ?
                    """,
                    (
                        symbol.upper(), interval,
                        expected_start_ms, expected_start_ms,
                        expected_end_ms, expected_end_ms,
                        step,
                    ),
                ).fetchone()[0]
            )
    target_end = expected_end_ms if expected_end_ms is not None else _last_closed_open_ms(interval)
    target_start = expected_start_ms if expected_start_ms is not None else first
    complete = bool(
        rows
        and first is not None
        and last is not None
        and (target_start is None or first <= target_start)
        and last >= target_end
        and gap_count == 0
        and span_expected == rows
    )
    return CoverageReport(
        symbol=symbol.upper(),
        interval=interval,
        rows=rows,
        first_open_time=iso_utc(first),
        last_open_time=iso_utc(last),
        expected_rows_in_span=span_expected,
        missing_rows_in_span=max(span_expected - rows, 0),
        gap_count=gap_count,
        closed_through=iso_utc(target_end),
        complete_to_last_closed_bar=complete,
    )


def _fetch_page_with_retry(
    session: requests.Session,
    symbol: str,
    interval: str,
    cursor_ms: int,
    end_ms: int,
    retries: int,
) -> list[list[Any]]:
    url = f"{market_data._base_url()}/fapi/v1/klines"
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        # 1,000 costs weight 5; >1,000 costs weight 10. This is materially more
        # efficient than the endpoint maximum of 1,500.
        "limit": 1000,
        "startTime": cursor_ms,
        "endTime": end_ms,
    }
    for attempt in range(retries + 1):
        response = session.get(url, params=params, timeout=30)
        if response.status_code not in (418, 429) and response.status_code < 500:
            response.raise_for_status()
            return response.json()
        if attempt == retries:
            response.raise_for_status()
        retry_after = float(response.headers.get("Retry-After", 0) or 0)
        time.sleep(max(retry_after, min(2**attempt, 20)))
    return []


def _month_starts(start_ms: int, end_ms: int) -> list[datetime]:
    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    last = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    months: list[datetime] = []
    while cursor <= last:
        months.append(cursor)
        cursor = datetime(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1, tzinfo=timezone.utc)
    return months


def import_data_vision_monthly(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    progress: ProgressCallback | None = None,
) -> dict[str, int]:
    """Import immutable monthly Binance archives, verifying their SHA-256 files."""
    symbol = symbol.upper()
    archive_root = resolve_path(f"data/binance_archives/klines/{symbol}/{interval}")
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest_path = resolve_path("data/binance_archives/manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        manifest = {}
    imported_rows = 0
    downloaded_files = 0
    with requests.Session() as session:
        for month in _month_starts(start_ms, end_ms):
            stamp = month.strftime("%Y-%m")
            filename = f"{symbol}-{interval}-{stamp}.zip"
            url = f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/{interval}/{filename}"
            checksum_response = session.get(f"{url}.CHECKSUM", timeout=30)
            if checksum_response.status_code == 404:
                continue
            checksum_response.raise_for_status()
            expected_sha = checksum_response.text.strip().split()[0].lower()
            archive_path = archive_root / filename
            valid_local = archive_path.exists() and hashlib.sha256(archive_path.read_bytes()).hexdigest() == expected_sha
            manifest_key = f"klines/{symbol}/{interval}/{stamp}"
            known = manifest.get(manifest_key, {})
            if valid_local and known.get("sha256") == expected_sha and known.get("rows"):
                next_month = datetime(
                    month.year + (month.month == 12),
                    1 if month.month == 12 else month.month + 1,
                    1,
                    tzinfo=timezone.utc,
                )
                cached_rows = _coverage_bounds(
                    symbol,
                    interval,
                    int(month.timestamp() * 1000),
                    int(next_month.timestamp() * 1000) - 1,
                )[2]
                if cached_rows >= int(known["rows"]):
                    if progress:
                        progress(f"{symbol} {interval}: archive {stamp} ya importado")
                    continue
            if not valid_local:
                if progress:
                    progress(f"{symbol} {interval}: descargando archive {stamp}")
                response = session.get(url, timeout=120)
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                temporary = archive_path.with_suffix(".zip.part")
                temporary.write_bytes(response.content)
                actual_sha = hashlib.sha256(response.content).hexdigest()
                if actual_sha != expected_sha:
                    temporary.unlink(missing_ok=True)
                    raise ValueError(f"Checksum mismatch for {url}")
                temporary.replace(archive_path)
                downloaded_files += 1

            batch: list[list[Any]] = []
            month_rows = 0
            with zipfile.ZipFile(archive_path) as zipped:
                csv_names = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
                if len(csv_names) != 1:
                    raise ValueError(f"Expected exactly one CSV in {archive_path}")
                with zipped.open(csv_names[0]) as raw:
                    reader = csv.reader((line.decode("utf-8") for line in raw))
                    for row in reader:
                        if not row or not row[0].isdigit():
                            continue
                        # Binance's archive timestamp unit has varied by dataset;
                        # SQLite's canonical schema remains epoch milliseconds.
                        if int(row[0]) > 100_000_000_000_000:
                            row[0] = str(int(row[0]) // 1000)
                        if int(row[6]) > 100_000_000_000_000:
                            row[6] = str(int(row[6]) // 1000)
                        open_, high, low, close = map(float, row[1:5])
                        volume, taker_buy = float(row[5]), float(row[9])
                        if min(open_, high, low, close, volume, taker_buy) < 0 or high < max(open_, close, low) or low > min(open_, close, high) or taker_buy > volume + 1e-9:
                            raise ValueError(f"Invalid OHLCV row in {filename}: {row[:10]}")
                        batch.append(row)
                        if len(batch) >= 10_000:
                            market_data.save_klines(symbol, interval, batch)
                            month_rows += len(batch)
                            batch.clear()
                    if batch:
                        market_data.save_klines(symbol, interval, batch)
                        month_rows += len(batch)
            imported_rows += month_rows
            manifest[manifest_key] = {
                "url": url,
                "sha256": expected_sha,
                "bytes": archive_path.stat().st_size,
                "rows": month_rows,
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": 1,
            }
            if progress:
                progress(f"{symbol} {interval}: archive {stamp} verificado ({month_rows:,} filas)")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"imported_rows": imported_rows, "downloaded_files": downloaded_files}


def download_complete_history(
    symbol: str,
    interval: str = "1m",
    start_ms: int | None = None,
    end_ms: int | None = None,
    repair_gaps: bool = True,
    # 5 request-weight every 0.25s ~= 1,200/min, comfortably below USD-M's
    # current 2,400/min cap while leaving room for the live watcher.
    pause_seconds: float = 0.25,
    retries: int = 5,
    use_archive: bool = True,
    progress: ProgressCallback | None = None,
) -> DownloadResult:
    """Download every closed kline in the requested range, safely resuming the cache."""
    symbol = symbol.upper()
    if interval not in market_data.INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")
    first_available = discover_first_open_time(symbol, interval)
    requested_start = max(start_ms if start_ms is not None else first_available, first_available)
    requested_end = min(end_ms if end_ms is not None else _last_closed_open_ms(interval), _last_closed_open_ms(interval))
    if requested_end < requested_start:
        raise ValueError("end time is before the first available closed kline")

    if use_archive:
        current_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        archive_end = min(
            requested_end,
            int(current_month.timestamp() * 1000) - market_data.INTERVAL_MS[interval],
        )
        if archive_end >= requested_start:
            import_data_vision_monthly(symbol, interval, requested_start, archive_end, progress)

    ranges = _gap_ranges(symbol, interval, requested_start, requested_end)
    if not repair_gaps and ranges:
        first_cached, last_cached, _ = _coverage_bounds(symbol, interval)
        ranges = []
        if first_cached is None:
            ranges = [(requested_start, requested_end)]
        elif last_cached is not None and last_cached < requested_end:
            ranges = [(last_cached + market_data.INTERVAL_MS[interval], requested_end)]

    received = 0
    request_count = 0
    step = market_data.INTERVAL_MS[interval]
    with requests.Session() as session:
        for range_index, (range_start, range_end) in enumerate(ranges, start=1):
            cursor = range_start
            if progress:
                progress(
                    f"{symbol} {interval}: tramo {range_index}/{len(ranges)} "
                    f"{iso_utc(range_start)} -> {iso_utc(range_end)}"
                )
            while cursor <= range_end:
                rows = _fetch_page_with_retry(session, symbol, interval, cursor, range_end, retries)
                request_count += 1
                if not rows:
                    break
                market_data.save_klines(symbol, interval, rows)
                received += len(rows)
                newest = int(rows[-1][0])
                if newest < cursor:
                    raise RuntimeError(f"Binance pagination did not advance at {iso_utc(cursor)}")
                cursor = newest + step
                if progress and (request_count == 1 or request_count % 25 == 0):
                    progress(f"{symbol} {interval}: {received:,} filas recibidas; hasta {iso_utc(newest)}")
                if pause_seconds:
                    time.sleep(pause_seconds)

    report = coverage_report(symbol, interval, requested_start, requested_end)
    return DownloadResult(
        symbol=symbol,
        interval=interval,
        first_available_ms=first_available,
        requested_end_ms=requested_end,
        rows_received=received,
        requests=request_count,
        coverage=report,
    )
