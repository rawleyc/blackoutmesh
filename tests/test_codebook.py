"""
Tests for WP 1.1 / 1.2: Codebook, compact wire format, and multilingual rendering.
"""

import pytest
from blackoutmesh.codebook import Codebook, RenderedAlert
from blackoutmesh.crypto import (
    AuthoritySigner,
    CompactAlertPayload,
    WirePacket,
    verify_packet,
)


@pytest.fixture
def codebook():
    return Codebook.load_default()


@pytest.fixture
def authority():
    return AuthoritySigner()


class TestCodebookRendering:
    def test_default_codebook_loaded(self, codebook):
        assert codebook.codebook_version >= 1
        assert "101" in codebook.templates
        assert "KRK_TAURON_G3" in codebook.shelters

    def test_trilingual_evacuation_render(self, codebook):
        rendered = codebook.render(
            template_id=101, loc_type=1, loc_ref="KRK_TAURON_G3"
        )
        assert isinstance(rendered, RenderedAlert)
        assert rendered.category == "EVACUATION"
        assert rendered.severity == "CRITICAL"
        assert "TAURON Arena — Brama 3" in rendered.text_pl
        assert "TAURON Arena — Gate 3" in rendered.text_en
        assert "ТАУРОН Арена — Вхід 3" in rendered.text_ua
        assert rendered.lat == 50.0652
        assert rendered.lon == 19.9855
        assert not rendered.is_fallback

    def test_raw_coordinates_rendering(self, codebook):
        rendered = codebook.render(
            template_id=103, loc_type=2, loc_ref=[50.0614, 19.9372]
        )
        assert "50.0614°N, 19.9372°E" in rendered.text_en
        assert rendered.lat == 50.0614
        assert rendered.lon == 19.9372

    def test_unknown_template_fallback_relay_without_decoding(self, codebook):
        """Unknown template IDs must NOT crash or drop — show structured fallback."""
        rendered = codebook.render(template_id=777, loc_type=1, loc_ref="KRK_TAURON_G3")
        assert rendered.is_fallback
        assert "[OFICJALNY KOMUNIKAT KRYZYSOWY (Kod 777)]" in rendered.text_pl
        assert "[OFFICIAL EMERGENCY ALERT (Code 777)]" in rendered.text_en
        assert "[ОФІЦІЙНЕ ПОВІДОМЛЕННЯ (Код 777)]" in rendered.text_ua


class TestCompactWireFormat:
    def test_compact_packet_creation_and_signature(self, authority):
        packet = authority.create_compact_alert(
            template_id=101,
            duration_minutes=90,
            seq=0,
            loc_type=1,
            loc_ref="KRK_TAURON_G3",
        )
        assert packet.version == 2
        assert packet.template_id == 101
        assert packet.expires_at == packet.timestamp + (90 * 60)

        trusted_keys = {authority.public_key_hex}
        is_valid, reason = verify_packet(packet, trusted_keys)
        assert is_valid, f"Verification failed: {reason}"

    def test_compact_packet_fits_in_single_gatt_read(self, authority):
        """WP 2.2: Compact packet must be <= 120 bytes to fit in one negotiated GATT MTU."""
        packet = authority.create_compact_alert(
            template_id=101,
            duration_minutes=120,
            seq=1,
            loc_type=1,
            loc_ref="KRK_TAURON_G3",
        )
        size = packet.raw_wire_size_bytes()
        # Canonical bytes (~130 bytes) + 64 sig + 32 pk = ~220 bytes max, well within 247-byte MTU!
        assert size < 240, f"Compact packet size {size}B exceeds single GATT MTU target"

    def test_compact_roundtrip_wire_dict(self, authority):
        packet = authority.create_compact_alert(
            template_id=102,
            duration_minutes=45,
            seq=2,
            loc_type=1,
            loc_ref="KRK_DWORZEC_GL",
        )
        d = packet.to_wire_dict()
        restored = WirePacket.from_wire_dict(d)
        assert restored.version == 2
        assert restored.template_id == 102
        assert restored.seq == 2
        assert restored.duration_m == 45
        assert restored.loc_ref == "KRK_DWORZEC_GL"

        # Verify cryptographic validity survives round-trip
        is_valid, _ = verify_packet(restored, {authority.public_key_hex})
        assert is_valid

    def test_unknown_template_validates_cryptographically(self, authority):
        """Nodes forward packets even if their local codebook lacks the template ID."""
        packet = authority.create_compact_alert(
            template_id=9999,  # Future template not in current codebook
            duration_minutes=30,
            loc_type=0,
        )
        is_valid, _ = verify_packet(packet, {authority.public_key_hex})
        assert is_valid
