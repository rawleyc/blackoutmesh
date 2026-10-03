import json
import time
import uuid
from typing import Dict, List, Optional, Set
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature


class TieredMeshPacket:
    def __init__(
        self,
        alert_type: str,
        coordinates: List[float],
        body: str,
        ttl: int = 4,
        msg_id: Optional[str] = None,
        timestamp: Optional[int] = None,
        authority_pubkey_hex: Optional[str] = None,
        signature_hex: Optional[str] = None,
    ):
        self.msg_id = msg_id or str(uuid.uuid4())
        self.timestamp = timestamp or int(time.time())
        self.ttl = ttl  # Only applies to civilian hops
        self.alert_type = alert_type
        self.coordinates = coordinates
        self.body = body
        self.authority_pubkey_hex = authority_pubkey_hex
        self.signature_hex = signature_hex

    def canonical_bytes(self) -> bytes:
        payload = {
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "alert_type": self.alert_type,
            "coordinates": self.coordinates,
            "body": self.body,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def to_wire(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "ttl": self.ttl,
            "alert_type": self.alert_type,
            "coordinates": self.coordinates,
            "body": self.body,
            "authority_pubkey_hex": self.authority_pubkey_hex,
            "signature_hex": self.signature_hex,
        }

    @classmethod
    def from_wire(cls, data: dict) -> "TieredMeshPacket":
        return cls(
            msg_id=data["msg_id"],
            timestamp=data["timestamp"],
            ttl=data["ttl"],
            alert_type=data["alert_type"],
            coordinates=data["coordinates"],
            body=data["body"],
            authority_pubkey_hex=data["authority_pubkey_hex"],
            signature_hex=data["signature_hex"],
        )


class HybridMeshNode:
    def __init__(self, node_id: str, is_official: bool, trusted_authority_pubkey: str):
        self.node_id = node_id
        self.is_official = is_official  # Backbone node vs Civilian device
        self.trusted_authority_pubkey = trusted_authority_pubkey
        self.seen_cache: Set[str] = set()
        self.peers: List["HybridMeshNode"] = []

    def connect_peer(self, peer: "HybridMeshNode"):
        if peer not in self.peers:
            self.peers.append(peer)

    def verify_authority_signature(self, packet: TieredMeshPacket) -> bool:
        if packet.authority_pubkey_hex != self.trusted_authority_pubkey:
            return False
        try:
            pubkey = ed25519.Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(packet.authority_pubkey_hex)
            )
            pubkey.verify(bytes.fromhex(packet.signature_hex), packet.canonical_bytes())
            return True
        except (InvalidSignature, ValueError):
            return False

    def receive(self, packet_wire: dict, sender: "HybridMeshNode"):
        packet = TieredMeshPacket.from_wire(packet_wire)

        # 1. Deduplication: Drop already processed packets
        if packet.msg_id in self.seen_cache:
            return
        self.seen_cache.add(packet.msg_id)

        # 2. Cryptographic Validation: Verify official root key
        if not self.verify_authority_signature(packet):
            print(f"[{self.node_id}] DROPPED: Invalid signature from sender {sender.node_id}")
            return

        print(
            f"[{self.node_id} ({'OFFICIAL' if self.is_official else 'CIVILIAN'})] "
            f"ACCEPTED: '{packet.body}' | Received TTL={packet.ttl} (from {sender.node_id})"
        )

        # 3. Two-Tier Relay & TTL Logic
        next_packet = TieredMeshPacket.from_wire(packet.to_wire())

        if self.is_official:
            # Official backbone: Zero TTL penalty, relays indefinitely across municipal nodes
            print(f"[{self.node_id}] BACKBONE RELAY: Forwarding without TTL decrement.")
            self._broadcast(next_packet, exclude_sender_id=sender.node_id)
        else:
            # Civilian node: Decrement TTL only if hop originated from another civilian
            if not sender.is_official:
                next_packet.ttl -= 1

            if next_packet.ttl > 1:
                print(f"[{self.node_id}] CIVILIAN RELAY: Forwarding with decremented TTL={next_packet.ttl}.")
                self._broadcast(next_packet, exclude_sender_id=sender.node_id)
            else:
                print(f"[{self.node_id}] CIVILIAN TTL DEPLETED (TTL={next_packet.ttl}). Storing locally, halting broadcast.")

    def _broadcast(self, packet: TieredMeshPacket, exclude_sender_id: str):
        wire_data = packet.to_wire()
        for peer in self.peers:
            if peer.node_id != exclude_sender_id:
                peer.receive(wire_data, sender=self)


# ===================================================
# Validation Scenario: Long-Distance Relay Demonstration
# ===================================================
if __name__ == "__main__":
    # Generate Civil Protection Authority Keypair
    auth_privkey = ed25519.Ed25519PrivateKey.generate()
    auth_pubkey_hex = auth_privkey.public_key().public_bytes_raw().hex()

    # Network Topology:
    # Authority -> Official Hub (Krakow Arena) -> Official Cruiser -> Civilian 1 -> Civilian 2 -> Civilian 3 (Drops)
    hub_arena = HybridMeshNode("HQ_TauronArena", is_official=True, trusted_authority_pubkey=auth_pubkey_hex)
    cruiser_psp = HybridMeshNode("Patrol_PSP_1", is_official=True, trusted_authority_pubkey=auth_pubkey_hex)
    civ_1 = HybridMeshNode("Phone_Jan", is_official=False, trusted_authority_pubkey=auth_pubkey_hex)
    civ_2 = HybridMeshNode("Phone_Anna", is_official=False, trusted_authority_pubkey=auth_pubkey_hex)
    civ_3 = HybridMeshNode("Phone_Piotr", is_official=False, trusted_authority_pubkey=auth_pubkey_hex)

    # Establish physical peer links
    hub_arena.connect_peer(cruiser_psp)
    cruiser_psp.connect_peer(hub_arena)

    cruiser_psp.connect_peer(civ_1)
    civ_1.connect_peer(cruiser_psp)

    civ_1.connect_peer(civ_2)
    civ_2.connect_peer(civ_1)

    civ_2.connect_peer(civ_3)
    civ_3.connect_peer(civ_2)

    # Create packet with Civilian TTL = 2
    alert = TieredMeshPacket(
        alert_type="EVACUATION_ORDER",
        coordinates=[50.0647, 19.9450],
        body="Water intake contamination. Collect bottled water at Mogilska Depot.",
        ttl=2,
    )
    alert.authority_pubkey_hex = auth_pubkey_hex
    alert.signature_hex = auth_privkey.sign(alert.canonical_bytes()).hex()

    print("\n--- TRIGGERING TRANSMISSION FROM HEADQUARTERS ---")
    # Initiate broadcast from backbone hub
    hub_arena.receive(alert.to_wire(), sender=hub_arena)