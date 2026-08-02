"""Explicit MALF API driver boundary for T02.

The production default uses only public Core snapshots.  Lifespan/Rank/Position
are invoked only when an approved lifecycle-facts provider supplies the public
facts required by the existing malf-engine APIs; the driver never reads Core
private attributes and never manufactures lifecycle facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from malf.core_engine import MALFCoreEngine
from malf.lifespan_engine import LifespanEngine
from malf.rank_engine import RankEngine
from malf.service_engine import build_wave_structural_snapshot
from malf.structural_position_engine import StructuralPositionEngine
from malf.types import (
    CoreStateSnapshot,
    Direction,
    PriceBar,
    RangeLifespan,
    RangeResolutionType,
    RangeSnapshot,
    WaveLifespan,
    WaveStructuralSnapshot,
)


ADAPTER_VERSION = "malf-v2.0-etf-tick-v0.1"


@dataclass(frozen=True)
class WaveLifecycleFacts:
    """Explicit facts required by LifespanEngine.calculate_wave_lifespan."""

    wave_id: str
    direction: Direction
    wave_start_bar_dt: str
    wave_start_price: int
    wave_end_bar_dt: str
    wave_end_price: int
    span_bars: int
    primitive_count: int
    pivot_count: int
    new_count: int
    no_new_span: int
    first_pivot_price: int
    guard_price: int
    current_wave_is_alive: bool = True


@dataclass(frozen=True)
class RangeLifecycleFacts:
    """Explicit facts required by LifespanEngine.calculate_range_lifespan."""

    range_id: str
    range_type: RangeResolutionType
    range_start_bar_dt: str
    range_end_bar_dt: str
    span_bars: int
    evolution_count: int
    replacement_count: int
    resolution_distance: int
    boundary_high_init: int
    boundary_low_init: int
    boundary_high_now: int
    boundary_low_now: int
    resolution_type: str
    confirmation_pivot_extreme_price: int
    record_resolved: bool = True


class LifecycleFactsProvider(Protocol):
    """Provider seam for lifecycle facts not currently exposed by Core snapshots."""

    def wave_facts(
        self, bar: PriceBar, core: CoreStateSnapshot
    ) -> WaveLifecycleFacts | None:
        """Return complete wave facts for this bar, or None when unavailable."""

    def range_facts(
        self, bar: PriceBar, core: CoreStateSnapshot
    ) -> RangeLifecycleFacts | None:
        """Return complete resolved-range facts for this bar, or None when unavailable."""

    def active_range(
        self, bar: PriceBar, core: CoreStateSnapshot
    ) -> RangeSnapshot | None:
        """Return a public active-range snapshot when one is available."""


class PublicCoreOnlyLifecycleFacts:
    """Production-safe default: do not infer unavailable lifecycle facts."""

    def wave_facts(
        self, bar: PriceBar, core: CoreStateSnapshot
    ) -> WaveLifecycleFacts | None:
        return None

    def range_facts(
        self, bar: PriceBar, core: CoreStateSnapshot
    ) -> RangeLifecycleFacts | None:
        return None

    def active_range(
        self, bar: PriceBar, core: CoreStateSnapshot
    ) -> RangeSnapshot | None:
        return None


class MALFDriver:
    """Own the actual MALF engines and build one Service snapshot per bar."""

    def __init__(
        self,
        *,
        malf_k: int = 2,
        lifecycle_facts: LifecycleFactsProvider | None = None,
        event_sink: Callable[[str], None] | None = None,
        data_stale: bool = True,
    ) -> None:
        self.core_engine = MALFCoreEngine(k=malf_k)
        self.lifespan_engine = LifespanEngine()
        self.rank_engine = RankEngine()
        self.position_engine = StructuralPositionEngine()
        self.lifecycle_facts = lifecycle_facts or PublicCoreOnlyLifecycleFacts()
        self.event_sink = event_sink
        self.data_stale = data_stale

    def on_bar(self, bar: PriceBar) -> WaveStructuralSnapshot:
        """Advance actual public MALF APIs in canonical order for one bar."""
        self._event("core.on_bar")
        core = self.core_engine.on_bar(bar)
        rule_versions = _rule_versions(core)

        active_range_provider = getattr(self.lifecycle_facts, "active_range", None)
        active_range = (
            active_range_provider(bar, core)
            if callable(active_range_provider)
            else None
        )
        wave_facts = self.lifecycle_facts.wave_facts(bar, core)
        range_facts = self.lifecycle_facts.range_facts(bar, core)
        wave_lifespan = self._build_wave_lifespan(bar, wave_facts)
        range_lifespan = self._build_range_lifespan(bar, range_facts)
        p1 = p2 = p3 = p4 = None
        peer_sample_sufficient = False

        if wave_lifespan is not None and wave_facts is not None:
            self._event("rank.filter_peer_sample")
            peers = self.rank_engine.filter_peer_sample(
                self.lifespan_engine.get_terminated_waves(wave_lifespan.direction),
                direction=wave_lifespan.direction,
                cutoff_bar_dt=bar.bar_dt,
            )
            wave_peer_sample_sufficient = len(peers) >= self.rank_engine.MIN_SAMPLE_SIZE
            peer_sample_sufficient = wave_peer_sample_sufficient

            self._event("rank.calculate_wave_ranks")
            ranks = self.rank_engine.calculate_wave_ranks(wave_lifespan, peers)

            self._event("rank.update_wave_lifespan_with_ranks")
            wave_lifespan = self.rank_engine.update_wave_lifespan_with_ranks(
                wave_lifespan, ranks
            )

        if range_lifespan is not None and range_facts is not None:
            self._event("rank.filter_range_peer_sample")
            range_peers = self.rank_engine.filter_range_peer_sample(
                self.lifespan_engine.get_resolved_ranges(range_lifespan.range_type),
                range_type=range_lifespan.range_type,
                cutoff_bar_dt=bar.bar_dt,
            )
            range_peer_sample_sufficient = len(range_peers) >= self.rank_engine.MIN_SAMPLE_SIZE
            peer_sample_sufficient = peer_sample_sufficient and range_peer_sample_sufficient

            self._event("rank.calculate_range_ranks")
            range_ranks = self.rank_engine.calculate_range_ranks(range_lifespan, range_peers)

            self._event("rank.update_range_lifespan_with_ranks")
            range_lifespan = self.rank_engine.update_range_lifespan_with_ranks(
                range_lifespan, range_ranks
            )

        if wave_lifespan is not None and wave_facts is not None:
            self._event("position.build_p1_view")
            p1 = self.position_engine.build_p1_view(wave_lifespan)

            terminated_waves = self.lifespan_engine.get_terminated_waves()
            self._event("position.build_p2_view")
            p2 = self.position_engine.build_p2_view(wave_lifespan, terminated_waves)

            self._event("position.build_p3_view")
            p3 = self.position_engine.build_p3_view(wave_lifespan, terminated_waves)

            self._event("position.build_p4_view")
            p4 = self.position_engine.build_p4_view(
                wave_lifespan,
                terminated_waves[-1] if terminated_waves else None,
                wave_facts.current_wave_is_alive,
            )

        self._event("service.build_wave_structural_snapshot")
        snapshot = build_wave_structural_snapshot(
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            bar_dt=bar.bar_dt,
            bar_index=core.bar_index,
            core=core,
            active_range=active_range,
            wave_lifespan=wave_lifespan,
            range_lifespan=range_lifespan,
            p1=p1,
            p2=p2,
            p3=p3,
            p4=p4,
            rule_versions=rule_versions,
            lineage_hash=None,
            input_integrity_passed=True,
            peer_sample_sufficient=peer_sample_sufficient,
            data_stale=self.data_stale,
            operational_enabled=False,
        )

        # Record only after the current Service snapshot has been assembled.
        # This keeps the current wave/range out of its own peer sample while
        # making completed public facts available to the next bar.
        if wave_lifespan is not None and wave_facts is not None:
            if not wave_facts.current_wave_is_alive:
                self.lifespan_engine.record_terminated_wave(wave_lifespan)
        if (
            range_lifespan is not None
            and range_facts is not None
            and range_facts.record_resolved
        ):
            self.lifespan_engine.record_resolved_range(range_lifespan)

        return snapshot

    def _build_range_lifespan(
        self, bar: PriceBar, facts: RangeLifecycleFacts | None
    ) -> RangeLifespan | None:
        if facts is None:
            return None
        self._event("lifespan.calculate_range_lifespan")
        return self.lifespan_engine.calculate_range_lifespan(
            range_id=facts.range_id,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            range_type=facts.range_type,
            range_start_bar_dt=facts.range_start_bar_dt,
            range_end_bar_dt=facts.range_end_bar_dt,
            span_bars=facts.span_bars,
            evolution_count=facts.evolution_count,
            replacement_count=facts.replacement_count,
            resolution_distance=facts.resolution_distance,
            boundary_high_init=facts.boundary_high_init,
            boundary_low_init=facts.boundary_low_init,
            boundary_high_now=facts.boundary_high_now,
            boundary_low_now=facts.boundary_low_now,
            resolution_type=facts.resolution_type,
            confirmation_pivot_extreme_price=facts.confirmation_pivot_extreme_price,
        )

    def _build_wave_lifespan(
        self, bar: PriceBar, facts: WaveLifecycleFacts | None
    ) -> WaveLifespan | None:
        if facts is None:
            return None
        self._event("lifespan.calculate_wave_lifespan")
        return self.lifespan_engine.calculate_wave_lifespan(
            wave_id=facts.wave_id,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            direction=facts.direction,
            wave_start_bar_dt=facts.wave_start_bar_dt,
            wave_start_price=facts.wave_start_price,
            wave_end_bar_dt=facts.wave_end_bar_dt,
            wave_end_price=facts.wave_end_price,
            span_bars=facts.span_bars,
            primitive_count=facts.primitive_count,
            pivot_count=facts.pivot_count,
            new_count=facts.new_count,
            no_new_span=facts.no_new_span,
            first_pivot_price=facts.first_pivot_price,
            guard_price=facts.guard_price,
        )

    def _event(self, name: str) -> None:
        if self.event_sink is not None:
            self.event_sink(name)


def _rule_versions(core: CoreStateSnapshot) -> dict[str, str]:
    return {
        "adapter": ADAPTER_VERSION,
        "core": core.core_rule_version,
        "pivot": core.pivot_detection_rule_version,
        "price_policy": core.price_policy,
        "schema": core.schema_version,
    }
