from __future__ import annotations

from pathlib import Path

import pytest


AUTHORITATIVE_TDX_FILE = Path(r"Z:\new_tdx64\vipdoc\sh\lday\sh510050.day")


def test_tdx_reader_parses_authoritative_sh510050() -> None:
    """TDX reader parses complete 32-byte <5If2I records as integer PriceBars."""
    from malf_data.adapters.tdx_reader import read_tdx_day

    bars = read_tdx_day(AUTHORITATIVE_TDX_FILE)

    assert len(bars) > 1_000
    first = bars[0]
    assert first.symbol == "sh510050"
    assert first.timeframe == "day"
    assert first.bar_dt.isdigit() and len(first.bar_dt) == 8
    assert all(isinstance(value, int) for value in (first.open, first.high, first.low, first.close))
    assert first.low <= min(first.open, first.close) <= max(first.open, first.close) <= first.high


def test_tdx_reader_rejects_truncated_record_for_entire_symbol(tmp_path: Path) -> None:
    """A structurally invalid TDX file is rejected rather than silently skipping its bad bar."""
    from malf_data.adapters.tdx_reader import TDXDataError, read_tdx_day

    malformed = tmp_path / "sh999999.day"
    malformed.write_bytes(b"x" * 31)

    with pytest.raises(TDXDataError, match="32-byte"):
        read_tdx_day(malformed)
