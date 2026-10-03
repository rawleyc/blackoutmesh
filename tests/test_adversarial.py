"""
WP 4.1 — Forgery Injection Test (Pre-Suppression Immunity)
WP 4.2 — Stale Packet Replay Attack Test

These tests validate the protocol's resistance to active adversaries.
"""

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from blackoutmesh.crypto import AuthoritySigner, WirePacket, verify_packet
from blackoutmesh.node import MeshNode
from blackoutmesh.trickle import TrickleConfig


@pytest.fixture
def authority():
    return AuthoritySigner()


@pytest.fixture
def evil_signer():
    """An adversary with their own (untrusted) signing key."""
    return AuthoritySigner()


@pytest.fixture
def trusted_keys(authority):
    return {authority.public_key_hex}


class TestForgeryInjection:
    """WP 4.1: 50 forged packets must not suppress legitimate alerts."""

    def test_50_forgeries_dont_suppress(self, authority, evil_signer, trusted_keys):
        node = MeshNode("victim", trusted_keys, TrickleConfig(k=3))

        # Inject legitimate alert
        real = authority.create_alert("EVAC", "Real alert", timestamp=1791024000)
        assert node.receive(real, 1791024000 + 1) == "accepted"

        # Adversary sends 50 packets signed with wrong key
        for i in range(50):
            forgery = evil_signer.create_alert(
                "EVAC",
                f"Fake alert {i}",
                msg_id=f"FAKE-{i:03d}",
                timestamp=1791024000,
            )
            result = node.receive(forgery, 1791024000 + 2 + i)
            assert result == "invalid"

        # Verify damage assessment
        assert node.stats.invalid_packets == 50
        assert node.stats.valid_receptions == 1
        # The critical check: Trickle was NOT affected by forgeries
        assert node.trickle.counter == 0  # No valid duplicates were heard
        assert node.stats.duplicate_receptions == 0

    def test_forgery_with_same_msgid_as_real(self, authority, trusted_keys):
        """Adversary reuses msg_id of real alert with bad signature."""
        node = MeshNode("victim", trusted_keys, TrickleConfig(k=3))

        real = authority.create_alert("EVAC", "Real alert", timestamp=1791024000)
        node.receive(real, 1791024000 + 1)

        # Forge packet with same msg_id but bad signature
        forgery = WirePacket(
            msg_id=real.msg_id,
            timestamp=real.timestamp,
            expires_at=real.expires_at,
            alert_type=real.alert_type,
            body=real.body,
            ttl=15,
            signature_hex="ab" * 64,
            pubkey_hex=authority.public_key_hex,
        )
        result = node.receive(forgery, 1791024000 + 2)
        # Must be caught at signature verification, NOT at dedup
        assert result == "invalid"
        assert node.trickle.counter == 0


class TestReplayAttack:
    """WP 4.2: Expired packets must be instantly rejected."""

    def test_stale_packet_rejected(self, authority, trusted_keys):
        """Packet with expires_at = now - 10s is dropped immediately."""
        node = MeshNode("victim", trusted_keys)

        pkt = authority.create_alert(
            "EVAC",
            "Old alert",
            timestamp=1791024000,
            validity_seconds=100,
        )
        # Try to deliver 110 seconds later (10s past expiry)
        now = 1791024000 + 110
        result = node.receive(pkt, now)
        assert result == "expired"
        assert node.stats.expired_packets == 1
        assert node.stats.valid_receptions == 0

    def test_packet_at_exact_expiry_boundary(self, authority, trusted_keys):
        """Packet at exactly expires_at should still pass (now <= expires_at)."""
        node = MeshNode("victim", trusted_keys)

        pkt = authority.create_alert(
            "EVAC",
            "Edge case alert",
            timestamp=1791024000,
            validity_seconds=100,
        )
        # Deliver at exactly expires_at
        result = node.receive(pkt, pkt.expires_at)
        assert result == "accepted"

    def test_replay_one_second_after_expiry(self, authority, trusted_keys):
        """One second after expiry -> rejected."""
        node = MeshNode("victim", trusted_keys)

        pkt = authority.create_alert(
            "EVAC",
            "Replay attempt",
            timestamp=1791024000,
            validity_seconds=100,
        )
        result = node.receive(pkt, pkt.expires_at + 1)
        assert result == "expired"
