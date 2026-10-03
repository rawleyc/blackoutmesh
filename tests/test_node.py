"""
Tests for WP 2.1-2.3: Node receive pipeline and neighbor churn.
"""

import pytest
from blackoutmesh.crypto import AuthoritySigner, WirePacket
from blackoutmesh.node import MeshNode
from blackoutmesh.trickle import TrickleConfig


@pytest.fixture
def authority():
    return AuthoritySigner()


@pytest.fixture
def trusted_keys(authority):
    return {authority.public_key_hex}


@pytest.fixture
def node(trusted_keys):
    return MeshNode(
        node_id="test_phone",
        trusted_pubkeys=trusted_keys,
        trickle_config=TrickleConfig(i_min=1.0, i_max=16.0, k=3),
    )


@pytest.fixture
def valid_packet(authority):
    return authority.create_alert(
        alert_type="EVAC",
        body="Test alert",
        ttl=15,
        timestamp=1791024000,
        validity_seconds=3600,
    )


class TestStrictOrderingLaw:
    """WP 2.1: Verify that crypto check happens BEFORE duplicate counting."""

    def test_valid_new_packet_accepted(self, node, valid_packet):
        result = node.receive(valid_packet, now_unix=1791024000 + 10)
        assert result == "accepted"
        assert node.stats.valid_receptions == 1

    def test_valid_duplicate_counted(self, node, valid_packet):
        node.receive(valid_packet, now_unix=1791024000 + 10)
        result = node.receive(valid_packet, now_unix=1791024000 + 20)
        assert result == "duplicate"
        assert node.stats.duplicate_receptions == 1
        # Trickle counter should be incremented
        assert node.trickle.counter == 1

    def test_invalid_packet_does_not_affect_trickle(self, node, authority):
        """Critical: forged packets must NOT increment heard counter."""
        # First, receive a valid packet
        valid = authority.create_alert("EVAC", "Real", timestamp=1791024000)
        node.receive(valid, now_unix=1791024000 + 10)
        initial_counter = node.trickle.counter

        # Send 50 forged packets with the same msg_id
        for i in range(50):
            forged = WirePacket(
                msg_id=valid.msg_id,
                timestamp=1791024000,
                expires_at=1791024000 + 3600,
                alert_type="EVAC",
                body="Real",
                ttl=15,
                signature_hex="00" * 64,
                pubkey_hex=authority.public_key_hex,
            )
            result = node.receive(forged, now_unix=1791024000 + 10 + i)
            assert result == "invalid"

        # Trickle counter must NOT have been incremented by forgeries
        assert node.trickle.counter == initial_counter
        assert node.stats.invalid_packets == 50

    def test_expired_packet_rejected_before_dedup(self, node, valid_packet):
        result = node.receive(valid_packet, now_unix=valid_packet.expires_at + 10)
        assert result == "expired"
        assert node.stats.expired_packets == 1
        assert node.stats.valid_receptions == 0


class TestNeighborChurn:
    """WP 2.3: Neighbor churn detection triggers Trickle reset."""

    def test_new_neighbors_trigger_reset(self, node, valid_packet):
        # Give the node a packet to carry
        node.receive(valid_packet, now_unix=1791024000 + 10)

        # First scan: nodes A, B
        changed = node.update_neighbors({"A", "B"}, now_sim=10.0)
        # First call has no previous, so all are new
        assert changed is True

    def test_no_change_no_reset(self, node, valid_packet):
        node.receive(valid_packet, now_unix=1791024000 + 10)
        node.update_neighbors({"A", "B"}, now_sim=10.0)
        # Same neighbors again
        changed = node.update_neighbors({"A", "B"}, now_sim=11.0)
        assert changed is False

    def test_churn_with_one_new(self, node, valid_packet):
        node.receive(valid_packet, now_unix=1791024000 + 10)
        node.update_neighbors({"A", "B"}, now_sim=10.0)
        # C is new
        changed = node.update_neighbors({"A", "C"}, now_sim=11.0)
        assert changed is True
