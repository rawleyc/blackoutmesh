"""
Tests for WP 2.4: Packet storage and expiry garbage collection.
"""

import pytest
from blackoutmesh.crypto import AuthoritySigner, WirePacket
from blackoutmesh.storage import PacketStore


@pytest.fixture
def store():
    return PacketStore()


@pytest.fixture
def authority():
    return AuthoritySigner()


def make_packet(authority, msg_id="test-001", expires_at=2000000000, ttl=15):
    pkt = authority.create_alert(
        alert_type="TEST",
        body="Test alert",
        ttl=ttl,
        msg_id=msg_id,
        timestamp=1791024000,
    )
    # Override expires_at for testing
    pkt.expires_at = expires_at
    return pkt


class TestPacketStore:

    def test_store_new_packet(self, store, authority):
        pkt = make_packet(authority)
        assert store.store(pkt) is True
        assert len(store) == 1

    def test_duplicate_rejected(self, store, authority):
        pkt = make_packet(authority)
        store.store(pkt)
        assert store.store(pkt) is False
        assert len(store) == 1

    def test_contains(self, store, authority):
        pkt = make_packet(authority)
        assert store.contains("test-001") is False
        store.store(pkt)
        assert store.contains("test-001") is True

    def test_get(self, store, authority):
        pkt = make_packet(authority)
        store.store(pkt)
        retrieved = store.get("test-001")
        assert retrieved is not None
        assert retrieved.msg_id == "test-001"

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None


class TestGarbageCollection:

    def test_gc_removes_expired(self, store, authority):
        pkt = make_packet(authority, expires_at=1000)
        store.store(pkt)
        assert len(store) == 1

        purged = store.gc_sweep(now_unix=1001)
        assert purged == 1
        assert len(store) == 0

    def test_gc_keeps_valid(self, store, authority):
        pkt = make_packet(authority, expires_at=2000)
        store.store(pkt)

        purged = store.gc_sweep(now_unix=1500)
        assert purged == 0
        assert len(store) == 1

    def test_gc_mixed(self, store, authority):
        store.store(make_packet(authority, msg_id="old", expires_at=1000))
        store.store(make_packet(authority, msg_id="new", expires_at=3000))

        purged = store.gc_sweep(now_unix=1500)
        assert purged == 1
        assert store.contains("new") is True
        assert store.contains("old") is False

    def test_gc_counter(self, store, authority):
        store.store(make_packet(authority, msg_id="a", expires_at=100))
        store.store(make_packet(authority, msg_id="b", expires_at=200))
        store.gc_sweep(now_unix=300)
        assert store.total_gc_purged == 2
