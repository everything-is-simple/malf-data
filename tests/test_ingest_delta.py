"""D1 RED/GREEN — 增量 ingest 一致性（2026-08-05 生产写入复核发现）。

缺陷：`ingest_bars` 按 `bar_dt <= last_bar_dt` 追加续传——
  1) 周/月聚合在日线扩展后**旧的部分桶残留**（如部分周 bar_dt=0112 与扩展后的完整周 bar_dt=0116
     并存，去重逻辑无法识别同一自然周），快照数 > 全新构建；
  2) 旧行 `lineage_hash` 不刷新 → 每组合 `COUNT(DISTINCT lineage_hash) > 1`。

修复目标（不变量）：增量后每 (symbol, timeframe) 的行集合与全新构建**完全一致**，且**单一 lineage**；
无变化重跑幂等（inserted_rows == 0）。
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from malf.types import PriceBar

from malf_data.aggregate import aggregate_to_month, aggregate_to_week
from malf_data.ingest import ingest_bars

# 2026-01-05 为周一；W2=0105-0109，W3=0112-0116
_WEEK2 = ["20260105", "20260106", "20260107", "20260108", "20260109"]
_WEEK3_PARTIAL = ["20260112"]
_WEEK3_FULL = ["20260113", "20260114", "20260115", "20260116"]
_JAN_PARTIAL = ["20260105", "20260106", "20260107", "20260108"]
_JAN_FULL = [
    "20260112", "20260113", "20260114", "20260115", "20260116",
    "20260119", "20260120", "20260121", "20260122", "20260123",
    "20260126", "20260127", "20260128", "20260129", "20260130",
]


def _day_bars(symbol: str, dates: list[str]) -> list[PriceBar]:
    bars = []
    for i, dt in enumerate(dates):
        base = 1000 + i * 10
        bars.append(PriceBar(symbol, "day", dt, base, base + 5, base - 5, base))
    return bars


def _lineage_and_rows(db: Path):
    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            "SELECT bar_dt, lineage_hash FROM snapshots ORDER BY bar_dt"
        ).fetchall()
        distinct = con.execute(
            "SELECT COUNT(DISTINCT lineage_hash) FROM snapshots"
        ).fetchone()[0]
        count = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    return count, distinct, rows


# ============ day：增量追加 + lineage 刷新 ============


def test_delta_day_appends_and_single_lineage(tmp_path: Path) -> None:
    symbol = "synthetic01"
    db = tmp_path / "r.duckdb"
    d1 = _day_bars(symbol, _WEEK2)
    r1 = ingest_bars(symbol, timeframe="day", bars=d1, db_path=db, data_stale=True)
    assert r1.inserted_rows == 5

    d2 = d1 + _day_bars(symbol, _WEEK3_PARTIAL)
    r2 = ingest_bars(symbol, timeframe="day", bars=d2, db_path=db, data_stale=True)
    assert r2.inserted_rows == 1

    fresh_db = tmp_path / "fresh.duckdb"
    fresh = ingest_bars(symbol, timeframe="day", bars=d2, db_path=fresh_db, data_stale=True)

    count, distinct, _ = _lineage_and_rows(db)
    assert count == 6
    assert distinct == 1  # RED：现实现为 2（旧批次 lineage 未刷新）
    _, fresh_distinct, _ = _lineage_and_rows(fresh_db)
    assert fresh_distinct == 1
    assert r2.lineage_hash == fresh.lineage_hash


# ============ week：扩展后旧部分桶必须被替换 ============


def test_delta_week_replaces_stale_partial_bucket(tmp_path: Path) -> None:
    symbol = "synthetic02"
    db = tmp_path / "r.duckdb"
    d1 = _day_bars(symbol, _WEEK2 + _WEEK3_PARTIAL)
    ingest_bars(symbol, timeframe="week", bars=aggregate_to_week(d1), db_path=db, data_stale=True)

    d2 = d1 + _day_bars(symbol, _WEEK3_FULL)  # 同一自然周 W3 被补全
    r2 = ingest_bars(symbol, timeframe="week", bars=aggregate_to_week(d2), db_path=db, data_stale=True)
    expected = aggregate_to_week(d2)

    count, distinct, rows = _lineage_and_rows(db)
    assert count == len(expected) == 2  # RED：现实现为 3（旧 0112 残留 + 新 0116）
    assert [r[0] for r in rows] == [b.bar_dt for b in expected]
    assert distinct == 1
    assert r2.inserted_rows == len(expected)


# ============ month：扩展后旧部分桶必须被替换 ============


def test_delta_month_replaces_stale_partial_bucket(tmp_path: Path) -> None:
    symbol = "synthetic03"
    db = tmp_path / "r.duckdb"
    d1 = _day_bars(symbol, ["20251229", "20251230", "20251231"] + _JAN_PARTIAL)
    ingest_bars(symbol, timeframe="month", bars=aggregate_to_month(d1), db_path=db, data_stale=True)

    d2 = d1 + _day_bars(symbol, _JAN_FULL)
    ingest_bars(symbol, timeframe="month", bars=aggregate_to_month(d2), db_path=db, data_stale=True)
    expected = aggregate_to_month(d2)

    count, distinct, rows = _lineage_and_rows(db)
    assert count == len(expected) == 2  # RED：现实现为 3（旧 0108 残留 + 新 0130）
    assert [r[0] for r in rows] == [b.bar_dt for b in expected]
    assert distinct == 1


# ============ 幂等：无变化重跑 inserted == 0 ============


def test_delta_no_change_is_idempotent(tmp_path: Path) -> None:
    symbol = "synthetic04"
    db = tmp_path / "r.duckdb"
    d = _day_bars(symbol, _WEEK2 + _WEEK3_PARTIAL)
    ingest_bars(symbol, timeframe="day", bars=d, db_path=db, data_stale=True)
    r2 = ingest_bars(symbol, timeframe="day", bars=d, db_path=db, data_stale=True)
    assert r2.inserted_rows == 0
    count, distinct, _ = _lineage_and_rows(db)
    assert count == 6
    assert distinct == 1

    w = aggregate_to_week(d)
    ingest_bars(symbol, timeframe="week", bars=w, db_path=db, data_stale=True)
    r3 = ingest_bars(symbol, timeframe="week", bars=w, db_path=db, data_stale=True)
    assert r3.inserted_rows == 0
