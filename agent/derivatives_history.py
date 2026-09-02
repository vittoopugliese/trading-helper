"""Historical Binance USD-M derivatives metrics and funding archives."""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from agent import historical, market_data
from agent.config import resolve_path

ProgressCallback = Callable[[str], None]

METRICS_EARLIEST = {
    "BTCUSDT": date(2020, 9, 1),
    "HYPEUSDT": date(2025, 5, 30),
}
FUNDING_EARLIEST = {
    "BTCUSDT": date(2020, 1, 1),
    "HYPEUSDT": date(2025, 5, 1),
}


@dataclass
class DerivativesDownloadResult:
    symbol: str
    metrics_files: int
    metrics_rows: int
    funding_files: int
    funding_rows: int
    metrics_from: str | None
    metrics_to: str | None
    funding_from: str | None
    funding_to: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _manifest_path() -> Path:
    return resolve_path("data/binance_archives/manifest.json")


def _load_manifest() -> dict[str, Any]:
    path = _manifest_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_manifest(manifest: dict[str, Any]) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _download_verified(url: str, destination: Path) -> tuple[Path, str] | None:
    checksum_response = requests.get(f"{url}.CHECKSUM", timeout=30)
    if checksum_response.status_code == 404:
        return None
    checksum_response.raise_for_status()
    expected = checksum_response.text.strip().split()[0].lower()
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        return destination, expected

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=120) as response:
        if response.status_code == 404:
            return None
        response.raise_for_status()
        with temporary.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    digest.update(chunk)
                    output.write(chunk)
    if digest.hexdigest() != expected:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"Checksum mismatch for {url}")
    temporary.replace(destination)
    return destination, expected


def _date_range(first: date, last: date) -> list[date]:
    if last < first:
        return []
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _cached_metric_dates(symbol: str) -> set[str]:
    df = market_data.load_derivatives_metrics_df(symbol)
    if df.empty:
        return set()
    counts = df.groupby(df["create_time"].dt.strftime("%Y-%m-%d")).size()
    # A complete archived day has 288 five-minute observations. Listing day can
    # be partial and is accepted when it contains any observations.
    return {str(day) for day, count in counts.items() if count >= 288 or day == METRICS_EARLIEST.get(symbol, date.min).isoformat()}


def _parse_metrics_archive(symbol: str, archive: Path) -> list[tuple[Any, ...]]:
    def optional_float(value: str | None) -> float | None:
        return float(value) if value not in (None, "") else None

    rows: list[tuple[Any, ...]] = []
    with zipfile.ZipFile(archive) as zipped:
        names = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Expected one CSV inside {archive}")
        with zipped.open(names[0]) as raw:
            reader = csv.DictReader(line.decode("utf-8") for line in raw)
            for item in reader:
                timestamp = datetime.strptime(item["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                rows.append(
                    (
                        symbol,
                        int(timestamp.timestamp() * 1000),
                        float(item["sum_open_interest"]),
                        float(item["sum_open_interest_value"]),
                        optional_float(item["sum_toptrader_long_short_ratio"]),
                        optional_float(item["count_long_short_ratio"]),
                        optional_float(item["sum_taker_long_short_vol_ratio"]),
                    )
                )
    return rows


def download_metrics(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    workers: int = 8,
    progress: ProgressCallback | None = None,
) -> tuple[int, int]:
    symbol = symbol.upper()
    first = max(start or METRICS_EARLIEST.get(symbol, date(2020, 1, 1)), METRICS_EARLIEST.get(symbol, date.min))
    last = min(end or (datetime.now(timezone.utc).date() - timedelta(days=1)), datetime.now(timezone.utc).date() - timedelta(days=1))
    cached = _cached_metric_dates(symbol)
    days = [day for day in _date_range(first, last) if day.isoformat() not in cached]
    if not days:
        return 0, 0

    root = resolve_path(f"data/binance_archives/metrics/{symbol}")
    manifest = _load_manifest()
    downloaded: list[tuple[date, Path, str]] = []

    def fetch(day: date) -> tuple[date, Path, str] | None:
        stamp = day.isoformat()
        filename = f"{symbol}-metrics-{stamp}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{filename}"
        result = _download_verified(url, root / filename)
        return (day, result[0], result[1]) if result else None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch, day): day for day in days}
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result:
                downloaded.append(result)
            if progress and (index == 1 or index % 50 == 0 or index == len(futures)):
                progress(f"{symbol} metrics: {index}/{len(futures)} archivos revisados")

    total_rows = 0
    for day, archive, checksum in sorted(downloaded):
        rows = _parse_metrics_archive(symbol, archive)
        market_data.save_derivatives_metrics(rows)
        total_rows += len(rows)
        key = f"metrics/{symbol}/{day.isoformat()}"
        manifest[key] = {
            "url": f"https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{archive.name}",
            "sha256": checksum,
            "bytes": archive.stat().st_size,
            "rows": len(rows),
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
        }
    _save_manifest(manifest)
    return len(downloaded), total_rows


def _parse_funding_archive(symbol: str, archive: Path) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    with zipfile.ZipFile(archive) as zipped:
        names = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Expected one CSV inside {archive}")
        with zipped.open(names[0]) as raw:
            reader = csv.DictReader(line.decode("utf-8") for line in raw)
            for item in reader:
                timestamp = int(item["calc_time"])
                if timestamp > 100_000_000_000_000:
                    timestamp //= 1000
                rows.append((symbol, timestamp, int(item["funding_interval_hours"]), float(item["last_funding_rate"])))
    return rows


def download_funding(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[int, int]:
    symbol = symbol.upper()
    earliest = FUNDING_EARLIEST.get(symbol, date(2020, 1, 1))
    first = max(start or earliest, earliest)
    now = datetime.now(timezone.utc)
    current_month = date(now.year, now.month, 1)
    last_complete_month_day = current_month - timedelta(days=1)
    last = min(end or last_complete_month_day, last_complete_month_day)
    months = historical._month_starts(
        int(datetime(first.year, first.month, 1, tzinfo=timezone.utc).timestamp() * 1000),
        int(datetime(last.year, last.month, 1, tzinfo=timezone.utc).timestamp() * 1000),
    )
    root = resolve_path(f"data/binance_archives/fundingRate/{symbol}")
    manifest = _load_manifest()
    total_files = total_rows = 0
    for index, month in enumerate(months, start=1):
        stamp = month.strftime("%Y-%m")
        key = f"fundingRate/{symbol}/{stamp}"
        if key in manifest:
            continue
        filename = f"{symbol}-fundingRate-{stamp}.zip"
        url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{filename}"
        result = _download_verified(url, root / filename)
        if not result:
            continue
        archive, checksum = result
        rows = _parse_funding_archive(symbol, archive)
        market_data.save_funding_rates(rows)
        total_files += 1
        total_rows += len(rows)
        manifest[key] = {
            "url": url,
            "sha256": checksum,
            "bytes": archive.stat().st_size,
            "rows": len(rows),
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
        }
        if progress and (index == 1 or index % 12 == 0 or index == len(months)):
            progress(f"{symbol} funding: {index}/{len(months)} meses")
    _save_manifest(manifest)
    return total_files, total_rows


def download_derivatives_history(
    symbol: str,
    progress: ProgressCallback | None = None,
) -> DerivativesDownloadResult:
    symbol = symbol.upper()
    metric_files, metric_rows = download_metrics(symbol, progress=progress)
    funding_files, funding_rows = download_funding(symbol, progress=progress)
    metrics = market_data.load_derivatives_metrics_df(symbol)
    funding = market_data.load_funding_rates_df(symbol)
    return DerivativesDownloadResult(
        symbol=symbol,
        metrics_files=metric_files,
        metrics_rows=metric_rows,
        funding_files=funding_files,
        funding_rows=funding_rows,
        metrics_from=metrics.iloc[0]["create_time"].isoformat() if not metrics.empty else None,
        metrics_to=metrics.iloc[-1]["create_time"].isoformat() if not metrics.empty else None,
        funding_from=funding.iloc[0]["calc_time"].isoformat() if not funding.empty else None,
        funding_to=funding.iloc[-1]["calc_time"].isoformat() if not funding.empty else None,
    )
