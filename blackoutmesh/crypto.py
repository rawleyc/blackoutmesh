"""
WP 1.1 — Canonical Wire Format & Binary Serialization
WP 1.3 — Signed Temporal Bounding (Anti-Replay Window)

Wire format:
  Signed (immutable):  msg_id, timestamp, expires_at, alert_type, body
  Unsigned (mutable):  ttl  (decremented in transit, NOT covered by signature)

Canonical serialization: sorted-key JSON, separators=(',', ':'), UTF-8 encoded.
"""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Set, Tuple

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature


@dataclass(frozen=True)
class AlertPayload:
    """Immutable signed fields of an alert packet."""
    msg_id: str
    timestamp: int        # Unix epoch seconds when alert was created
    expires_at: int       # Unix epoch seconds when alert becomes invalid
    alert_type: str       # e.g., "CIVIL_DEFENSE_EVAC", "CHEMICAL_SPILL"
    body: str             # Human-readable alert text

    def canonical_bytes(self) -> bytes:
        """Deterministic JSON serialization for signing/verification.

        Strict sorted ASCII-encoded JSON with minimal separators.
        This is the ONLY representation that may be signed or verified.
        """
        payload = {
            "alert_type": self.alert_type,
            "body": self.body,
            "expires_at": self.expires_at,
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")


@dataclass
class WirePacket:
    """Complete over-the-air packet: signed payload + mutable metadata."""
    # Signed payload fields
    msg_id: str
    timestamp: int
    expires_at: int
    alert_type: str
    body: str

    # Mutable in transit (NOT signed)
    ttl: int              # Remaining civilian hops [0, 15]

    # Cryptographic envelope
    signature_hex: str
    pubkey_hex: str

    # Transmission metadata
    origin_node: str = ""

    @property
    def payload(self) -> AlertPayload:
        return AlertPayload(
            msg_id=self.msg_id,
            timestamp=self.timestamp,
            expires_at=self.expires_at,
            alert_type=self.alert_type,
            body=self.body,
        )

    def to_wire_dict(self) -> dict:
        """Serialize to dictionary for transmission."""
        return {
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "expires_at": self.expires_at,
            "alert_type": self.alert_type,
            "body": self.body,
            "ttl": self.ttl,
            "signature_hex": self.signature_hex,
            "pubkey_hex": self.pubkey_hex,
            "origin_node": self.origin_node,
        }

    @classmethod
    def from_wire_dict(cls, data: dict) -> "WirePacket":
        """Deserialize from dictionary."""
        return cls(
            msg_id=data["msg_id"],
            timestamp=data["timestamp"],
            expires_at=data["expires_at"],
            alert_type=data["alert_type"],
            body=data["body"],
            ttl=data["ttl"],
            signature_hex=data["signature_hex"],
            pubkey_hex=data["pubkey_hex"],
            origin_node=data.get("origin_node", ""),
        )


class AuthoritySigner:
    """Civil Defense Authority that signs alert packets.

    In production, this runs on an HSM or air-gapped signing station.
    The private key NEVER leaves the authority's secure environment.
    """

    def __init__(self, private_key: Optional[ed25519.Ed25519PrivateKey] = None):
        self._private_key = private_key or ed25519.Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        self.public_key_hex = self._public_key.public_bytes_raw().hex()

    def create_alert(
        self,
        alert_type: str,
        body: str,
        ttl: int = 15,
        validity_seconds: int = 3600,
        msg_id: Optional[str] = None,
        timestamp: Optional[int] = None,
    ) -> WirePacket:
        """Create and sign a new alert packet.

        Args:
            alert_type: Alert category string.
            body: Human-readable alert message.
            ttl: Initial hop count for civilian relay.
            validity_seconds: How long the alert remains valid.
            msg_id: Override message ID (auto-generated if None).
            timestamp: Override creation time (uses current time if None).

        Returns:
            A fully signed WirePacket ready for broadcast.
        """
        ts = timestamp or int(time.time())
        mid = msg_id or f"ALERT-{uuid.uuid4().hex[:12].upper()}"
        expires = ts + validity_seconds

        payload = AlertPayload(
            msg_id=mid,
            timestamp=ts,
            expires_at=expires,
            alert_type=alert_type,
            body=body,
        )
        signature = self._private_key.sign(payload.canonical_bytes()).hex()

        return WirePacket(
            msg_id=mid,
            timestamp=ts,
            expires_at=expires,
            alert_type=alert_type,
            body=body,
            ttl=ttl,
            signature_hex=signature,
            pubkey_hex=self.public_key_hex,
            origin_node="authority",
        )


def verify_packet(
    packet: WirePacket,
    trusted_pubkeys: Set[str],
    now_unix: Optional[float] = None,
) -> Tuple[bool, str]:
    """Verify a packet's cryptographic integrity and temporal validity.

    CRITICAL ORDERING: This function enforces the Strict Ordering Law.
    It MUST be called BEFORE any duplicate counter is incremented.

    Returns:
        (is_valid, reason) tuple. reason is "" on success, error string on failure.
    """
    now = now_unix if now_unix is not None else time.time()

    # 1. Temporal check: reject expired packets (anti-replay)
    if now > packet.expires_at:
        return False, "expired"

    # 2. Trust check: is this pubkey in our trust store?
    if packet.pubkey_hex not in trusted_pubkeys:
        return False, "untrusted_key"

    # 3. Cryptographic check: verify Ed25519 signature
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(packet.pubkey_hex)
        )
        canonical = packet.payload.canonical_bytes()
        public_key.verify(
            bytes.fromhex(packet.signature_hex),
            canonical,
        )
    except (InvalidSignature, ValueError, Exception) as e:
        return False, f"invalid_signature: {e}"

    return True, ""
