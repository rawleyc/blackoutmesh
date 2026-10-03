"""
WP 2.4 — Local Storage, Packet Pool & Expiry Garbage Collector

In-memory priority queue ordered by expires_at.
Automatic sweep purging packets where now > expires_at.
"""

import heapq
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

from .crypto import WirePacket


@dataclass
class PacketStore:
    """Thread-safe packet storage with expiry-based garbage collection.

    Stores received alert packets indexed by msg_id.
    Maintains a min-heap ordered by expires_at for efficient GC sweeps.
    """

    _packets: Dict[str, WirePacket] = field(default_factory=dict)
    _expiry_heap: List[Tuple[int, str]] = field(default_factory=list)
    _gc_count: int = 0

    def store(self, packet: WirePacket) -> bool:
        """Store a packet. Returns True if this is a new packet (not duplicate).

        Only call this AFTER verify_packet() has succeeded.
        """
        if packet.msg_id in self._packets:
            return False

        self._packets[packet.msg_id] = packet
        heapq.heappush(self._expiry_heap, (packet.expires_at, packet.msg_id))
        return True

    def contains(self, msg_id: str) -> bool:
        """Check if a message has already been received."""
        return msg_id in self._packets

    def get(self, msg_id: str) -> Optional[WirePacket]:
        """Retrieve a stored packet by msg_id."""
        return self._packets.get(msg_id)

    def gc_sweep(self, now_unix: float) -> int:
        """Remove all expired packets.

        Args:
            now_unix: Current Unix epoch time.

        Returns:
            Number of packets purged.
        """
        purged = 0
        while self._expiry_heap and self._expiry_heap[0][0] < now_unix:
            expires_at, msg_id = heapq.heappop(self._expiry_heap)
            if msg_id in self._packets and self._packets[msg_id].expires_at <= now_unix:
                del self._packets[msg_id]
                purged += 1
        self._gc_count += purged
        return purged

    @property
    def total_gc_purged(self) -> int:
        return self._gc_count

    def __len__(self) -> int:
        return len(self._packets)

    def __repr__(self) -> str:
        return f"PacketStore({len(self)} packets, {self._gc_count} purged)"
