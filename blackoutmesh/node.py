"""
WP 2.0 — Node Core & Local Daemon Engine

Integrates:
  - WP 2.1: Cryptographic Verifier (Pre-Filter Pipeline)
  - WP 2.2: Trickle Timer (via TrickleState)
  - WP 2.3: Neighbor Churn Detector
  - WP 2.4: Storage with Expiry GC (via PacketStore)

The receive() pipeline enforces the Strict Ordering Law:
  1. Verify signature + expiry  (crypto.verify_packet)
  2. THEN check duplicate       (storage.contains)
  3. THEN store + trigger Trickle reset
"""

from dataclasses import dataclass, field
from typing import Optional, Set

from .crypto import WirePacket, verify_packet
from .trickle import TrickleState, TrickleConfig, TrickleEvent
from .storage import PacketStore


@dataclass
class NodeStats:
    """Counters for a single node."""
    valid_receptions: int = 0
    duplicate_receptions: int = 0
    invalid_packets: int = 0
    expired_packets: int = 0
    transmissions: int = 0
    suppressions: int = 0


class MeshNode:
    """A single BlackoutMesh device (civilian phone or seed device).

    This class encapsulates the full receive/transmit pipeline:
    verify -> deduplicate -> store -> trickle-manage -> broadcast.
    """

    def __init__(
        self,
        node_id: str,
        trusted_pubkeys: Set[str],
        trickle_config: Optional[TrickleConfig] = None,
        is_seed: bool = False,
    ):
        self.node_id = node_id
        self.trusted_pubkeys = trusted_pubkeys
        self.is_seed = is_seed

        self.store = PacketStore()
        self.trickle = TrickleState(config=trickle_config or TrickleConfig())
        self.stats = NodeStats()

        # Neighbor churn tracking (WP 2.3)
        self._prev_neighbors: Set[str] = set()
        self._current_neighbors: Set[str] = set()

    def receive(self, packet: WirePacket, now_unix: float) -> str:
        """Process an incoming packet through the full verification pipeline.

        STRICT ORDERING LAW:
          Step 1: Verify cryptographic signature + expiry + trust
          Step 2: Check for duplicate ONLY after verification passes
          Step 3: Store and trigger Trickle reset

        Args:
            packet: The incoming WirePacket.
            now_unix: Current Unix epoch time.

        Returns:
            One of: "accepted", "duplicate", "invalid", "expired"
        """
        # STEP 1: Cryptographic verification BEFORE anything else
        valid, reason = verify_packet(packet, self.trusted_pubkeys, now_unix)
        if not valid:
            if reason == "expired":
                self.stats.expired_packets += 1
                return "expired"
            else:
                self.stats.invalid_packets += 1
                return "invalid"

        # STEP 2: Duplicate check (only reached for verified packets)
        if self.store.contains(packet.msg_id):
            self.stats.duplicate_receptions += 1
            self.trickle.hear_consistent()
            return "duplicate"

        # STEP 3: New valid packet — store and reset Trickle
        decremented = WirePacket(
            msg_id=packet.msg_id,
            timestamp=packet.timestamp,
            expires_at=packet.expires_at,
            alert_type=packet.alert_type,
            body=packet.body,
            ttl=max(0, packet.ttl - 1),
            signature_hex=packet.signature_hex,
            pubkey_hex=packet.pubkey_hex,
            origin_node=packet.origin_node,
        )
        self.store.store(decremented)
        self.stats.valid_receptions += 1
        return "accepted"

    def update_neighbors(self, current_neighbor_ids: Set[str], now_sim: float) -> bool:
        """Detect neighbor churn and trigger Trickle reset if needed.

        WP 2.3: Computes delta_neighbors = current \\ previous.
        If |delta| > 0, resets Trickle to I_min (store-carry-forward).

        Args:
            current_neighbor_ids: Set of neighbor node IDs visible right now.
            now_sim: Current simulation time (seconds from start).

        Returns:
            True if new neighbors were detected (Trickle was reset).
        """
        new_neighbors = current_neighbor_ids - self._prev_neighbors
        self._prev_neighbors = current_neighbor_ids.copy()

        if new_neighbors and len(self.store) > 0:
            self.trickle.reset(now_sim, full=True)
            return True
        return False

    def gc_sweep(self, now_unix: float) -> int:
        """Run garbage collection on expired packets."""
        return self.store.gc_sweep(now_unix)
