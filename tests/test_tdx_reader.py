from __future__ import annotations

import os
from pathlib import Path
import struct

import pytest


_RECORD = struct.Struct("<5If2I")


def _packed_record(date: int, close: int = 1000) -> bytes:
    return _RECORD.pack(date, close - 2, close + 3, close - 4, close, 1.0, 100, 0)


# 支持 Linux 和 Windows 路径
if os.name == 'posix':
    AUTHORITATIVE_TDX_FILE = Path("/sessions/ecstatic-amazing-hamilton/mnt/new_tdx64/vipdoc/sh/lday/sh510050.day")
else:
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



def test_tdx_reader_rejects_duplicate_bar_dt_for_entire_symbol(tmp_path: Path) -> None:
    """Duplicate dates are structural input defects and must reject the symbol."""
    from malf_data.adapters.tdx_reader import TDXDataError, read_tdx_day

    duplicated = tmp_path / "sh999999.day"
    duplicated.write_bytes(_packed_record(20260102) + _packed_record(20260102))

    with pytest.raises(TDXDataError, match="strictly increasing"):
        read_tdx_day(duplicated)


def test_tdx_reader_rejects_descending_bar_dt_for_entire_symbol(tmp_path: Path) -> None:
    """Out-of-order dates are not sorted or skipped by the strict reader."""
    from malf_data.adapters.tdx_reader import TDXDataError, read_tdx_day

    descending = tmp_path / "sh999999.day"
    descending.write_bytes(_packed_record(20260103) + _packed_record(20260102))

    with pytest.raises(TDXDataError, match="strictly increasing"):
        read_tdx_day(descending)
