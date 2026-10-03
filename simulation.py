"""
DTN emergency alert propagation over BLE, Krakow / TAURON Arena vicinity.

Changes versus the original script:
  1. Alert lifetime (3600 s) now exceeds the simulation, and expiry is part of
     the SIGNED payload (expires_at), so it cannot be extended or replayed.
  2. Simulation step = advertisement interval (1 s). The old 10 s step allowed
     only one attempt per contact where real BLE makes about ten.
  3. Transmission is a BROADCAST: one advert reaches every neighbour in range
     (the old cooldown let each node serve only one neighbour per step).
  4. Forwarding uses Trickle-style suppression (interval doubling, counter K)
     and resets to fast advertising whenever a new neighbour appears.
  5. Positions are interpolated along street edges (no snapping to vertices),
     and contacts use a KD-tree instead of an O(n^2) pandas loop.
  6. Origin node bug fixed: nearest_nodes was called on the PROJECTED graph
     with lon/lat, which picked a node at the corner of the map.
  7. Several redundant seed devices instead of a single point of failure.
  8. Signature is verified BEFORE a copy is counted as a duplicate, so a
     forged packet cannot suppress forwarding of the real alert.
  9. Metrics fixed (suppression counted, duplicates are totals, TTL 15).
 10. Monte Carlo harness: "the network does not fail" is now a measurable
     success rate under optimistic and pessimistic radio conditions.
"""

import json
import math
import random
from dataclasses import dataclass, replace
from typing import Dict, List, Set, Tuple

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import osmnx as ox
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519
from scipy.spatial import cKDTree


# ============================================================
# CONFIGURATION
# ============================================================

@dataclass
class Params:
    # Geography
    center_point: Tuple[float, float] = (50.0647, 19.9650)
    network_radius_m: float = 1500.0
    initial_crowd_radius_m: float = 600.0

    # Population
    num_civilians: int = 75
    num_seeds: int = 3                  # redundant injection devices
    relay_probability: float = 0.90
    stationary_probability: float = 0.10

    # Clock (dt equals the advertisement interval)
    duration_s: int = 900
    dt_s: float = 1.0

    # Mobility
    min_speed_mps: float = 0.8
    max_speed_mps: float = 1.6

    # Radio
    radio_range_m: float = 50.0         # max modeled distance, not a guarantee
    phy: str = "1M"                     # "1M", "CODED_S2", "CODED_S8"
    tx_power_dbm_mean: float = 4.0
    tx_power_dbm_std: float = 1.5
    rx_sensitivity_dbm: float = -90.0
    antenna_gain_dbi: float = 0.0
    body_loss_db: float = 2.0
    path_loss_exp: float = 2.2
    shadowing_std_db: float = 4.0
    ref_path_loss_db: float = 40.0      # at 1 m, about 2.4 GHz
    snr_transition_db: float = 4.0

    # Advertising / scanning abstraction
    scan_interval_s: float = 1.0
    scan_window_s: float = 0.25
    base_adv_success: float = 0.95

    # Packet
    initial_ttl: int = 15
    max_packet_age_s: int = 3600        # signed validity window
    alert_type: str = "CIVIL_DEFENSE_EVAC"
    alert_body: str = "Grid failure. Water station active at TAURON Arena Gate 3."

    # Trickle forwarding
    trickle_imin_s: float = 1.0
    trickle_imax_s: float = 16.0
    trickle_k: int = 3


BASE = Params()

# Harsher radio: shorter range, more clutter, more body loss
PESSIMISTIC = replace(
    BASE,
    radio_range_m=25.0,
    path_loss_exp=3.0,
    body_loss_db=6.0,
)

ALERT_TIMESTAMP_UNIX = 1791024000

MONTE_CARLO_RUNS = 20
DENSITY_SWEEP = [75, 150, 300, 600, 1200]   # civilians within the crowd radius
SWEEP_RUNS = 8
MC_TARGET_FRACTION = 0.90               # "success" = 90% of civilians reached
MC_DEADLINE_S = 600                     # ... within this many seconds


# ============================================================
# CRYPTOGRAPHIC TRUST AUTHORITY
# ============================================================

class CivilDefenseAuthority:

    def __init__(self):
        self.private_key = ed25519.Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.public_key_hex = self.public_key.public_bytes_raw().hex()

    @staticmethod
    def canonical_payload(
        msg_id: str,
        timestamp: int,
        expires_at: int,
        alert_type: str,
        body: str,
    ) -> bytes:
        payload = {
            "msg_id": msg_id,
            "timestamp": timestamp,
            "expires_at": expires_at,
            "alert_type": alert_type,
            "body": body,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def sign_alert(
        self,
        msg_id: str,
        timestamp: int,
        expires_at: int,
        alert_type: str,
        body: str,
    ) -> str:
        canonical = self.canonical_payload(
            msg_id, timestamp, expires_at, alert_type, body
        )
        return self.private_key.sign(canonical).hex()


# ============================================================
# PACKET MODEL
# ============================================================

@dataclass
class Packet:
    # Signed fields
    msg_id: str
    timestamp: int
    expires_at: int
    alert_type: str
    body: str

    # Unsigned, mutable in transit
    ttl: int

    signature_hex: str
    pubkey_hex: str
    origin_node: str


# ============================================================
# RADIO PROFILE
# ============================================================

@dataclass
class RadioProfile:
    tx_power_dbm: float
    rx_sensitivity_dbm: float
    phy: str
    scan_interval_s: float
    scan_window_s: float
    relay_enabled: bool


def make_radio_profile(p: Params) -> RadioProfile:
    return RadioProfile(
        tx_power_dbm=random.gauss(p.tx_power_dbm_mean, p.tx_power_dbm_std),
        rx_sensitivity_dbm=p.rx_sensitivity_dbm,
        phy=p.phy,
        scan_interval_s=p.scan_interval_s,
        scan_window_s=p.scan_window_s,
        relay_enabled=(random.random() < p.relay_probability),
    )


# ============================================================
# RADIO MODEL
# ============================================================

def phy_gain_db(phy: str) -> float:
    """Approximate simulation margin, NOT Bluetooth SIG link-budget values."""
    if phy == "CODED_S2":
        return 6.0
    if phy == "CODED_S8":
        return 9.0
    return 0.0


def path_loss_db(distance_m: float, p: Params) -> float:
    d = max(distance_m, 1.0)
    return p.ref_path_loss_db + 10.0 * p.path_loss_exp * math.log10(d)


def received_power_dbm(tx: RadioProfile, distance_m: float, p: Params) -> float:
    shadowing = random.gauss(0.0, p.shadowing_std_db)
    return (
        tx.tx_power_dbm
        + p.antenna_gain_dbi
        + phy_gain_db(tx.phy)
        - path_loss_db(distance_m, p)
        - p.body_loss_db
        + shadowing
    )


def reception_probability(rx_power_dbm: float, rx: RadioProfile, p: Params) -> float:
    """Smooth transition around receiver sensitivity."""
    margin = rx_power_dbm - rx.rx_sensitivity_dbm
    x = -margin / p.snr_transition_db
    if x > 50.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(x))


def ble_opportunity_probability(rx: RadioProfile, p: Params) -> float:
    """Probability that one advert lands inside a scan window."""
    duty = rx.scan_window_s / rx.scan_interval_s
    duty = max(0.0, min(1.0, duty))
    return duty * p.base_adv_success


def packet_delivery_probability(
    tx: RadioProfile, rx: RadioProfile, distance_m: float, p: Params
) -> float:
    if distance_m > p.radio_range_m:
        return 0.0
    rx_power = received_power_dbm(tx, distance_m, p)
    return reception_probability(rx_power, rx, p) * ble_opportunity_probability(rx, p)


# ============================================================
# WORLD (loaded once, reused by every run)
# ============================================================

@dataclass
class World:
    G: nx.MultiDiGraph
    nodes_gdf: object
    edges_gdf: object
    coords: Dict[int, Tuple[float, float]]
    node_ids: np.ndarray
    node_xy: np.ndarray
    origin_node: int
    origin_xy: Tuple[float, float]


def build_world(p: Params) -> World:
    print("[1/4] Fetching pedestrian network...")
    g_raw = ox.graph_from_point(
        p.center_point, dist=p.network_radius_m, network_type="walk"
    )

    # FIX: find the origin on the UNPROJECTED graph (lon/lat), before projecting.
    origin_node = int(
        ox.distance.nearest_nodes(
            g_raw, X=p.center_point[1], Y=p.center_point[0]
        )
    )

    G = ox.project_graph(g_raw)
    nodes_gdf, edges_gdf = ox.graph_to_gdfs(G)

    node_ids = np.array(nodes_gdf.index)
    node_xy = np.column_stack(
        [nodes_gdf.geometry.x.values, nodes_gdf.geometry.y.values]
    )
    coords = {
        int(n): (float(x), float(y))
        for n, (x, y) in zip(node_ids, node_xy)
    }
    origin_xy = coords[origin_node]

    print(
        f"Network loaded: {len(nodes_gdf):,} nodes, {len(edges_gdf):,} edges. "
        f"Origin OSM node: {origin_node}"
    )
    return World(
        G=G,
        nodes_gdf=nodes_gdf,
        edges_gdf=edges_gdf,
        coords=coords,
        node_ids=node_ids,
        node_xy=node_xy,
        origin_node=origin_node,
        origin_xy=origin_xy,
    )


def candidates_near_origin(world: World, radius_m: float) -> List[int]:
    d = np.hypot(
        world.node_xy[:, 0] - world.origin_xy[0],
        world.node_xy[:, 1] - world.origin_xy[1],
    )
    return [int(n) for n in world.node_ids[d <= radius_m]]


# ============================================================
# CIVILIAN DEVICE
# ============================================================

class CivilianNode:

    def __init__(
        self,
        node_id: str,
        start_osm_node: int,
        p: Params,
        trusted_pubkeys: Set[str],
        is_seed: bool = False,
    ):
        self.node_id = node_id
        self.is_seed = is_seed
        self.p = p

        # Mobility
        self.current_osm = start_osm_node
        self.prev_osm = None
        self.target_osm = None
        self.edge_length = 0.0
        self.remaining_edge_distance = 0.0
        self.speed_mps = random.uniform(p.min_speed_mps, p.max_speed_mps)
        self.stationary = is_seed or (random.random() < p.stationary_probability)

        # Radio
        self.radio = make_radio_profile(p)
        if is_seed:
            self.radio.relay_enabled = True

        self.trusted_pubkeys = trusted_pubkeys

        # Packet state
        self.received_alerts: Dict[str, Packet] = {}
        self.prev_neighbors: Set[int] = set()

        # Trickle state
        self.trickle_interval = p.trickle_imin_s
        self.trickle_start = 0.0
        self.trickle_t = 0.0
        self.trickle_fired = False
        self.heard = 0

        # Statistics
        self.transmissions_sent = 0
        self.successful_receptions = 0
        self.duplicate_receptions = 0
        self.invalid_packets = 0
        self.expired_packets = 0
        self.forwarding_suppressed = 0

    # --------------------------------------------------------
    # Cryptographic verification (signature + trust + expiry)
    # --------------------------------------------------------

    def verify(self, packet: Packet, now_unix: float) -> bool:
        if now_unix > packet.expires_at:
            return False
        if packet.pubkey_hex not in self.trusted_pubkeys:
            return False
        try:
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(packet.pubkey_hex)
            )
            canonical = CivilDefenseAuthority.canonical_payload(
                packet.msg_id,
                packet.timestamp,
                packet.expires_at,
                packet.alert_type,
                packet.body,
            )
            public_key.verify(bytes.fromhex(packet.signature_hex), canonical)
            return True
        except (InvalidSignature, ValueError):
            return False

    # --------------------------------------------------------
    # Mobility (random walk on street graph, no immediate backtracking)
    # --------------------------------------------------------

    def choose_next_edge(self, graph: nx.MultiDiGraph):
        neighbors = list(graph.neighbors(self.current_osm))
        if not neighbors:
            self.target_osm = None
            return

        options = [n for n in neighbors if n != self.prev_osm] or neighbors
        self.target_osm = random.choice(options)

        edge_data = graph.get_edge_data(self.current_osm, self.target_osm)
        if edge_data is None:
            self.target_osm = None
            return

        edge = next(iter(edge_data.values()))
        self.edge_length = max(float(edge.get("length", 5.0)), 0.1)
        self.remaining_edge_distance = self.edge_length

    def move(self, graph: nx.MultiDiGraph, dt_s: float):
        if self.stationary:
            return

        distance_to_move = self.speed_mps * dt_s

        while distance_to_move > 0:
            if self.target_osm is None:
                self.choose_next_edge(graph)
                if self.target_osm is None:
                    return

            step_distance = min(distance_to_move, self.remaining_edge_distance)
            self.remaining_edge_distance -= step_distance
            distance_to_move -= step_distance

            if self.remaining_edge_distance <= 1e-9:
                self.prev_osm = self.current_osm
                self.current_osm = self.target_osm
                self.target_osm = None
                self.remaining_edge_distance = 0.0
                self.edge_length = 0.0

    def position(self, coords: Dict[int, Tuple[float, float]]) -> Tuple[float, float]:
        """Interpolated position along the current street edge."""
        ax, ay = coords[self.current_osm]
        if self.target_osm is None or self.edge_length <= 0:
            return ax, ay
        bx, by = coords[self.target_osm]
        f = 1.0 - self.remaining_edge_distance / self.edge_length
        return ax + (bx - ax) * f, ay + (by - ay) * f

    # --------------------------------------------------------
    # Trickle-style forwarding
    # --------------------------------------------------------

    def trickle_reset(self, now_s: float, full: bool = False):
        if full:
            self.trickle_interval = self.p.trickle_imin_s
        self.trickle_start = now_s
        self.trickle_t = random.uniform(
            self.trickle_interval / 2.0, self.trickle_interval
        )
        self.trickle_fired = False
        self.heard = 0

    def should_transmit(self, msg_id: str, now_s: float, now_unix: float) -> bool:
        if not self.radio.relay_enabled:
            return False

        pkt = self.received_alerts.get(msg_id)
        if pkt is None or pkt.ttl <= 0:
            return False
        if now_unix > pkt.expires_at:
            return False

        elapsed = now_s - self.trickle_start
        send = False

        if not self.trickle_fired and elapsed >= self.trickle_t:
            self.trickle_fired = True
            if self.heard < self.p.trickle_k:
                send = True
            else:
                self.forwarding_suppressed += 1

        if elapsed >= self.trickle_interval:
            self.trickle_interval = min(
                self.trickle_interval * 2.0, self.p.trickle_imax_s
            )
            self.trickle_reset(now_s)

        return send


# ============================================================
# SIMULATION
# ============================================================

def run_simulation(world: World, p: Params, seed: int, verbose: bool = False) -> dict:
    random.seed(seed)
    np.random.seed(seed)

    G = world.G
    coords = world.coords

    # ---- Authenticated alert ----
    authority = CivilDefenseAuthority()
    trusted_keys = {authority.public_key_hex}

    msg_id = f"ALERT-KRK-{random.getrandbits(32):08X}"
    expires_at = ALERT_TIMESTAMP_UNIX + p.max_packet_age_s
    signature = authority.sign_alert(
        msg_id, ALERT_TIMESTAMP_UNIX, expires_at, p.alert_type, p.alert_body
    )
    seed_packet = Packet(
        msg_id=msg_id,
        timestamp=ALERT_TIMESTAMP_UNIX,
        expires_at=expires_at,
        alert_type=p.alert_type,
        body=p.alert_body,
        ttl=p.initial_ttl,
        signature_hex=signature,
        pubkey_hex=authority.public_key_hex,
        origin_node="Emergency_Squad",
    )

    # ---- Deploy devices ----
    candidates = candidates_near_origin(world, p.initial_crowd_radius_m)
    if len(candidates) < 10:
        raise RuntimeError("Too few pedestrian nodes inside initial crowd radius.")

    nodes: List[CivilianNode] = []

    for k in range(p.num_seeds):
        start = world.origin_node if k == 0 else random.choice(candidates)
        s = CivilianNode(
            f"Emergency_Squad_{k}", start, p, trusted_keys, is_seed=True
        )
        s.received_alerts[msg_id] = seed_packet
        s.trickle_reset(0.0, full=True)
        nodes.append(s)

    for i in range(p.num_civilians):
        nodes.append(
            CivilianNode(
                f"Phone_{i:03d}", random.choice(candidates), p, trusted_keys
            )
        )

    civilians = [n for n in nodes if not n.is_seed]

    # ---- Metrics ----
    coverage_history: List[int] = []
    contact_history: List[int] = []
    tx_history: List[int] = []
    rx_history: List[int] = []
    dup_history: List[int] = []
    suppressed_history: List[int] = []
    delivery_times: Dict[str, float] = {}

    cumulative_tx = 0
    cumulative_dup = 0
    steps = int(p.duration_s / p.dt_s)

    for step in range(1, steps + 1):
        now_s = step * p.dt_s
        now_unix = ALERT_TIMESTAMP_UNIX + now_s

        # A. Move
        for n in nodes:
            n.move(G, p.dt_s)

        # B. Contacts (KD-tree on interpolated positions)
        pos = np.array([n.position(coords) for n in nodes])
        pairs = cKDTree(pos).query_pairs(p.radio_range_m, output_type="ndarray")

        neighbors: Dict[int, List[Tuple[int, float]]] = {
            i: [] for i in range(len(nodes))
        }
        if len(pairs) > 0:
            diff = pos[pairs[:, 0]] - pos[pairs[:, 1]]
            dists = np.hypot(diff[:, 0], diff[:, 1])
            for (i, j), d in zip(pairs, dists):
                neighbors[int(i)].append((int(j), float(d)))
                neighbors[int(j)].append((int(i), float(d)))

        # C. Broadcast transmissions
        pending: List[Tuple[CivilianNode, CivilianNode, Packet]] = []
        tx_this_step = 0

        for i, tx in enumerate(nodes):
            nbr_ids = {j for j, _ in neighbors[i]}

            # New neighbour: advertise quickly again (store-carry-forward)
            if msg_id in tx.received_alerts and (nbr_ids - tx.prev_neighbors):
                tx.trickle_reset(now_s, full=True)
            tx.prev_neighbors = nbr_ids

            if not tx.should_transmit(msg_id, now_s, now_unix):
                continue

            tx.transmissions_sent += 1
            tx_this_step += 1
            pkt = tx.received_alerts[msg_id]

            # One advert, independent reception chance at every neighbour
            for j, d in neighbors[i]:
                rx = nodes[j]
                prob = packet_delivery_probability(tx.radio, rx.radio, d, p)
                if random.random() < prob:
                    pending.append((tx, rx, pkt))

        # D. Apply receptions at end of step (no same-step chain propagation)
        rx_this_step = 0
        dup_this_step = 0

        for tx, rx, pkt in pending:
            if now_unix > pkt.expires_at:
                rx.expired_packets += 1
                continue

            # Verify BEFORE counting, so forgeries cannot suppress forwarding
            if not rx.verify(pkt, now_unix):
                rx.invalid_packets += 1
                continue

            if msg_id in rx.received_alerts:
                rx.heard += 1
                rx.duplicate_receptions += 1
                dup_this_step += 1
                continue

            rx.received_alerts[msg_id] = replace(pkt, ttl=pkt.ttl - 1)
            rx.successful_receptions += 1
            rx.trickle_reset(now_s, full=True)
            rx_this_step += 1
            delivery_times.setdefault(rx.node_id, now_s)

        # E. Statistics
        cumulative_tx += tx_this_step
        cumulative_dup += dup_this_step

        reached = sum(msg_id in n.received_alerts for n in civilians)
        coverage_history.append(reached)
        contact_history.append(len(pairs))
        tx_history.append(cumulative_tx)
        rx_history.append(rx_this_step)
        dup_history.append(cumulative_dup)
        suppressed_history.append(sum(n.forwarding_suppressed for n in nodes))

        if verbose and (step % 30 == 0 or step == 1):
            print(
                f"  t={now_s:5.0f}s: {reached:02d}/{len(civilians)} civilians | "
                f"contacts={len(pairs):03d} | TX(cum)={cumulative_tx:05d} | "
                f"RX(step)={rx_this_step:02d} | dup(cum)={cumulative_dup:05d}"
            )

    civ_times = [t for nid, t in delivery_times.items() if not nid.startswith("Emergency")]

    return {
        "msg_id": msg_id,
        "nodes": nodes,
        "civilians": civilians,
        "positions": [n.position(coords) for n in nodes],
        "coverage_history": coverage_history,
        "contact_history": contact_history,
        "tx_history": tx_history,
        "rx_history": rx_history,
        "dup_history": dup_history,
        "suppressed_history": suppressed_history,
        "delivery_times": civ_times,
        "steps": steps,
        "invalid": sum(n.invalid_packets for n in nodes),
        "expired": sum(n.expired_packets for n in nodes),
    }


# ============================================================
# REPORTING
# ============================================================

def print_summary(res: dict, p: Params):
    n_civ = len(res["civilians"])
    reached = res["coverage_history"][-1]
    times = res["delivery_times"]

    print("\n" + "=" * 64)
    print("EXECUTIVE SIMULATION METRICS")
    print("=" * 64)
    print(f"  Civilian devices               : {n_civ}")
    print(f"  Redundant seed devices         : {p.num_seeds}")
    print(f"  Simulation duration            : {p.duration_s} s (dt={p.dt_s} s)")
    print(f"  Maximum radio distance         : {p.radio_range_m:.0f} m")
    print(f"  PHY                            : {p.phy}")
    print(f"  Initial TTL / validity         : {p.initial_ttl} hops / {p.max_packet_age_s} s")
    print(f"  Trickle Imin/Imax/K            : {p.trickle_imin_s}/{p.trickle_imax_s}/{p.trickle_k}")
    print(f"  Final verified coverage        : {reached}/{n_civ} ({100.0 * reached / n_civ:.1f}%)")
    print(f"  Total broadcasts               : {res['tx_history'][-1]}")
    print(f"  Successful first receptions    : {sum(res['rx_history'])}")
    print(f"  Duplicate receptions (total)   : {res['dup_history'][-1]}")
    print(f"  Suppressed transmissions       : {res['suppressed_history'][-1]}")
    print(f"  Invalid packets                : {res['invalid']}")
    print(f"  Expired packets                : {res['expired']}")
    if times:
        print(f"  Median delivery time           : {np.median(times):.1f} s")
        print(f"  P90 delivery time              : {np.percentile(times, 90):.1f} s")
    else:
        print("  Delivery time                  : no civilian deliveries")
    print("=" * 64)
    print(
        "\nIMPORTANT: simplified stochastic BLE link-budget model, meant for "
        "comparative simulation, not a substitute for on-device measurements."
    )


def plot_results(world: World, res: dict, p: Params, path: str = "dtn_results.png"):
    nodes = res["nodes"]
    msg_id = res["msg_id"]
    steps = np.arange(1, res["steps"] + 1) * p.dt_s
    n_civ = len(res["civilians"])

    fig, axes = plt.subplots(2, 2, figsize=(17, 12))

    # Plot 1: final network
    ax = axes[0, 0]
    world.edges_gdf.plot(ax=ax, linewidth=0.35, color="#777777", alpha=0.55)

    reached_xy = [
        pos for n, pos in zip(nodes, res["positions"])
        if not n.is_seed and msg_id in n.received_alerts
    ]
    unreached_xy = [
        pos for n, pos in zip(nodes, res["positions"])
        if not n.is_seed and msg_id not in n.received_alerts
    ]
    seed_xy = [pos for n, pos in zip(nodes, res["positions"]) if n.is_seed]

    if unreached_xy:
        ax.scatter(*zip(*unreached_xy), color="#888888", s=35, label="Unreached", zorder=3)
    if reached_xy:
        ax.scatter(
            *zip(*reached_xy), color="#00cc66", edgecolors="black",
            s=55, label="Verified alert", zorder=4,
        )
    if seed_xy:
        ax.scatter(
            *zip(*seed_xy), color="#ff1744", marker="*", s=250,
            label="Seed devices", zorder=5,
        )
    ax.set_title("DTN Alert Coverage, Krakow")
    ax.set_xlabel("Projected X (m)")
    ax.set_ylabel("Projected Y (m)")
    ax.legend()

    # Plot 2: coverage over time
    ax = axes[0, 1]
    ax.plot(steps, res["coverage_history"], color="#00aa55", linewidth=2.5)
    ax.axhline(n_civ, color="#444444", linestyle=":", label="All civilians")
    ax.axhline(n_civ * MC_TARGET_FRACTION, color="#cc0000", linestyle="--",
               label=f"{int(MC_TARGET_FRACTION * 100)}% target")
    ax.set_title("Verified Alert Coverage")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Civilians reached")
    ax.grid(linestyle=":", alpha=0.5)
    ax.legend()

    # Plot 3: forwarding and redundancy (all cumulative)
    ax = axes[1, 0]
    ax.plot(steps, res["tx_history"], color="#0066ff", linewidth=2, label="Broadcasts (cum)")
    ax.plot(steps, res["dup_history"], color="#9933cc", linewidth=2, linestyle=":", label="Duplicate RX (cum)")
    ax.plot(steps, res["suppressed_history"], color="#ff9900", linewidth=2, linestyle="--", label="Suppressed (cum)")
    ax.set_title("Forwarding and Redundancy")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Cumulative events")
    ax.grid(linestyle=":", alpha=0.5)
    ax.legend()

    # Plot 4: contacts vs first receptions
    ax = axes[1, 1]
    ax.plot(steps, res["contact_history"], color="#0099cc", linewidth=2, label="Physical contacts")
    ax.plot(steps, np.cumsum(res["rx_history"]), color="#16a085", linewidth=2, label="First receptions (cum)")
    ax.set_title("Physical Contact vs Delivery")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Events")
    ax.grid(linestyle=":", alpha=0.5)
    ax.legend()

    plt.tight_layout()
    plt.savefig(path, dpi=130)
    print(f"Figure saved to {path}")
    plt.show()


# ============================================================
# MONTE CARLO: make "does not fail" a number
# ============================================================

def monte_carlo(world: World, p: Params, runs: int, label: str) -> dict:
    successes = 0
    fractions = []
    t_target = []

    for r in range(runs):
        res = run_simulation(world, p, seed=1000 + r)
        n_civ = len(res["civilians"])
        cov = res["coverage_history"]

        idx = min(int(MC_DEADLINE_S / p.dt_s), len(cov)) - 1
        frac = cov[idx] / n_civ
        fractions.append(frac)
        if frac >= MC_TARGET_FRACTION:
            successes += 1

        hit = next(
            (i for i, c in enumerate(cov) if c / n_civ >= MC_TARGET_FRACTION), None
        )
        if hit is not None:
            t_target.append((hit + 1) * p.dt_s)

    rate = successes / runs
    print(f"\n[{label}] {runs} runs, range={p.radio_range_m:.0f} m, "
          f"exp={p.path_loss_exp}, body={p.body_loss_db} dB")
    print(f"  P(>= {int(MC_TARGET_FRACTION * 100)}% reached within {MC_DEADLINE_S} s) = {rate:.2f}")
    print(f"  Coverage at deadline: mean={np.mean(fractions):.2f}, "
          f"min={np.min(fractions):.2f}, p10={np.percentile(fractions, 10):.2f}")
    if t_target:
        print(f"  Median time to target (successful runs): {np.median(t_target):.0f} s")
    return {"success_rate": rate, "fractions": fractions}


def density_sweep(world: World, p: Params, counts: List[int], runs: int, label: str):
    """Find the crowd density where phone-to-phone delivery starts to work."""
    print(f"\n[{label}] Density sweep ({runs} runs each)")
    print(f"  {'devices':>8} {'per km2':>8} {'P(success)':>11} {'mean cov':>9}")
    area_km2 = math.pi * (p.initial_crowd_radius_m / 1000.0) ** 2
    for n in counts:
        pn = replace(p, num_civilians=n)
        fr = []
        ok = 0
        for r in range(runs):
            res = run_simulation(world, pn, seed=2000 + r)
            cov = res["coverage_history"]
            idx = min(int(MC_DEADLINE_S / pn.dt_s), len(cov)) - 1
            f = cov[idx] / n
            fr.append(f)
            ok += f >= MC_TARGET_FRACTION
        print(f"  {n:>8} {n / area_km2:>8.0f} {ok / runs:>11.2f} {np.mean(fr):>9.2f}")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    world = build_world(BASE)

    print("[2/4] Running single detailed simulation...")
    result = run_simulation(world, BASE, seed=42, verbose=True)

    print("[3/4] Summary")
    print_summary(result, BASE)
    plot_results(world, result, BASE)

    if MONTE_CARLO_RUNS > 0:
        print("\n[4/4] Monte Carlo robustness check")
        monte_carlo(world, BASE, MONTE_CARLO_RUNS, "Baseline radio")
        monte_carlo(world, PESSIMISTIC, MONTE_CARLO_RUNS, "Pessimistic radio")

    if DENSITY_SWEEP:
        density_sweep(world, BASE, DENSITY_SWEEP, SWEEP_RUNS, "Baseline radio")
        density_sweep(world, PESSIMISTIC, DENSITY_SWEEP, SWEEP_RUNS, "Pessimistic radio")