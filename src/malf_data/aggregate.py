"""Deterministic day → week / month OHLC aggregation (B, raw_none ETF scope).

口径依据：`docs/spec/trd.md §5.4.3` 与 `docs/.record/issues/004-日周月线周期支持-三周期MALF快照.md`
- 周线：bar_dt = 该自然周（周一起）最后交易日；open = 周内首个交易日开盘；
  high/low = 周内极值；close = 周内最后交易日收盘；
- 月线：bar_dt = 该月最后交易日；open = 月初首个交易日开盘；
  high/low = 月内极值；close = 月末最后交易日收盘；
- 规则版本：`malf-week-from-day-v1` / `malf-month-from-day-v1`（常量定义处；实际嵌入快照由 ingest 层在计算 lineage_hash 前写入 `rule_versions`，见 `ingest.build_snapshots`）。
- 纯函数、确定性：相同输入 → 相同输出；输入必须按 bar_dt 严格递增且全部为 day timeframe。

本模块不连接 DuckDB、不读文件、不调用 MALF 引擎，自身不写快照；聚合在源整数价格域上完成，不做 /1000、round 或 binary float。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Iterable

from malf.types import PriceBar

WEEK_RULE_VERSION: str = "malf-week-from-day-v1"
MONTH_RULE_VERSION: str = "malf-month-from-day-v1"

_SUPPORTED_TARGETS: tuple[str, ...] = ("week", "month")


def _parse_bar_dt(bar_dt: str) -> date:
    return date(int(bar_dt[0:4]), int(bar_dt[4:6]), int(bar_dt[6:8]))


def _validate_input(daily: list[PriceBar]) -> None:
    for bar in daily:
        if bar.timeframe != "day":
            raise ValueError("aggregate input bars must have timeframe 'day'")
    symbols = {bar.symbol for bar in daily}
    if len(symbols) > 1:
        raise ValueError("aggregate input bars must have the same symbol")
    for prev, curr in zip(daily, daily[1:]):
        if not prev.bar_dt < curr.bar_dt:
            raise ValueError("aggregate input bar_dt must be strictly increasing")


def _group_and_reduce(
    daily: list[PriceBar],
    target: str,
    key_of: Callable[[date], date],
) -> list[PriceBar]:
    _validate_input(daily)
    groups: dict[date, list[PriceBar]] = {}
    for bar in daily:
        groups.setdefault(key_of(_parse_bar_dt(bar.bar_dt)), []).append(bar)

    result: list[PriceBar] = []
    for key in sorted(groups):
        bars = groups[key]
        first, last = bars[0], bars[-1]
        result.append(
            PriceBar(
                symbol=first.symbol,
                timeframe=target,
                bar_dt=last.bar_dt,
                open=first.open,
                high=max(bar.high for bar in bars),
                low=min(bar.low for bar in bars),
                close=last.close,
            )
        )
    return result


def aggregate_to_week(daily: Iterable[PriceBar]) -> list[PriceBar]:
    """Group consecutive day bars into natural calendar weeks (Monday start)."""
    bars = list(daily)
    return _group_and_reduce(bars, "week", lambda d: d - timedelta(days=d.weekday()))


def aggregate_to_month(daily: Iterable[PriceBar]) -> list[PriceBar]:
    """Group consecutive day bars into calendar months."""
    bars = list(daily)
    return _group_and_reduce(bars, "month", lambda d: d.replace(day=1))
