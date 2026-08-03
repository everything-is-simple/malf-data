"""B: 三周期（day/week/month）独立 ingest 与 DuckDB 三分区共存集成测试。"""

from __future__ import annotations

from pathlib import Path
import json
import struct

import pytest

from malf.types import PriceBar

from malf_data.adapters.duckdb_adapter import DuckDBAdapter
from malf_data.adapters.tdx_reader import read_tdx_day
from malf_data.aggregate import (
    MONTH_RULE_VERSION,
    WEEK_RULE_VERSION,
    aggregate_to_week,
)
from malf_data.ingest import build_snapshots, ingest_bars, ingest_symbol


_RECORD = struct.Struct("<5If2I")

# 21 个连续交易日（2025-06-02 周一 → 2025-06-22），跨 3 个自然周、1 个自然月。
_DAY_DATES = [
    "20250602", "20250603", "20250604", "20250605", "20250606", "20250607", "20250608",
    "20250609", "20250610", "20250611", "20250612", "20250613", "20250614", "20250615",
    "20250616", "20250617", "20250618", "20250619", "20250620", "20250621", "20250622",
]


def _write_day_file(root: Path, symbol: str) -> Path:
    path = root / "vipdoc" / symbol[:2] / "lday" / f"{symbol}.day"
    path.parent.mkdir(parents=True)
    records = []
    for index, bar_dt in enumerate(_DAY_DATES):
        close = 1000 + index
        records.append(
            _RECORD.pack(
                int(bar_dt),
                close - 2,
                close + 3,
                close - 4,
                close,
                1.0,
                100 + index,
                0,
            )
        )
    path.write_bytes(b"".join(records))
    return path


def test_ingest_three_timeframes_coexist_without_pk_collision(tmp_path: Path) -> None:
    symbol = "sh999998"
    tdx_root = tmp_path / "tdx"
    db_path = tmp_path / "riskbench.duckdb"
    _write_day_file(tdx_root, symbol)

    r_day = ingest_symbol(symbol, tdx_root=tdx_root, db_path=db_path)
    r_week = ingest_symbol(symbol, timeframe="week", tdx_root=tdx_root, db_path=db_path)
    r_month = ingest_symbol(symbol, timeframe="month", tdx_root=tdx_root, db_path=db_path)

    assert r_day.inserted_rows == 21
    assert r_week.inserted_rows == 3
    assert r_month.inserted_rows == 1

    with DuckDBAdapter(db_path) as adapter:
        distribution = adapter.connection.execute(
            "SELECT timeframe, COUNT(*) FROM snapshots GROUP BY timeframe ORDER BY timeframe"
        ).fetchall()
        total = adapter.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        week_dts = [
            row[0]
            for row in adapter.connection.execute(
                "SELECT bar_dt FROM snapshots WHERE timeframe='week' ORDER BY bar_dt"
            ).fetchall()
        ]
        month_dts = [
            row[0]
            for row in adapter.connection.execute(
                "SELECT bar_dt FROM snapshots WHERE timeframe='month' ORDER BY bar_dt"
            ).fetchall()
        ]
        usage, freshness = adapter.connection.execute(
            "SELECT usage, freshness FROM snapshots WHERE timeframe='week' LIMIT 1"
        ).fetchone()

    assert distribution == [("day", 21), ("month", 1), ("week", 3)]
    assert total == 25
    assert week_dts == ["20250608", "20250615", "20250622"]
    assert month_dts == ["20250622"]
    assert (usage, freshness) == ("research_only", "stale_research_only")


def test_ingest_week_is_idempotent_on_second_run(tmp_path: Path) -> None:
    symbol = "sh999997"
    tdx_root = tmp_path / "tdx"
    db_path = tmp_path / "riskbench.duckdb"
    _write_day_file(tdx_root, symbol)

    first = ingest_symbol(symbol, timeframe="week", tdx_root=tdx_root, db_path=db_path)
    second = ingest_symbol(symbol, timeframe="week", tdx_root=tdx_root, db_path=db_path)

    assert first.inserted_rows == 3
    assert second.inserted_rows == 0
    with DuckDBAdapter(db_path) as adapter:
        count = adapter.connection.execute(
            "SELECT COUNT(*) FROM snapshots WHERE timeframe='week'"
        ).fetchone()[0]
    assert count == 3


def test_week_snapshots_and_lineage_hash_are_deterministic(tmp_path: Path) -> None:
    symbol = "sh999996"
    tdx_root = tmp_path / "tdx"
    _write_day_file(tdx_root, symbol)
    daily = read_tdx_day(tdx_root / "vipdoc" / "sh" / "lday" / f"{symbol}.day")
    weekly = aggregate_to_week(daily)

    first = build_snapshots(weekly, data_stale=True)
    second = build_snapshots(weekly, data_stale=True)

    assert first == second
    assert first[0].lineage_hash == second[0].lineage_hash
    assert all(snapshot.timeframe == "week" for snapshot in first)
    assert all(
        snapshot.rule_versions["bar_aggregation"] == WEEK_RULE_VERSION
        for snapshot in first
    )


def test_persisted_aggregated_snapshots_record_aggregation_rule_version(tmp_path: Path) -> None:
    symbol = "sh999994"
    tdx_root = tmp_path / "tdx"
    db_path = tmp_path / "riskbench.duckdb"
    _write_day_file(tdx_root, symbol)

    ingest_symbol(symbol, timeframe="week", tdx_root=tdx_root, db_path=db_path)
    ingest_symbol(symbol, timeframe="month", tdx_root=tdx_root, db_path=db_path)

    with DuckDBAdapter(db_path) as adapter:
        rows = adapter.connection.execute(
            "SELECT timeframe, rule_versions FROM snapshots "
            "WHERE timeframe IN ('week', 'month') GROUP BY timeframe, rule_versions "
            "ORDER BY timeframe"
        ).fetchall()

    versions = {timeframe: json.loads(str(rule_versions)) for timeframe, rule_versions in rows}
    assert versions["week"]["bar_aggregation"] == WEEK_RULE_VERSION
    assert versions["month"]["bar_aggregation"] == MONTH_RULE_VERSION


def test_ingest_bars_rejects_timeframe_mismatch(tmp_path: Path) -> None:
    symbol = "sh999993"
    db_path = tmp_path / "riskbench.duckdb"
    day_bar = PriceBar(symbol, "day", "20250602", 1000, 1003, 996, 1000)

    with pytest.raises(ValueError, match="bar timeframe"):
        ingest_bars(symbol, timeframe="week", bars=[day_bar], db_path=db_path)


def test_ingest_bars_rejects_symbol_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "riskbench.duckdb"
    day_bar = PriceBar("sz999993", "day", "20250602", 1000, 1003, 996, 1000)

    with pytest.raises(ValueError, match="bar symbol"):
        ingest_bars("sh999993", timeframe="day", bars=[day_bar], db_path=db_path)


def test_ingest_rejects_unsupported_timeframe(tmp_path: Path) -> None:
    symbol = "sh999995"
    tdx_root = tmp_path / "tdx"
    db_path = tmp_path / "riskbench.duckdb"
    _write_day_file(tdx_root, symbol)

    with pytest.raises(ValueError, match="day.*week.*month"):
        ingest_symbol(symbol, timeframe="hour", tdx_root=tdx_root, db_path=db_path)
