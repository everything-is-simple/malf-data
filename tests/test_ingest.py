from __future__ import annotations

from dataclasses import fields
import os
from pathlib import Path
import struct

from malf.types import PriceBar, WaveStructuralSnapshot

from malf_data.adapters.duckdb_adapter import DuckDBAdapter
from malf_data.adapters.tdx_reader import read_tdx_day


_RECORD = struct.Struct("<5If2I")

# 跨平台路径支持
if os.name == 'posix':
    _TDX_ROOT = Path("/sessions/ecstatic-amazing-hamilton/mnt/new_tdx64")
else:
    _TDX_ROOT = Path(r"Z:\new_tdx64")


def _write_day_file(root: Path, symbol: str, count: int = 6) -> Path:
    path = root / "vipdoc" / symbol[:2] / "lday" / f"{symbol}.day"
    path.parent.mkdir(parents=True)
    records = []
    for index in range(count):
        close = 1000 + index
        records.append(
            _RECORD.pack(
                20260101 + index,
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


def test_driver_composes_real_core_and_service_into_44_field_snapshots() -> None:
    """The driver uses MALFCoreEngine.on_bar and Service's 44-field factory."""
    from malf_data.ingest import build_snapshots

    bars = read_tdx_day(_TDX_ROOT / "vipdoc" / "sh" / "lday" / "sh510050.day")[:8]
    snapshots = build_snapshots(bars, data_stale=True)

    assert len(snapshots) == len(bars)
    assert all(len(fields(WaveStructuralSnapshot)) == 44 for _ in snapshots)
    assert snapshots[0].symbol == "sh510050"
    assert snapshots[0].bar_index == 0
    assert snapshots[0].usage == "research_only"
    assert snapshots[0].freshness == "stale_research_only"
    assert snapshots[0].rule_versions["adapter"] == "malf-v2.0-etf-tick-v0.1"
    # T9.11 对齐权威 Service §5：键名 price_domain（原 price_policy）
    assert snapshots[0].rule_versions["price_domain"] == "source_integer_fixed_point"
    assert all(snapshot.lineage_hash == snapshots[0].lineage_hash for snapshot in snapshots)


def test_driver_lineage_hash_is_stable_for_the_same_bars() -> None:
    """Two fresh engine runs produce the same snapshot sequence and lineage hash."""
    from malf_data.ingest import build_snapshots, calculate_lineage_hash

    bars = read_tdx_day(_TDX_ROOT / "vipdoc" / "sh" / "lday" / "sh510050.day")[:20]
    first = build_snapshots(bars, data_stale=True)
    second = build_snapshots(bars, data_stale=True)

    assert calculate_lineage_hash(first) == calculate_lineage_hash(second)
    assert first == second


def test_ingest_symbol_commits_rows_and_resumes_after_a_partial_run(tmp_path: Path) -> None:
    """Ingest reconstructs engine state, skips committed bars, and writes the suffix."""
    from malf_data.ingest import build_snapshots, ingest_symbol

    symbol = "sh999999"
    tdx_root = tmp_path / "tdx"
    db_path = tmp_path / "riskbench.duckdb"
    _write_day_file(tdx_root, symbol)
    bars = read_tdx_day(tdx_root / "vipdoc" / "sh" / "lday" / f"{symbol}.day")

    partial_adapter = DuckDBAdapter(db_path)
    try:
        for snapshot in build_snapshots(bars[:3], data_stale=True):
            partial_adapter.insert_snapshot(snapshot)
    finally:
        partial_adapter.close()

    result = ingest_symbol(
        symbol,
        tdx_root=tdx_root,
        db_path=db_path,
        data_stale=True,
    )

    assert result.inserted_rows == 3
    with DuckDBAdapter(db_path) as adapter:
        count = adapter.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        usage, freshness = adapter.connection.execute(
            "SELECT usage, freshness FROM snapshots ORDER BY bar_dt LIMIT 1"
        ).fetchone()
    assert count == 6
    assert (usage, freshness) == ("research_only", "stale_research_only")
