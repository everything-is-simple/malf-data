"""Deterministic TDX-to-MALF snapshot ingestion."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from malf.types import PriceBar, WaveStructuralSnapshot

from malf_data.adapters.duckdb_adapter import DuckDBAdapter
from malf_data.adapters.tdx_reader import read_tdx_day
from malf_data.aggregate import aggregate_to_month, aggregate_to_week
from malf_data.driver import ADAPTER_VERSION, MALFDriver


_SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("day", "week", "month")


@dataclass(frozen=True)
class IngestResult:
    """Summary of one deterministic ingest run."""

    symbol: str
    timeframe: str
    inserted_rows: int
    lineage_hash: str


def build_snapshots(
    bars: Iterable[PriceBar],
    *,
    malf_k: int = 2,
    data_stale: bool = True,
) -> list[WaveStructuralSnapshot]:
    """Run the explicit MALF driver and attach one deterministic run hash."""
    driver = MALFDriver(malf_k=malf_k, data_stale=data_stale)
    unhashed = [driver.on_bar(bar) for bar in bars]
    lineage_hash = calculate_lineage_hash(unhashed)
    return [replace(snapshot, lineage_hash=lineage_hash) for snapshot in unhashed]


def calculate_lineage_hash(snapshots: Iterable[WaveStructuralSnapshot]) -> str:
    """Hash a canonical snapshot sequence without self-referential lineage data."""
    digest = sha256()
    for snapshot in snapshots:
        payload = asdict(snapshot)
        payload["lineage_hash"] = None
        digest.update(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def ingest_bars(
    symbol: str,
    *,
    timeframe: str,
    bars: Iterable[PriceBar],
    db_path: Path,
    malf_k: int = 2,
    data_stale: bool = True,
) -> IngestResult:
    """Run the deterministic MALF driver over prepared bars and resume durable writes.

    The complete input prefix is replayed through Core on every run so a resumed
    run preserves stateful engine semantics; only already committed bars are
    excluded from DuckDB writes (partitioned by symbol + timeframe).
    """
    snapshots = build_snapshots(bars, malf_k=malf_k, data_stale=data_stale)

    inserted_rows = 0
    with DuckDBAdapter(db_path) as adapter:
        last_bar_dt = adapter.get_last_bar_dt(symbol, timeframe)
        for snapshot in snapshots:
            if last_bar_dt is not None and snapshot.bar_dt <= last_bar_dt:
                continue
            adapter.insert_snapshot(snapshot)
            inserted_rows += 1

    return IngestResult(
        symbol=symbol,
        timeframe=timeframe,
        inserted_rows=inserted_rows,
        lineage_hash=snapshots[0].lineage_hash if snapshots else calculate_lineage_hash([]),
    )


def ingest_symbol(
    symbol: str,
    *,
    timeframe: str = "day",
    tdx_root: Path,
    db_path: Path,
    malf_k: int = 2,
    data_stale: bool = True,
) -> IngestResult:
    """Read one authoritative TDX symbol and resume durable snapshot writes.

    week/month 输入来自 `aggregate.py` 的 day 聚合产物（trd.md §5.4.3 口径）；
    日/周/月各自独立跑 MALF（不混池 rank），快照以 timeframe 字段区分。
    """
    if timeframe not in _SUPPORTED_TIMEFRAMES:
        raise ValueError("timeframe must be one of 'day', 'week', 'month'")

    file_path = tdx_root / "vipdoc" / symbol[:2] / "lday" / f"{symbol}.day"
    daily = read_tdx_day(file_path)
    if timeframe == "week":
        bars = aggregate_to_week(daily)
    elif timeframe == "month":
        bars = aggregate_to_month(daily)
    else:
        bars = daily
    return ingest_bars(
        symbol,
        timeframe=timeframe,
        bars=bars,
        db_path=db_path,
        malf_k=malf_k,
        data_stale=data_stale,
    )
