"""D3：完整交易日 / as-of 门禁（RED→GREEN）。

合同：approved_as_of_date = 20260804。
- day 输入进入引擎前按 as_of_date 截断；
- week/month 从截断后的 day 序列重新聚合；
- 生产模式要求显式 as_of_date，不依赖机器系统日期。
"""

from __future__ import annotations

import duckdb
import pytest

from malf.types import PriceBar
from malf_data.adapters.tdx_reader import read_tdx_day
from malf_data.ingest import apply_as_of_cutoff, ingest_symbol
from pathlib import Path

TDX_ROOT = Path(r"Z:\new_tdx64")
APPROVED_AS_OF = "20260804"


def _bar(dt: str, price: int = 100) -> PriceBar:
    return PriceBar("TEST", "day", dt, price, price + 2, price - 1, price)


# ---- RED：纯函数截断 ----

def test_as_of_cutoff_filters_later_bars():
    """20260805 及以后的 bar 必须在 as_of=20260804 下被截断。"""
    bars = [_bar("20260803"), _bar("20260804"), _bar("20260805"), _bar("20260806")]
    cut = apply_as_of_cutoff(bars, APPROVED_AS_OF)
    assert [b.bar_dt for b in cut] == ["20260803", "20260804"]
    assert cut[-1].bar_dt == APPROVED_AS_OF


def test_as_of_cutoff_noop_when_all_within():
    """全部 bar ≤ as_of 时不截断。"""
    bars = [_bar("20260803"), _bar("20260804")]
    cut = apply_as_of_cutoff(bars, APPROVED_AS_OF)
    assert len(cut) == 2


def test_as_of_cutoff_none_returns_original():
    """as_of_date=None 保持原样（兼容非 D3 调用）。"""
    bars = [_bar("20260805")]
    assert apply_as_of_cutoff(bars, None) == bars


# ---- RED：day 集成（真实 TDX 源，含 20260805 时验证截断） ----

@pytest.mark.skipif(
    not (TDX_ROOT / "vipdoc" / "sh" / "lday" / "sh510050.day").exists(),
    reason="TDX 源不存在（不影响单元测试）",
)
def test_ingest_day_as_of_truncates_to_approved(tmp_path):
    """TDX 源含 20260805 时，ingest_symbol(as_of_date=20260804) 后 max_bar_dt 必须为 20260804。"""
    db = tmp_path / "asof-day.duckdb"
    result = ingest_symbol(
        "sh510050",
        timeframe="day",
        tdx_root=TDX_ROOT,
        db_path=db,
        as_of_date=APPROVED_AS_OF,
    )
    con = duckdb.connect(str(db), read_only=True)
    mx = con.execute("SELECT MAX(bar_dt) FROM snapshots").fetchone()[0]
    con.close()
    assert mx <= APPROVED_AS_OF
    assert mx == APPROVED_AS_OF  # 源含 20260805 时必须被截断


# ---- RED：week/month 从截断 day 重聚合 ----

@pytest.mark.skipif(
    not (TDX_ROOT / "vipdoc" / "sh" / "lday" / "sh510050.day").exists(),
    reason="TDX 源不存在（不影响单元测试）",
)
@pytest.mark.parametrize("tf", ["week", "month"])
def test_ingest_aggregate_as_of_uses_truncated_day(tmp_path, tf: str):
    """week/month 必须从截断后的 day 序列重聚合，max(bar_dt) <= 20260804。"""
    db = tmp_path / f"asof-{tf}.duckdb"
    ingest_symbol(
        "sh510050",
        timeframe=tf,
        tdx_root=TDX_ROOT,
        db_path=db,
        as_of_date=APPROVED_AS_OF,
    )
    con = duckdb.connect(str(db), read_only=True)
    mx = con.execute("SELECT MAX(bar_dt) FROM snapshots").fetchone()[0]
    con.close()
    assert mx <= APPROVED_AS_OF
