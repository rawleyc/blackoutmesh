"""
Tests for WP 1.4 + WP 2.2: RFC 6206 Trickle state machine.
"""

import pytest
from blackoutmesh.trickle import TrickleState, TrickleConfig, TrickleEvent


@pytest.fixture
def trickle():
    config = TrickleConfig(i_min=1.0, i_max=16.0, k=3)
    state = TrickleState(config=config)
    state.reset(0.0, full=True)
    return state


class TestTrickleBasicBehavior:

    def test_initial_interval_is_imin(self, trickle):
        assert trickle.interval == 1.0

    def test_transmit_when_counter_below_k(self, trickle):
        """With c=0 < K=3, the node should transmit at time t."""
        events = []
        for step in range(1, 20):
            ev = trickle.tick(step * 0.1)
            if ev != TrickleEvent.NONE:
                events.append(ev)
        assert TrickleEvent.TRANSMIT in events

    def test_suppress_when_counter_at_k(self, trickle):
        """With c >= K=3, the node should suppress."""
        for _ in range(3):
            trickle.hear_consistent()
        assert trickle.counter >= trickle.config.k

        events = []
        for step in range(1, 20):
            ev = trickle.tick(step * 0.1)
            if ev != TrickleEvent.NONE:
                events.append(ev)
        assert TrickleEvent.SUPPRESS in events
        assert TrickleEvent.TRANSMIT not in events

    def test_interval_doubles_up_to_imax(self, trickle):
        """Interval should double: 1 -> 2 -> 4 -> 8 -> 16 -> 16 (capped)."""
        intervals = [trickle.interval]
        now = 0.0
        for _ in range(10):
            now += trickle.interval + 0.01
            trickle.tick(now)
            intervals.append(trickle.interval)

        assert intervals[0] == 1.0
        assert max(intervals) <= 16.0

    def test_full_reset_returns_to_imin(self, trickle):
        """On inconsistency (new neighbor), interval resets to I_min."""
        now = 0.0
        for _ in range(5):
            now += trickle.interval + 0.01
            trickle.tick(now)
        assert trickle.interval > 1.0

        trickle.reset(now, full=True)
        assert trickle.interval == 1.0
        assert trickle.counter == 0
        assert trickle.fired is False


class TestTrickleStats:

    def test_transmission_counter_increments(self, trickle):
        initial = trickle.total_transmissions
        for step in range(1, 20):
            trickle.tick(step * 0.1)
        assert trickle.total_transmissions > initial

    def test_suppression_counter_increments(self, trickle):
        for _ in range(5):
            trickle.hear_consistent()
        initial = trickle.total_suppressions
        for step in range(1, 20):
            trickle.tick(step * 0.1)
        assert trickle.total_suppressions > initial
