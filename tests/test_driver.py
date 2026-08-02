from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from malf.types import (
    Direction,
    PriceBar,
    RangeResolutionType,
    RangeState,
    RangeSnapshot,
    WaveLifespan,
    WaveStructuralSnapshot,
)

from malf_data.driver import MALFDriver, RangeLifecycleFacts, WaveLifecycleFacts


_MALF_ENGINE_FIXTURES = (
    Path(__file__).resolve().parents[2] / "malf-engine" / "tests" / "fixtures"
)


def _fixture_bars(name: str, subdir: str | None = None) -> list[PriceBar]:
    """从 malf-engine golden fixture 读取 input_bars（输入数据，非预期输出）。"""
    path = _MALF_ENGINE_FIXTURES / subdir / f"{name}.json" if subdir else _MALF_ENGINE_FIXTURES / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        PriceBar("sh999999", "day", b["bar_dt"], b["open"], b["high"], b["low"], b["close"])
        for b in data["input_bars"]
    ]


def _bar() -> PriceBar:
    return PriceBar(
        symbol="sh999999",
        timeframe="day",
        bar_dt="20990101",
        open=1000,
        high=1010,
        low=990,
        close=1005,
    )


def _peer(index: int, direction: Direction) -> WaveLifespan:
    year = 2000 + index
    return WaveLifespan(
        wave_id=f"peer-{index}",
        symbol="sh999999",
        timeframe="day",
        direction=direction,
        wave_start_bar_dt=f"{year:04d}0101",
        wave_end_bar_dt=f"{year:04d}0102",
        span_bars=10 + index,
        wave_start_price=1000,
        wave_end_price=1100 + index,
        price_range=100 + index,
        progress_pct=0.2 + index / 1000,
        primitive_count=3,
        pivot_count=4,
        new_count=1,
        no_new_span=1,
        span_rank=0.1 + index / 1000,
        range_rank=0.2 + index / 1000,
        stagnation_rank=0.3 + index / 1000,
        progress_rank=0.4 + index / 1000,
    )


def _facts() -> WaveLifecycleFacts:
    return WaveLifecycleFacts(
        wave_id="current-wave",
        direction=Direction.UP,
        wave_start_bar_dt="20980101",
        wave_start_price=1000,
        wave_end_bar_dt="20990101",
        wave_end_price=1500,
        span_bars=80,
        primitive_count=3,
        pivot_count=12,
        new_count=8,
        no_new_span=2,
        first_pivot_price=1050,
        guard_price=900,
        current_wave_is_alive=True,
    )


class _SyntheticFacts:
    def wave_facts(self, bar: PriceBar, core: object) -> WaveLifecycleFacts:
        return _facts()

    def range_facts(self, bar: PriceBar, core: object) -> RangeLifecycleFacts | None:
        return None


class _SyntheticWaveAndRangeFacts(_SyntheticFacts):
    def range_facts(self, bar: PriceBar, core: object) -> RangeLifecycleFacts:
        return RangeLifecycleFacts(
            range_id="current-range",
            range_type=RangeResolutionType.CONTINUATION,
            range_start_bar_dt="20980102",
            range_end_bar_dt="20990101",
            span_bars=40,
            evolution_count=4,
            replacement_count=2,
            resolution_distance=25,
            boundary_high_init=1100,
            boundary_low_init=900,
            boundary_high_now=1120,
            boundary_low_now=880,
            resolution_type="up",  # RangeLifecycleFacts 字段（方向语义）
            confirmation_pivot_extreme_price=1145,
        )

    def active_range(self, bar: PriceBar, core: object) -> RangeSnapshot:
        return RangeSnapshot(
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            bar_dt=bar.bar_dt,
            range_id="active-range",
            range_state=RangeState.ALIVE,
            birth_bar_dt="20980102",
            boundary_init_high=1100,
            boundary_init_low=900,
            boundary_now_high=1120,
            boundary_now_low=880,
            break_direction=Direction.UP,
            old_wave_direction=Direction.UP,
            range_rule_version="range-v0.0.1",
            schema_version="malf-range-snapshot-v0",
            evolution_count=4,
        )


def _seed_history(driver: MALFDriver) -> None:
    for index in range(30):
        driver.lifespan_engine.record_terminated_wave(_peer(index, Direction.UP))
        driver.lifespan_engine.record_terminated_wave(_peer(index + 30, Direction.DOWN))


def _wave(wave_id: str, direction: Direction, rank: float | None) -> WaveLifespan:
    """构造带指定 rank 的 WaveLifespan（正序历史用，其余字段简化）。"""
    return WaveLifespan(
        wave_id=wave_id,
        symbol="sh999999",
        timeframe="day",
        direction=direction,
        wave_start_bar_dt="20980101",
        wave_end_bar_dt="20990101",
        span_bars=10,
        wave_start_price=1000,
        wave_end_price=1100,
        price_range=100,
        progress_pct=0.2,
        primitive_count=3,
        pivot_count=4,
        new_count=1,
        no_new_span=1,
        span_rank=rank,
        range_rank=rank,
        stagnation_rank=rank,
        progress_rank=rank,
    )


def _seed_range_history(driver: MALFDriver) -> None:
    for index in range(30):
        lifespan = driver.lifespan_engine.calculate_range_lifespan(
            range_id=f"peer-range-{index}",
            symbol="sh999999",
            timeframe="day",
            range_type=RangeResolutionType.CONTINUATION,
            range_start_bar_dt=f"{1990 + index:04d}0101",
            range_end_bar_dt=f"{1990 + index:04d}0102",
            span_bars=10 + index,
            evolution_count=1 + index,
            replacement_count=index,
            resolution_distance=10 + index,
            boundary_high_init=1100,
            boundary_low_init=900,
            boundary_high_now=1110 + index,
            boundary_low_now=890,
            breakout_direction="up",
            confirmation_pivot_extreme_price=1120 + index,
        )
        driver.lifespan_engine.record_resolved_range(lifespan)


def test_driver_uses_real_malf_layers_in_canonical_order() -> None:
    events: list[str] = []
    driver = MALFDriver(
        lifecycle_facts=_SyntheticFacts(),
        event_sink=events.append,
        data_stale=True,
    )
    _seed_history(driver)

    snapshot = driver.on_bar(_bar())

    assert events == [
        "core.on_bar",
        "lifespan.calculate_wave_lifespan",
        "rank.filter_peer_sample",
        "rank.calculate_wave_ranks",
        "rank.update_wave_lifespan_with_ranks",
        "position.build_p1_view",
        "position.build_p2_view",
        "position.build_p3_view",
        "position.build_p4_view",
        "service.build_wave_structural_snapshot",
    ]
    assert len(fields(WaveStructuralSnapshot)) == 44
    assert snapshot.wave_span_rank is not None
    assert snapshot.p2_same_dir_span_momentum is not None
    assert snapshot.p3_cross_dir_span_momentum is not None
    assert snapshot.p4_cross_span_momentum is not None
    assert snapshot.usage == "research_only"
    assert snapshot.freshness == "stale_research_only"


def test_public_core_default_preserves_unavailable_lifespan_as_none() -> None:
    snapshot = MALFDriver(data_stale=True).on_bar(_bar())

    assert snapshot.wave_span_rank is None
    assert snapshot.p2_same_dir_span_momentum is None
    assert snapshot.p3_cross_dir_span_momentum is None
    assert snapshot.reason_codes
    assert "peer_sample_insufficient" in snapshot.reason_codes


def test_driver_calls_range_lifespan_and_rank_when_public_facts_are_supplied() -> None:
    events: list[str] = []
    driver = MALFDriver(
        lifecycle_facts=_SyntheticWaveAndRangeFacts(),
        event_sink=events.append,
        data_stale=True,
    )
    _seed_history(driver)
    _seed_range_history(driver)

    snapshot = driver.on_bar(_bar())

    assert events == [
        "core.on_bar",
        "lifespan.calculate_wave_lifespan",
        "lifespan.calculate_range_lifespan",
        "rank.filter_peer_sample",
        "rank.calculate_wave_ranks",
        "rank.update_wave_lifespan_with_ranks",
        "rank.filter_range_peer_sample",
        "rank.calculate_range_ranks",
        "rank.update_range_lifespan_with_ranks",
        "position.build_p1_view",
        "position.build_p2_view",
        "position.build_p3_view",
        "position.build_p4_view",
        "service.build_wave_structural_snapshot",
    ]
    assert snapshot.range_span_rank is not None
    assert snapshot.range_boundary_high_now == 1120
    assert snapshot.range_boundary_low_now == 880
    assert snapshot.range_evolution_rank is not None
    assert snapshot.range_replacement_rank is not None
    assert snapshot.range_resolution_distance_rank is not None
    assert len(driver.lifespan_engine.get_resolved_ranges()) == 31


class _SequencedWaveFacts(_SyntheticFacts):
    def __init__(self) -> None:
        self.calls = 0

    def wave_facts(self, bar: PriceBar, core: object) -> WaveLifecycleFacts:
        self.calls += 1
        current = _facts()
        if self.calls == 1:
            return WaveLifecycleFacts(**{**current.__dict__, "current_wave_is_alive": False})
        return WaveLifecycleFacts(**{**current.__dict__, "wave_id": "next-wave"})


def test_driver_records_terminated_lifespans_for_future_rank_peers() -> None:
    """A completed lifespan enters the public history only after its Service view."""
    facts = _SequencedWaveFacts()
    driver = MALFDriver(lifecycle_facts=facts, data_stale=True)
    _seed_history(driver)

    first = driver.on_bar(_bar())
    second = driver.on_bar(
        PriceBar(
            symbol="sh999999",
            timeframe="day",
            bar_dt="20990102",
            open=1001,
            high=1011,
            low=991,
            close=1006,
        )
    )

    assert first.wave_span_rank is not None
    assert len(driver.lifespan_engine.get_terminated_waves()) == 61
    assert second.wave_span_rank is not None
    assert second.p2_same_dir_span_momentum is not None


def test_default_provider_forms_wave_ranks_with_sufficient_peers() -> None:
    """DECISION-004 §2.6: 默认 provider 消费公开 Core facts，不再恒为 None。

    不注入任何 provider；喂 t3_same_direction_break_up 的完整 bar 序列（产生
    UP wave：d08 进 alive → d09 break 终止），预置 30 个 UP/DOWN peer 历史。
    期望 break bar（d09，terminated_wave 事件）的 wave rank 与 P2-P4 形成。
    """
    driver = MALFDriver(data_stale=True)
    _seed_history(driver)

    snapshots = [driver.on_bar(b) for b in _fixture_bars("t3_same_direction_break_up")]
    break_snap = next(s for s in snapshots if s.bar_dt == "d09")

    assert break_snap.wave_span_rank is not None
    assert break_snap.wave_range_rank is not None
    assert break_snap.wave_stagnation_rank is not None
    assert break_snap.p2_same_dir_span_momentum is not None
    assert break_snap.p3_cross_dir_span_momentum is not None
    assert break_snap.p4_cross_span_momentum is not None
    assert "peer_sample_insufficient" not in break_snap.reason_codes
    assert len(driver.lifespan_engine.get_terminated_waves()) == 61  # 30 UP + 30 DOWN + 刚终止的 1


def test_driver_passes_reversed_terminated_waves_to_p2_p3() -> None:
    """P2/P3 顺序 bug 回归（2026-08-02 真实数据暴露）。

    build_p2_view/build_p3_view 约定 terminated_waves 按时间倒序（W-1 在前），
    而 get_terminated_waves() 返回 append 正序（最早在前）。driver 必须反转后
    传给 P2/P3，否则 [:3] 取到最早的 3 个波（其 rank 恒 None）→ P2/P3 恒 None。

    构造正序历史：最早 30 个 rank=None（老波，peer 不足），最近几个 rank 非空。
    修复前：driver 传正序 → P2/P3 取最早 3 个（rank=None）→ None（FAIL）；
    修复后：driver 传倒序 → P2/P3 取最近 3 个（rank 非空）→ 非 None（PASS）。
    """
    driver = MALFDriver(data_stale=True)
    # 最早 30 个 UP rank=None（真实数据早期 peer 不足的诚实状态）
    for index in range(30):
        driver.lifespan_engine.record_terminated_wave(
            _wave(f"old-up-{index}", Direction.UP, rank=None)
        )
        driver.lifespan_engine.record_terminated_wave(
            _wave(f"old-down-{index}", Direction.DOWN, rank=None)
        )
    # 最近 3 个：rank 非空（后期 peer 充足）
    driver.lifespan_engine.record_terminated_wave(_wave("recent-1", Direction.DOWN, rank=0.4))
    driver.lifespan_engine.record_terminated_wave(_wave("recent-2", Direction.UP, rank=0.5))
    driver.lifespan_engine.record_terminated_wave(_wave("recent-3", Direction.UP, rank=0.6))

    snapshots = [driver.on_bar(b) for b in _fixture_bars("t3_same_direction_break_up")]
    break_snap = next(s for s in snapshots if s.bar_dt == "d09")

    # 修复前：driver 传正序 → 最早 3 个 rank=None → P2/P3 恒 None
    # 注意：44 字段 Service 合同只持久化 P2/P3 的 span/range momentum（无 stagnation）
    assert break_snap.p2_same_dir_span_momentum is not None
    assert break_snap.p2_same_dir_range_momentum is not None
    assert break_snap.p2_same_dir_label is not None
    assert break_snap.p3_cross_dir_span_momentum is not None
    assert break_snap.p3_cross_dir_range_momentum is not None
    assert break_snap.p3_cross_dir_label is not None


def test_default_provider_forms_range_ranks_with_sufficient_peers() -> None:
    """DECISION-004 §2.6: 默认 provider 消费公开 range facts，range rank 形成。

    喂 R1_continuation（d14 break → d20 resolution），预置 30 个 continuation
    range peer。期望 resolution bar（d20）的 range 四 rank 与 boundary 字段形成。
    """
    driver = MALFDriver(data_stale=True)
    _seed_history(driver)
    _seed_range_history(driver)

    snapshots = [
        driver.on_bar(b)
        for b in _fixture_bars("R1_continuation_down_break_down_resolve", subdir="range")
    ]
    # d20 是 resolution bar：active_range 已消失（Service 的 range_boundary_* 来自 active_range，
    # resolved 后诚实为 None），但 range rank 来自 resolved_range，非 None。
    resolved = next(s for s in snapshots if s.bar_dt == "d20")
    alive_range = next(s for s in snapshots if s.bar_dt == "d19")

    assert resolved.range_span_rank is not None
    assert resolved.range_evolution_rank is not None
    assert resolved.range_replacement_rank is not None
    assert resolved.range_resolution_distance_rank is not None
    # range alive 期间（d19）boundary 字段形成
    assert alive_range.range_boundary_high_now is not None
    assert alive_range.range_boundary_low_now is not None
    assert len(driver.lifespan_engine.get_resolved_ranges()) == 31  # 30 peer + 刚 resolved 的 1

