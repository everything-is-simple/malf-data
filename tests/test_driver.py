from __future__ import annotations

from dataclasses import fields

from malf.types import (
    Direction,
    PriceBar,
    RangeResolutionType,
    WaveLifespan,
    WaveStructuralSnapshot,
)

from malf_data.driver import MALFDriver, RangeLifecycleFacts, WaveLifecycleFacts


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
            resolution_type="up",
            confirmation_pivot_extreme_price=1145,
        )


def _seed_history(driver: MALFDriver) -> None:
    for index in range(30):
        driver.lifespan_engine.record_terminated_wave(_peer(index, Direction.UP))
        driver.lifespan_engine.record_terminated_wave(_peer(index + 30, Direction.DOWN))


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
            resolution_type="up",
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
    assert snapshot.range_evolution_rank is not None
    assert snapshot.range_replacement_rank is not None
    assert snapshot.range_resolution_distance_rank is not None
