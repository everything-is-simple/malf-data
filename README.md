# malf-data

T02 upstream data pipeline component for AI MALF RiskBench.

It reads authoritative local TDX day data, produces deterministic MALF snapshots, and stores derived snapshots in DuckDB. It does not modify the TDX input source or malf-engine.
