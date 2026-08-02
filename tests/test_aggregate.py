"""B: day → week/month OHLC 聚合的 golden fixture 测试（RED 先行，人工推导预期）。"""

from __future__ import annotations

import pytest

from malf.types import PriceBar

from malf_data.aggregate import (
    aggregate_to_month,
    aggregate_to_week,
)


def _bar(
    bar_dt: str,
    open_: int,
    high: int,
    low: int,
    close: int,
    symbol: str = "TST",
    timeframe: str = "day",
) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        timeframe=timeframe,
        bar_dt=bar_dt,
        open=open_,
        high=high,
        low=low,
        close=close,
    )


def _week(symbol: str, bar_dt: str, open_: int, high: int, low: int, close: int) -> PriceBar:
    return PriceBar(symbol, "week", bar_dt, open_, high, low, close)


def _month(symbol: str, bar_dt: str, open_: int, high: int, low: int, close: int) -> PriceBar:
    return PriceBar(symbol, "month", bar_dt, open_, high, low, close)


class TestAggregateToWeek:
    def test_single_full_monday_to_friday_week(self) -> None:
        """一个完整自然周（周一 2025-08-18 → 周五 2025-08-22）聚合为一条周线。"""
        days = [
            _bar("20250818", 100, 105, 98, 102),
            _bar("20250819", 103, 106, 99, 104),
            _bar("20250820", 105, 108, 101, 107),
            _bar("20250821", 106, 110, 102, 109),
            _bar("20250822", 108, 112, 104, 111),
        ]
        assert aggregate_to_week(days) == [
            _week("TST", "20250822", 100, 112, 98, 111),
        ]

    def test_groups_partial_week_and_multiple_weeks(self) -> None:
        """跨周分组：周五 08-15 属上一自然周（周一 08-11），08-18 起另起一周。"""
        days = [
            _bar("20250815", 90, 95, 88, 92),       # 周五，属周一 08-11 周
            _bar("20250818", 100, 105, 98, 102),
            _bar("20250819", 103, 106, 99, 104),
            _bar("20250820", 105, 108, 101, 107),
            _bar("20250821", 106, 110, 102, 109),
            _bar("20250822", 108, 112, 104, 111),   # 周一 08-18 周
            _bar("20250825", 110, 115, 106, 112),
            _bar("20250826", 112, 118, 108, 115),
            _bar("20250829", 114, 120, 110, 117),   # 周一 08-25 周（缺 08-27/28，无交易日）
        ]
        assert aggregate_to_week(days) == [
            _week("TST", "20250815", 90, 95, 88, 92),
            _week("TST", "20250822", 100, 112, 98, 111),
            _week("TST", "20250829", 110, 120, 106, 117),
        ]

    def test_cross_year_week_rolls_to_the_monday_of_the_week(self) -> None:
        """跨年周：2025-12-29（周一）所在自然周包含 2026-01-02，bar_dt=该周最后交易日。"""
        days = [
            _bar("20251229", 200, 205, 198, 202),   # 周一
            _bar("20251230", 201, 206, 199, 203),
            _bar("20251231", 202, 207, 200, 204),
            _bar("20260102", 205, 210, 201, 208),   # 周五
        ]
        assert aggregate_to_week(days) == [
            _week("TST", "20260102", 200, 210, 198, 208),
        ]

    def test_single_day_is_its_own_week(self) -> None:
        days = [_bar("20250818", 100, 105, 98, 102)]
        assert aggregate_to_week(days) == [
            _week("TST", "20250818", 100, 105, 98, 102),
        ]


class TestAggregateToMonth:
    def test_single_month_open_close_and_extremes(self) -> None:
        days = [
            _bar("20250801", 100, 105, 98, 102),
            _bar("20250808", 103, 110, 100, 108),
            _bar("20250829", 106, 115, 102, 111),
        ]
        assert aggregate_to_month(days) == [
            _month("TST", "20250829", 100, 115, 98, 111),
        ]

    def test_cross_year_month_partitions_by_calendar_month(self) -> None:
        days = [
            _bar("20251230", 200, 205, 198, 202),
            _bar("20251231", 202, 207, 200, 204),
            _bar("20260102", 205, 210, 201, 208),
            _bar("20260130", 210, 220, 205, 215),
        ]
        assert aggregate_to_month(days) == [
            _month("TST", "20251231", 200, 207, 198, 204),
            _month("TST", "20260130", 205, 220, 201, 215),
        ]


class TestAggregateContracts:
    def test_empty_input_returns_empty_list(self) -> None:
        assert aggregate_to_week([]) == []
        assert aggregate_to_month([]) == []

    def test_same_input_aggregates_deterministically(self) -> None:
        days = [
            _bar("20250818", 100, 105, 98, 102),
            _bar("20250819", 103, 106, 99, 104),
            _bar("20250820", 105, 108, 101, 107),
            _bar("20250821", 106, 110, 102, 109),
            _bar("20250822", 108, 112, 104, 111),
            _bar("20250901", 120, 125, 115, 122),
        ]
        assert aggregate_to_week(days) == aggregate_to_week(days)
        assert aggregate_to_month(days) == aggregate_to_month(days)

    def test_unsorted_input_raises(self) -> None:
        days = [
            _bar("20250819", 103, 106, 99, 104),
            _bar("20250818", 100, 105, 98, 102),
        ]
        with pytest.raises(ValueError, match="strictly increasing"):
            aggregate_to_week(days)

    def test_non_day_input_raises(self) -> None:
        days = [_bar("20250818", 100, 105, 98, 102, timeframe="week")]
        with pytest.raises(ValueError, match="timeframe 'day'"):
            aggregate_to_week(days)

    def test_symbol_preserved_and_timeframe_set(self) -> None:
        days = [_bar("20250818", 100, 105, 98, 102, symbol="sh510300")]
        week = aggregate_to_week(days)
        month = aggregate_to_month(days)
        assert week[0].symbol == "sh510300" and week[0].timeframe == "week"
        assert month[0].symbol == "sh510300" and month[0].timeframe == "month"
