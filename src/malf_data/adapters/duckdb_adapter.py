"""Persist complete MALF v2.1 Service snapshots in a local DuckDB file."""

from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
from typing import Final

import duckdb

from malf.types import WaveStructuralSnapshot


SNAPSHOT_COLUMNS: Final[tuple[str, ...]] = tuple(
    field.name for field in fields(WaveStructuralSnapshot)
)


_CREATE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS snapshots (
    "symbol" VARCHAR NOT NULL,
    "timeframe" VARCHAR NOT NULL,
    "bar_dt" VARCHAR NOT NULL,
    "bar_index" BIGINT NOT NULL,
    "system_state" VARCHAR NOT NULL,
    "direction" VARCHAR,
    "active_wave_id" VARCHAR,
    "progress_extreme_price" BIGINT,
    "progress_extreme_bar_dt" VARCHAR,
    "guard_price" BIGINT,
    "guard_bar_dt" VARCHAR,
    "bar_count" BIGINT,
    "break_bar_dt" VARCHAR,
    "break_price" BIGINT,
    "transition_boundary_high" BIGINT,
    "transition_boundary_low" BIGINT,
    "candidate_pivot_type" VARCHAR,
    "candidate_pivot_price" BIGINT,
    "range_boundary_high_now" BIGINT,
    "range_boundary_low_now" BIGINT,
    "range_evolution_count" BIGINT,
    "range_candidate_replacement_count" BIGINT,
    "range_type" VARCHAR,
    "wave_span_rank" DOUBLE,
    "wave_range_rank" DOUBLE,
    "wave_stagnation_rank" DOUBLE,
    "range_span_rank" DOUBLE,
    "range_evolution_rank" DOUBLE,
    "range_replacement_rank" DOUBLE,
    "range_resolution_distance_rank" DOUBLE,
    "p2_same_dir_span_momentum" DOUBLE,
    "p2_same_dir_range_momentum" DOUBLE,
    "p2_same_dir_label" VARCHAR,
    "p3_cross_dir_span_momentum" DOUBLE,
    "p3_cross_dir_range_momentum" DOUBLE,
    "p3_cross_dir_label" VARCHAR,
    "p4_cross_span_momentum" DOUBLE,
    "p4_cross_range_momentum" DOUBLE,
    "p4_cross_alive_warning" BOOLEAN NOT NULL,
    "rule_versions" JSON NOT NULL,
    "lineage_hash" VARCHAR,
    "reason_codes" JSON NOT NULL,
    "usage" VARCHAR NOT NULL,
    "freshness" VARCHAR NOT NULL,
    PRIMARY KEY ("symbol", "timeframe", "bar_dt")
)
"""

_COLUMN_LIST: Final[str] = ", ".join(f'"{name}"' for name in SNAPSHOT_COLUMNS)
_PLACEHOLDERS: Final[str] = ", ".join("?" for _ in SNAPSHOT_COLUMNS)
_INSERT_SQL: Final[str] = f"INSERT INTO snapshots ({_COLUMN_LIST}) VALUES ({_PLACEHOLDERS})"


class DuckDBAdapter:
    """Own one local DuckDB connection and commit each durable snapshot row."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(db_path))
        self.connection.execute(_CREATE_TABLE_SQL)
        self.connection.commit()

    def close(self) -> None:
        """Close the owned DuckDB connection."""
        self.connection.close()

    def insert_snapshot(self, snapshot: WaveStructuralSnapshot) -> None:
        """Insert one complete Service snapshot and commit it before returning."""
        values = [
            _serialize_value(name, getattr(snapshot, name))
            for name in SNAPSHOT_COLUMNS
        ]
        self.connection.execute(_INSERT_SQL, values)
        self.connection.commit()

    def get_last_bar_dt(self, symbol: str, timeframe: str) -> str | None:
        """Return the latest committed bar date for an exact resume partition."""
        row = self.connection.execute(
            """
            SELECT MAX("bar_dt")
            FROM snapshots
            WHERE "symbol" = ? AND "timeframe" = ?
            """,
            [symbol, timeframe],
        ).fetchone()
        return row[0] if row is not None else None


def _serialize_value(name: str, value: object) -> object:
    if name in {"rule_versions", "reason_codes"}:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return value


