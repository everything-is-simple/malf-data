"""Read authoritative TDX .day records without altering price precision."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import struct
from typing import Final

from malf.types import PriceBar


_RECORD: Final[struct.Struct] = struct.Struct("<5If2I")
_TIMEFRAME: Final[str] = "day"


class TDXDataError(ValueError):
    """Raised when a symbol's TDX input violates the approved binary contract."""


def read_tdx_day(file_path: Path) -> list[PriceBar]:
    """Parse every TDX day record or reject the complete symbol input.

    Prices remain in their original integer comparison domain.  The parsed
    amount, volume, and reserved fields are intentionally not used by T02.
    """
    raw = file_path.read_bytes()
    if len(raw) % _RECORD.size:
        raise TDXDataError(
            f"TDX input {file_path} has a non-32-byte trailing record"
        )

    bars: list[PriceBar] = []
    symbol = file_path.stem
    previous_bar_dt: str | None = None
    for offset in range(0, len(raw), _RECORD.size):
        date, open_price, high, low, close, _amount, _volume, _reserved = _RECORD.unpack_from(raw, offset)
        bar_dt = f"{date:08d}"
        _validate_record(file_path, offset // _RECORD.size, bar_dt, open_price, high, low, close)
        if previous_bar_dt is not None and bar_dt <= previous_bar_dt:
            raise TDXDataError(
                f"TDX input {file_path} has bar_dt values that are not strictly increasing"
                f" at record {offset // _RECORD.size}: {previous_bar_dt} -> {bar_dt}"
            )
        previous_bar_dt = bar_dt
        bars.append(
            PriceBar(
                symbol=symbol,
                timeframe=_TIMEFRAME,
                bar_dt=bar_dt,
                open=open_price,
                high=high,
                low=low,
                close=close,
            )
        )
    return bars


def _validate_record(
    file_path: Path,
    record_index: int,
    bar_dt: str,
    open_price: int,
    high: int,
    low: int,
    close: int,
) -> None:
    try:
        datetime.strptime(bar_dt, "%Y%m%d")
    except ValueError as error:
        raise TDXDataError(
            f"TDX input {file_path} has an invalid date in record {record_index}: {bar_dt}"
        ) from error

    if min(open_price, high, low, close) <= 0 or low > min(open_price, close) or high < max(open_price, close):
        raise TDXDataError(
            f"TDX input {file_path} has invalid OHLC values in record {record_index}"
        )
