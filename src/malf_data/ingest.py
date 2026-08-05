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
from malf_data.aggregate import (
    MONTH_RULE_VERSION,
    WEEK_RULE_VERSION,
    aggregate_to_month,
    aggregate_to_week,
)
from malf_data.driver import ADAPTER_VERSION, MALFDriver


_SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("day", "week", "month")
_AGGREGATION_RULE_VERSION_BY_TIMEFRAME: dict[str, str] = {
    "week": WEEK_RULE_VERSION,
    "month": MONTH_RULE_VERSION,
}


@dataclass(frozen=True)
class IngestResult:
    """Summary of one deterministic ingest run."""

    symbol: str
    timeframe: str
    inserted_rows: int
    lineage_hash: str


def apply_as_of_cutoff(bars: Iterable[PriceBar], as_of_date: str | None) -> list[PriceBar]:
    """D3（2026-08-05）：按完整交易日 as_of_date 截断 bar 序列。

    - day 输入进入引擎前调用；week/month 聚合必须基于截断后的 day 序列；
    - as_of_date=None 时原样返回（兼容非 D3 调用）；
    - 返回序列保持原时间顺序。
    """
    if as_of_date is None:
        return list(bars)
    return [b for b in bars if b.bar_dt <= as_of_date]


def build_snapshots(
    bars: Iterable[PriceBar],
    *,
    malf_k: int = 2,
    data_stale: bool = True,
) -> list[WaveStructuralSnapshot]:
    """Run one homogeneous MALF series and attach deterministic audit metadata."""
    prepared = list(bars)
    _validate_homogeneous_series(prepared)

    driver = MALFDriver(malf_k=malf_k, data_stale=data_stale)
    unhashed = [driver.on_bar(bar) for bar in prepared]
    aggregation_rule_version = (
        _AGGREGATION_RULE_VERSION_BY_TIMEFRAME.get(prepared[0].timeframe)
        if prepared
        else None
    )
    if aggregation_rule_version is not None:
        unhashed = [
            replace(
                snapshot,
                rule_versions={
                    **snapshot.rule_versions,
                    "bar_aggregation": aggregation_rule_version,
                },
            )
            for snapshot in unhashed
        ]

    lineage_hash = calculate_lineage_hash(unhashed)
    return [replace(snapshot, lineage_hash=lineage_hash) for snapshot in unhashed]


def _validate_homogeneous_series(bars: list[PriceBar]) -> None:
    """Reject mixed identity/timeframe input before it reaches a stateful driver."""
    if not bars:
        return
    symbols = {bar.symbol for bar in bars}
    if len(symbols) != 1:
        raise ValueError("snapshot input bars must have the same symbol")
    timeframes = {bar.timeframe for bar in bars}
    if len(timeframes) != 1:
        raise ValueError("snapshot input bars must have the same timeframe")
    for previous, current in zip(bars, bars[1:]):
        if not previous.bar_dt < current.bar_dt:
            raise ValueError("snapshot input bar_dt must be strictly increasing")


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
    run preserves stateful engine semantics.

    D1 修复（2026-08-05，生产写入复核发现）：以"最新行 lineage == 当前系列 lineage"
    判定系列是否变化——无变化 → 幂等跳过；有变化 →
      - day：追加新 bar（bar_dt > 已提交最大 bar_dt）+ 刷新分区内全部既有行 lineage；
      - week/month：聚合派生在扩展后会重导旧的部分桶（旧桶 bar_dt 与新桶不同，
        无法按 bar_dt 去重）→ 全量重建（删除分区 + 全量插入），收敛到全新构建。
    不变量：增量后每 (symbol, timeframe) 行集合 == 全新构建，且单一 lineage。
    """
    if timeframe not in _SUPPORTED_TIMEFRAMES:
        raise ValueError("timeframe must be one of 'day', 'week', 'month'")

    prepared = list(bars)
    _validate_homogeneous_series(prepared)
    for bar in prepared:
        if bar.symbol != symbol:
            raise ValueError("bar symbol must match ingest symbol")
        if bar.timeframe != timeframe:
            raise ValueError("bar timeframe must match ingest timeframe")

    snapshots = build_snapshots(prepared, malf_k=malf_k, data_stale=data_stale)
    new_hash = snapshots[0].lineage_hash if snapshots else calculate_lineage_hash([])

    inserted_rows = 0
    with DuckDBAdapter(db_path) as adapter:
        last_lineage = adapter.get_last_lineage(symbol, timeframe)
        if last_lineage is not None and last_lineage == new_hash:
            # 系列未变化：幂等跳过（inserted_rows == 0）
            pass
        elif timeframe == "day":
            last_bar_dt = adapter.get_last_bar_dt(symbol, timeframe)
            for snapshot in snapshots:
                if last_bar_dt is not None and snapshot.bar_dt <= last_bar_dt:
                    continue
                adapter.insert_snapshot(snapshot)
                inserted_rows += 1
            adapter.refresh_lineage(symbol, timeframe, new_hash)
        else:
            # week/month：聚合派生，删除分区后全量插入（收敛到全新构建）
            adapter.delete_symbol_timeframe(symbol, timeframe)
            for snapshot in snapshots:
                adapter.insert_snapshot(snapshot)
                inserted_rows += 1

    return IngestResult(
        symbol=symbol,
        timeframe=timeframe,
        inserted_rows=inserted_rows,
        lineage_hash=new_hash,
    )


def ingest_symbol(
    symbol: str,
    *,
    timeframe: str = "day",
    tdx_root: Path,
    db_path: Path,
    malf_k: int = 2,
    data_stale: bool = True,
    as_of_date: str | None = None,
) -> IngestResult:
    """Read one authoritative TDX symbol and resume durable snapshot writes.

    week/month 输入来自 `aggregate.py` 的 day 聚合产物（trd.md §5.4.3 口径）；
    日/周/月各自独立跑 MALF（不混池 rank），快照以 timeframe 字段区分。

    D3（2026-08-05）：as_of_date 为完整交易日截止——在 day 输入进入引擎/聚合前
    截断，week/month 从截断后的 day 序列重聚合；生产模式要求显式传入，不依赖机器系统日期。
    """
    if timeframe not in _SUPPORTED_TIMEFRAMES:
        raise ValueError("timeframe must be one of 'day', 'week', 'month'")

    file_path = tdx_root / "vipdoc" / symbol[:2] / "lday" / f"{symbol}.day"
    daily = read_tdx_day(file_path)
    daily = apply_as_of_cutoff(daily, as_of_date)  # D3：完整交易日截断
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
