"""
WP 4.3 — Radio Budget Validation

Confirms:
  - Mean received power at 50m > -90 dBm (receiver sensitivity)
  - Path-loss calculation matches analytical formula
  - PHY gain values are correct
"""

import math
import pytest
from blackoutmesh.radio import (
    RadioParams,
    path_loss_db,
    mean_received_power_dbm,
    phy_gain_db,
    received_power_dbm,
)


class TestPathLoss:

    def test_path_loss_at_1m(self):
        """At 1m, path loss should equal reference (40 dB)."""
        params = RadioParams()
        pl = path_loss_db(1.0, params)
        assert pl == pytest.approx(40.0, abs=0.01)

    def test_path_loss_at_10m(self):
        """PL(10m) = 40 + 10 * 2.2 * log10(10) = 40 + 22 = 62 dB."""
        params = RadioParams()
        pl = path_loss_db(10.0, params)
        expected = 40.0 + 10.0 * 2.2 * math.log10(10.0)
        assert pl == pytest.approx(expected, abs=0.01)

    def test_path_loss_at_50m(self):
        """PL(50m) = 40 + 10 * 2.2 * log10(50) ~= 77.36 dB."""
        params = RadioParams()
        pl = path_loss_db(50.0, params)
        expected = 40.0 + 10.0 * 2.2 * math.log10(50.0)
        assert pl == pytest.approx(expected, abs=0.01)

    def test_minimum_distance_clamped(self):
        """Distances < 1m should be clamped to 1m."""
        params = RadioParams()
        assert path_loss_db(0.1, params) == path_loss_db(1.0, params)
        assert path_loss_db(0.0, params) == path_loss_db(1.0, params)


class TestReceivedPower:

    def test_mean_power_at_50m_above_sensitivity(self):
        """The mean received power at 50m MUST exceed -90 dBm.

        This is the critical radio budget validation from WP 4.3:
          P_rx = 4.0 + 0.0 + 0.0 - PL(50m) - 2.0
               = 4.0 - 77.36 - 2.0
               = -75.36 dBm
        Which is well above -90 dBm sensitivity.
        """
        params = RadioParams()
        mean_rx = mean_received_power_dbm(4.0, 50.0, params)
        assert mean_rx > -90.0, f"Mean received power {mean_rx:.1f} dBm is below -90 dBm!"

    def test_pessimistic_at_25m(self):
        """Pessimistic radio (n=3.0, body=6dB) at 25m should still be above -90 dBm."""
        params = RadioParams(path_loss_exp=3.0, body_loss_db=6.0, radio_range_m=25.0)
        mean_rx = mean_received_power_dbm(4.0, 25.0, params)
        assert mean_rx > -90.0

    def test_pessimistic_at_50m_may_fail(self):
        """Pessimistic radio at 50m: expected to be near or below sensitivity."""
        params = RadioParams(path_loss_exp=3.0, body_loss_db=6.0)
        mean_rx = mean_received_power_dbm(4.0, 50.0, params)
        # This validates WHY pessimistic range is 25m, not 50m
        # PL(50m, n=3) = 40 + 30*log10(50) ~= 90.9 dB
        # P_rx = 4 - 90.9 - 6 = -92.9 dBm -> BELOW sensitivity
        assert mean_rx < -90.0


class TestPhyGain:

    def test_1m_phy_no_gain(self):
        assert phy_gain_db("1M") == 0.0

    def test_coded_s2_gain(self):
        assert phy_gain_db("CODED_S2") == 6.0

    def test_coded_s8_gain(self):
        assert phy_gain_db("CODED_S8") == 9.0

    def test_unknown_phy_no_gain(self):
        assert phy_gain_db("UNKNOWN") == 0.0
