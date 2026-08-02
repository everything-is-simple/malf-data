from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import duckdb

from malf.types import WaveStructuralSnapshot


EXPECTED_SNAPSHOT_COLUMNS = [
    "symbol",
    "timeframe",
    "bar_dt",
    "bar_index",
    "system_state",
    "direction",
    "active_wave_id",
    "progress_extreme_price",
    "progress_extreme_bar_dt",
    "guard_price",
    "guard_bar_dt",
    "bar_count",
    "break_bar_dt",
    "break_price",
    "transition_boundary_high",
    "transition_boundary_low",
    "candidate_pivot_type",
    "candidate_pivot_price",
    "range_boundary_high_now",
    "range_boundary_low_now",
    "range_evolution_count",
    "range_candidate_replacement_count",
    "range_type",
    "wave_span_rank",
    "wave_range_rank",
    "wave_stagnation_rank",
    "range_span_rank",
    "range_evolution_rank",
    "range_replacement_rank",
    "range_resolution_distance_rank",
    "p2_same_dir_span_momentum",
    "p2_same_dir_range_momentum",
    "p2_same_dir_label",
    "p3_cross_dir_span_momentum",
    "p3_cross_dir_range_momentum",
    "p3_cross_dir_label",
    "p4_cross_span_momentum",
    "p4_cross_range_momentum",
    "p4_cross_alive_warning",
    "rule_versions",
    "lineage_hash",
    "reason_codes",
    "usage",
    "freshness",
]


def _complete_snapshot() -> WaveStructuralSnapshot:
    return WaveStructuralSnapshot(
        symbol="sh510050",
        timeframe="day",
        bar_dt="20260629",
        bar_index=42,
        system_state="up_alive",
        direction="up",
        active_wave_id="sh510050_day_W1",
        progress_extreme_price=1234,
        progress_extreme_bar_dt="20260629",
        guard_price=1200,
        guard_bar_dt="20260620",
        bar_count=12,
        break_bar_dt=None,
        break_price=None,
        transition_boundary_high=None,
        transition_boundary_low=None,
        candidate_pivot_type=None,
        candidate_pivot_price=None,
        range_boundary_high_now=None,
        range_boundary_low_now=None,
        range_evolution_count=None,
        range_candidate_replacement_count=None,
        range_type=None,
        wave_span_rank=0.2,
        wave_range_rank=0.3,
        wave_stagnation_rank=0.4,
        range_span_rank=None,
        range_evolution_rank=None,
        range_replacement_rank=None,
        range_resolution_distance_rank=None,
        p2_same_dir_span_momentum=0.5,
        p2_same_dir_range_momentum=0.6,
        p2_same_dir_label="rising",
        p3_cross_dir_span_momentum=None,
        p3_cross_dir_range_momentum=None,
        p3_cross_dir_label=None,
        p4_cross_span_momentum=None,
        p4_cross_range_momentum=None,
        p4_cross_alive_warning=False,
        rule_versions={"adapter": "malf-v2.0-etf-tick-v0.1"},
        lineage_hash="a" * 64,
        reason_codes=["data_stale"],
        usage="research_only",
        freshness="stale_research_only",
    )


def test_duckdb_adapter_creates_exact_44_column_snapshot_contract(tmp_path: Path) -> None:
    """DuckDB schema preserves every approved MALF v2.1 Service snapshot field."""
    from malf_data.adapters.duckdb_adapter import DuckDBAdapter

    db_path = tmp_path / "riskbench.duckdb"
    adapter = DuckDBAdapter(db_path)
    try:
        columns = [
            row[1]
            for row in adapter.connection.execute("PRAGMA table_info('snapshots')").fetchall()
        ]
    finally:
        adapter.close()

    assert len(fields(WaveStructuralSnapshot)) == 44
    assert columns == EXPECTED_SNAPSHOT_COLUMNS


def test_duckdb_adapter_commits_and_reads_a_complete_snapshot(tmp_path: Path) -> None:
    """One snapshot INSERT is durable immediately and retains service metadata."""
    from malf_data.adapters.duckdb_adapter import DuckDBAdapter

    db_path = tmp_path / "riskbench.duckdb"
    snapshot = _complete_snapshot()
    adapter = DuckDBAdapter(db_path)
    adapter.insert_snapshot(snapshot)
    adapter.close()

    with duckdb.connect(str(db_path), read_only=True) as connection:
        row = connection.execute(
            """
            SELECT usage, freshness, p2_same_dir_span_momentum,
                   p2_same_dir_label, lineage_hash, rule_versions, reason_codes
            FROM snapshots
            WHERE symbol = ? AND timeframe = ? AND bar_dt = ?
            """,
            [snapshot.symbol, snapshot.timeframe, snapshot.bar_dt],
        ).fetchone()

    assert row == (
        "research_only",
        "stale_research_only",
        0.5,
        "rising",
        "a" * 64,
        '{"adapter":"malf-v2.0-etf-tick-v0.1"}',
        '["data_stale"]',
    )


def test_duckdb_adapter_returns_last_committed_bar_for_resume(tmp_path: Path) -> None:
    """Resume queries the latest committed bar for one symbol and timeframe only."""
    from malf_data.adapters.duckdb_adapter import DuckDBAdapter

    adapter = DuckDBAdapter(tmp_path / "riskbench.duckdb")
    try:
        adapter.insert_snapshot(_complete_snapshot())
        assert adapter.get_last_bar_dt("sh510050", "day") == "20260629"
        assert adapter.get_last_bar_dt("sz159915", "day") is None
    finally:
        adapter.close()
