"""
Radio link-budget model for BLE 5.x advertisement channels.

Extracted from simulation.py for reuse in unit tests (WP 4.3).
This is a SIMPLIFIED stochastic model for comparative simulation,
NOT a substitute for on-device measurements.

Path-loss model:
  PL(d) = ref_PL + 10 * n * log10(d) + body_loss + N(0, sigma_shadow)

Where:
  ref_PL    = 40.0 dB (free-space at 1m, ~2.4 GHz)
  n         = path-loss exponent (2.2 baseline, 3.0 pessimistic)
  body_loss = human body attenuation (2 dB baseline, 6 dB pessimistic)
  sigma_shadow  = log-normal shadowing std dev (4.0 dB)
"""

import math
import random
from dataclasses import dataclass


@dataclass
class RadioParams:
    """Radio environment parameters."""
    radio_range_m: float = 50.0
    tx_power_dbm: float = 4.0
    rx_sensitivity_dbm: float = -90.0
    antenna_gain_dbi: float = 0.0
    body_loss_db: float = 2.0
    path_loss_exp: float = 2.2
    shadowing_std_db: float = 4.0
    ref_path_loss_db: float = 40.0
    snr_transition_db: float = 4.0
    phy: str = "1M"
    scan_interval_s: float = 1.0
    scan_window_s: float = 0.25
    base_adv_success: float = 0.95


def phy_gain_db(phy: str) -> float:
    """Approximate coding gain for BLE PHY modes."""
    return {"CODED_S2": 6.0, "CODED_S8": 9.0}.get(phy, 0.0)


def path_loss_db(distance_m: float, params: RadioParams) -> float:
    """Log-distance path loss in dB."""
    d = max(distance_m, 1.0)
    return params.ref_path_loss_db + 10.0 * params.path_loss_exp * math.log10(d)


def received_power_dbm(
    tx_power_dbm: float, distance_m: float, params: RadioParams
) -> float:
    """Compute received power with shadowing (stochastic)."""
    shadowing = random.gauss(0.0, params.shadowing_std_db)
    return (
        tx_power_dbm
        + params.antenna_gain_dbi
        + phy_gain_db(params.phy)
        - path_loss_db(distance_m, params)
        - params.body_loss_db
        + shadowing
    )


def reception_probability(
    rx_power_dbm: float, rx_sensitivity_dbm: float, params: RadioParams
) -> float:
    """Sigmoid reception probability around receiver sensitivity threshold."""
    margin = rx_power_dbm - rx_sensitivity_dbm
    x = -margin / params.snr_transition_db
    if x > 50.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(x))


def mean_received_power_dbm(
    tx_power_dbm: float, distance_m: float, params: RadioParams
) -> float:
    """Deterministic (no shadowing) mean received power for validation."""
    d = max(distance_m, 1.0)
    pl = params.ref_path_loss_db + 10.0 * params.path_loss_exp * math.log10(d)
    return (
        tx_power_dbm
        + params.antenna_gain_dbi
        + phy_gain_db(params.phy)
        - pl
        - params.body_loss_db
    )
