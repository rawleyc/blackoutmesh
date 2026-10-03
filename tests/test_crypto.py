"""
Tests for WP 1.1 (Wire Format), WP 1.3 (Signed Expiry).
Verifies canonical serialization, signing, verification, and anti-replay.
"""

import time
import pytest
from blackoutmesh.crypto import (
    AlertPayload,
    WirePacket,
    AuthoritySigner,
    verify_packet,
)


@pytest.fixture
def authority():
    return AuthoritySigner()


@pytest.fixture
def valid_packet(authority):
    return authority.create_alert(
        alert_type="CIVIL_DEFENSE_EVAC",
        body="Water station at Gate 3.",
        ttl=15,
        validity_seconds=3600,
        timestamp=1791024000,
    )


class TestCanonicalSerialization:
    """WP 1.1: Verify deterministic JSON serialization."""

    def test_canonical_bytes_are_deterministic(self):
        """Same payload always produces identical bytes."""
        p1 = AlertPayload("id1", 1000, 2000, "EVAC", "test")
        p2 = AlertPayload("id1", 1000, 2000, "EVAC", "test")
        assert p1.canonical_bytes() == p2.canonical_bytes()

    def test_canonical_bytes_are_sorted_json(self):
        """Fields appear in alphabetical order with minimal separators."""
        p = AlertPayload("id1", 1000, 2000, "EVAC", "body")
        raw = p.canonical_bytes().decode("utf-8")
        # Keys must be sorted: alert_type, body, expires_at, msg_id, timestamp
        assert raw.index('"alert_type"') < raw.index('"body"')
        assert raw.index('"body"') < raw.index('"expires_at"')
        assert raw.index('"expires_at"') < raw.index('"msg_id"')
        assert raw.index('"msg_id"') < raw.index('"timestamp"')
        # No spaces
        assert " " not in raw

    def test_different_payloads_produce_different_bytes(self):
        p1 = AlertPayload("id1", 1000, 2000, "EVAC", "body1")
        p2 = AlertPayload("id1", 1000, 2000, "EVAC", "body2")
        assert p1.canonical_bytes() != p2.canonical_bytes()


class TestWirePacket:
    """WP 1.1: Wire format round-trip."""

    def test_roundtrip_serialization(self, valid_packet):
        wire_dict = valid_packet.to_wire_dict()
        restored = WirePacket.from_wire_dict(wire_dict)
        assert restored.msg_id == valid_packet.msg_id
        assert restored.timestamp == valid_packet.timestamp
        assert restored.expires_at == valid_packet.expires_at
        assert restored.signature_hex == valid_packet.signature_hex
        assert restored.ttl == valid_packet.ttl

    def test_payload_extraction(self, valid_packet):
        payload = valid_packet.payload
        assert payload.msg_id == valid_packet.msg_id
        assert payload.expires_at == valid_packet.expires_at


class TestSignatureVerification:
    """WP 1.3: Signature + expiry verification."""

    def test_valid_packet_passes(self, authority, valid_packet):
        trusted = {authority.public_key_hex}
        ok, reason = verify_packet(valid_packet, trusted, now_unix=1791024000 + 100)
        assert ok is True
        assert reason == ""

    def test_expired_packet_rejected(self, authority, valid_packet):
        """Anti-replay: packet past expires_at is dropped."""
        trusted = {authority.public_key_hex}
        future = valid_packet.expires_at + 10
        ok, reason = verify_packet(valid_packet, trusted, now_unix=future)
        assert ok is False
        assert reason == "expired"

    def test_untrusted_key_rejected(self, valid_packet):
        """Packet from unknown authority is dropped."""
        ok, reason = verify_packet(valid_packet, set(), now_unix=1791024000 + 100)
        assert ok is False
        assert reason == "untrusted_key"

    def test_tampered_body_rejected(self, authority, valid_packet):
        """Modified body invalidates the signature."""
        trusted = {authority.public_key_hex}
        tampered = WirePacket(
            msg_id=valid_packet.msg_id,
            timestamp=valid_packet.timestamp,
            expires_at=valid_packet.expires_at,
            alert_type=valid_packet.alert_type,
            body="TAMPERED MESSAGE",
            ttl=valid_packet.ttl,
            signature_hex=valid_packet.signature_hex,
            pubkey_hex=valid_packet.pubkey_hex,
        )
        ok, reason = verify_packet(tampered, trusted, now_unix=1791024000 + 100)
        assert ok is False
        assert "invalid_signature" in reason

    def test_tampered_signature_rejected(self, authority, valid_packet):
        """Corrupted signature bytes are rejected."""
        trusted = {authority.public_key_hex}
        bad_sig = "00" * 64  # 64 bytes of zeros
        tampered = WirePacket(
            msg_id=valid_packet.msg_id,
            timestamp=valid_packet.timestamp,
            expires_at=valid_packet.expires_at,
            alert_type=valid_packet.alert_type,
            body=valid_packet.body,
            ttl=valid_packet.ttl,
            signature_hex=bad_sig,
            pubkey_hex=valid_packet.pubkey_hex,
        )
        ok, reason = verify_packet(tampered, trusted, now_unix=1791024000 + 100)
        assert ok is False

    def test_ttl_change_does_not_invalidate(self, authority, valid_packet):
        """TTL is mutable and NOT signed — changing it must not break verification."""
        trusted = {authority.public_key_hex}
        modified = WirePacket(
            msg_id=valid_packet.msg_id,
            timestamp=valid_packet.timestamp,
            expires_at=valid_packet.expires_at,
            alert_type=valid_packet.alert_type,
            body=valid_packet.body,
            ttl=1,
            signature_hex=valid_packet.signature_hex,
            pubkey_hex=valid_packet.pubkey_hex,
        )
        ok, reason = verify_packet(modified, trusted, now_unix=1791024000 + 100)
        assert ok is True
