"""
WP 1.4 — Dynamic Trickle State Machine (RFC 6206)
WP 2.2 — Trickle Timer & Adaptive Interval Doubler

RFC 6206 Trickle algorithm for BLE advertisement rate control.

State transitions:
  1. At start of each interval I, reset counter c=0, pick random t in [I/2, I).
  2. On hearing a consistent transmission (verified duplicate), increment c.
  3. At time t: if c < K, transmit; else suppress.
  4. At end of interval I: double interval -> min(I*2, I_max), go to step 1.
  5. On inconsistency (new neighbor detected): reset I to I_min, go to step 1.
"""

import random
from dataclasses import dataclass, field
from enum import Enum


class TrickleEvent(Enum):
    """Events emitted by the Trickle state machine."""
    TRANSMIT = "transmit"
    SUPPRESS = "suppress"
    INTERVAL_DOUBLED = "interval_doubled"
    RESET = "reset"
    NONE = "none"


@dataclass
class TrickleConfig:
    """Trickle algorithm parameters."""
    i_min: float = 1.0    # Minimum interval (seconds)
    i_max: float = 16.0   # Maximum interval (seconds)
    k: int = 3            # Redundancy constant


@dataclass
class TrickleState:
    """Complete state of one Trickle timer instance."""
    config: TrickleConfig = field(default_factory=TrickleConfig)
    interval: float = 1.0       # Current interval I
    interval_start: float = 0.0 # When this interval began (sim time)
    t: float = 0.5              # Transmission point within interval
    counter: int = 0            # c: heard count in this interval
    fired: bool = False         # Whether we've passed t this interval

    # Cumulative stats
    total_transmissions: int = 0
    total_suppressions: int = 0

    def reset(self, now: float, full: bool = False) -> TrickleEvent:
        """Reset the Trickle timer.

        Args:
            now: Current simulation time in seconds.
            full: If True, reset interval to I_min (inconsistency detected).
                  If False, keep current interval (normal interval boundary).

        Returns:
            TrickleEvent.RESET if full reset, else TrickleEvent.NONE.
        """
        if full:
            self.interval = self.config.i_min

        self.interval_start = now
        self.t = random.uniform(self.interval / 2.0, self.interval)
        self.fired = False
        self.counter = 0

        return TrickleEvent.RESET if full else TrickleEvent.NONE

    def hear_consistent(self) -> None:
        """Record hearing a consistent (verified duplicate) transmission.

        Called when we receive a verified duplicate of a message we already have.
        This increments the redundancy counter c.
        """
        self.counter += 1

    def tick(self, now: float) -> TrickleEvent:
        """Advance the Trickle timer by one tick.

        Call this once per simulation step (dt = 1s = BLE adv interval).

        Args:
            now: Current simulation time in seconds.

        Returns:
            TrickleEvent indicating what happened:
            - TRANSMIT: caller should broadcast
            - SUPPRESS: transmission was suppressed (c >= K)
            - INTERVAL_DOUBLED: interval boundary crossed
            - NONE: nothing happened
        """
        elapsed = now - self.interval_start
        event = TrickleEvent.NONE

        # Check if we've reached the transmission point t
        if not self.fired and elapsed >= self.t:
            self.fired = True
            if self.counter < self.config.k:
                self.total_transmissions += 1
                event = TrickleEvent.TRANSMIT
            else:
                self.total_suppressions += 1
                event = TrickleEvent.SUPPRESS

        # Check if interval has expired -> double and restart
        if elapsed >= self.interval:
            self.interval = min(self.interval * 2.0, self.config.i_max)
            self.reset(now, full=False)
            if event == TrickleEvent.NONE:
                event = TrickleEvent.INTERVAL_DOUBLED

        return event
