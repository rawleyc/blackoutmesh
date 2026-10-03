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
from typing import Any, Optional, Set, Tuple

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature


@dataclass(frozen=True)
class AlertPayload:
    """V1 signed fields of an alert packet (free-text)."""
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


@dataclass(frozen=True)
class CompactAlertPayload:
    """V2 Compact Signed Payload using Codebook Templates (~28-35 bytes).

    Fields:
      v: Protocol version (2)
      cb_v: Required codebook version (1)
      msg_id: Short hex identifier (e.g. "A7F29B01")
      seq: Monotonic update sequence (0=new, 1..254=updates, 255=all-clear)
      timestamp: Unix epoch seconds
      duration_m: Validity window in minutes (ts + duration_m*60 = expiry)
      template_id: Codebook template ID (e.g. 101=Evac, 102=Air Raid)
      loc_type: 0=None, 1=Preset Shelter ID, 2=Raw [lat,lon]
      loc_ref: Shelter ID string or [lat, lon] tuple
      custom_text: Optional string for escape hatch template (max 60 chars)
    """
    msg_id: str
    timestamp: int
    duration_m: int
    template_id: int
    seq: int = 0
    v: int = 2
    cb_v: int = 1
    loc_type: int = 0
    loc_ref: Any = ""
    custom_text: str = ""

    @property
    def expires_at(self) -> int:
        return self.timestamp + (self.duration_m * 60)

    def canonical_bytes(self) -> bytes:
        """Deterministic canonical JSON serialization for compact packets."""
        payload = {
            "cb_v": self.cb_v,
            "custom_text": self.custom_text,
            "dur": self.duration_m,
            "id": self.msg_id,
            "loc_ref": self.loc_ref,
            "loc_type": self.loc_type,
            "seq": self.seq,
            "tid": self.template_id,
            "ts": self.timestamp,
            "v": self.v,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")


@dataclass
class WirePacket:
    """Complete over-the-air packet: signed payload + mutable metadata."""
    msg_id: str
    timestamp: int
    expires_at: int
    ttl: int
    signature_hex: str
    pubkey_hex: str
    version: int = 1
    origin_node: str = ""

    # V1 legacy fields
    alert_type: str = ""
    body: str = ""

    # V2 compact fields
    template_id: int = 0
    seq: int = 0
    duration_m: int = 60
    loc_type: int = 0
    loc_ref: Any = ""
    custom_text: str = ""
    cb_v: int = 1

    @property
    def payload(self):
        if self.version == 2:
            return CompactAlertPayload(
                msg_id=self.msg_id,
                timestamp=self.timestamp,
                duration_m=self.duration_m,
                template_id=self.template_id,
                seq=self.seq,
                v=self.version,
                cb_v=self.cb_v,
                loc_type=self.loc_type,
                loc_ref=self.loc_ref,
                custom_text=self.custom_text,
            )
        return AlertPayload(
            msg_id=self.msg_id,
            timestamp=self.timestamp,
            expires_at=self.expires_at,
            alert_type=self.alert_type,
            body=self.body,
        )

    def raw_wire_size_bytes(self) -> int:
        """Estimated serialized size in bytes over GATT read."""
        canonical_len = len(self.payload.canonical_bytes())
        # Canonical bytes + 64 bytes sig + 32 bytes pubkey + 1 byte TTL
        return canonical_len + 64 + 32 + 1

    def to_wire_dict(self) -> dict:
        """Serialize to dictionary for transmission."""
        d = {
            "v": self.version,
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "expires_at": self.expires_at,
            "ttl": self.ttl,
            "signature_hex": self.signature_hex,
            "pubkey_hex": self.pubkey_hex,
            "origin_node": self.origin_node,
        }
        if self.version == 2:
            d.update({
                "cb_v": self.cb_v,
                "tid": self.template_id,
                "seq": self.seq,
                "dur": self.duration_m,
                "loc_type": self.loc_type,
                "loc_ref": self.loc_ref,
                "custom_text": self.custom_text,
            })
        else:
            d.update({
                "alert_type": self.alert_type,
                "body": self.body,
            })
        return d

    @classmethod
    def from_wire_dict(cls, data: dict) -> "WirePacket":
        """Deserialize from dictionary."""
        version = data.get("v", 1)
        if version == 2:
            ts = data["timestamp"]
            dur = data.get("dur", 60)
            expires_at = data.get("expires_at", ts + dur * 60)
            return cls(
                version=2,
                msg_id=data["msg_id"],
                timestamp=ts,
                expires_at=expires_at,
                ttl=data.get("ttl", 15),
                signature_hex=data["signature_hex"],
                pubkey_hex=data["pubkey_hex"],
                origin_node=data.get("origin_node", ""),
                template_id=data.get("tid", 101),
                seq=data.get("seq", 0),
                duration_m=dur,
                loc_type=data.get("loc_type", 0),
                loc_ref=data.get("loc_ref", ""),
                custom_text=data.get("custom_text", ""),
                cb_v=data.get("cb_v", 1),
            )
        return cls(
            version=1,
            msg_id=data["msg_id"],
            timestamp=data["timestamp"],
            expires_at=data["expires_at"],
            alert_type=data.get("alert_type", ""),
            body=data.get("body", ""),
            ttl=data.get("ttl", 15),
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

    def create_compact_alert(
        self,
        template_id: int,
        duration_minutes: int = 60,
        seq: int = 0,
        loc_type: int = 0,
        loc_ref: Any = "",
        custom_text: str = "",
        ttl: int = 15,
        msg_id: Optional[str] = None,
        timestamp: Optional[int] = None,
        cb_v: int = 1,
    ) -> WirePacket:
        """Create and sign a compact templated alert packet (V2).

        Args:
            template_id: Codebook template ID (e.g. 101, 102).
            duration_minutes: Validity window in minutes.
            seq: Monotonic sequence number (0=initial, 1..254=updates, 255=all-clear).
            loc_type: 0=None, 1=Preset Shelter ID, 2=Raw coordinates.
            loc_ref: Shelter code string or [lat, lon] tuple.
            custom_text: Optional custom text for escape hatch (TID 999).
            ttl: Initial civilian hop count.
            msg_id: Override message ID (auto-generated if None).
            timestamp: Override creation time.
            cb_v: Required codebook version.

        Returns:
            Fully signed WirePacket with CompactAlertPayload (~94 bytes).
        """
        ts = timestamp or int(time.time())
        mid = msg_id or f"A{uuid.uuid4().hex[:7].upper()}"
        expires = ts + (duration_minutes * 60)

        payload = CompactAlertPayload(
            v=2,
            cb_v=cb_v,
            msg_id=mid,
            seq=seq,
            timestamp=ts,
            duration_m=duration_minutes,
            template_id=template_id,
            loc_type=loc_type,
            loc_ref=loc_ref,
            custom_text=custom_text[:60] if custom_text else "",
        )
        sig = self._private_key.sign(payload.canonical_bytes()).hex()

        return WirePacket(
            version=2,
            msg_id=mid,
            timestamp=ts,
            expires_at=expires,
            ttl=ttl,
            signature_hex=sig,
            pubkey_hex=self.public_key_hex,
            origin_node="authority",
            template_id=template_id,
            seq=seq,
            duration_m=duration_minutes,
            loc_type=loc_type,
            loc_ref=loc_ref,
            custom_text=custom_text[:60] if custom_text else "",
            cb_v=cb_v,
        )

    def create_alert(
        self,
        alert_type: str,
        body: str,
        ttl: int = 15,
        validity_seconds: int = 3600,
        msg_id: Optional[str] = None,
        timestamp: Optional[int] = None,
    ) -> WirePacket:
        """Create and sign a legacy free-text alert packet (V1)."""
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
            version=1,
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
