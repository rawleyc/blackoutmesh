"""
WP 1.2 — Pre-Shared Root Key Distribution

Devices ship with a static JSON keyring containing the Ed25519 public keys
of trusted regional authorities. This module manages that trust store.

Example keyring JSON:
{
    "authorities": [
        {
            "id": "PL-KRK-MUWK",
            "name": "Małopolski Urząd Wojewódzki",
            "pubkey_hex": "abc123..."
        },
        {
            "id": "PL-KRK-PSP",
            "name": "PSP Kraków (Fire/Rescue)",
            "pubkey_hex": "def456..."
        }
    ]
}
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set, Dict


@dataclass(frozen=True)
class AuthorityEntry:
    """A single trusted authority in the keyring."""
    id: str
    name: str
    pubkey_hex: str


class Keyring:
    """In-memory trust store of pre-shared authority public keys."""

    def __init__(self):
        self._authorities: Dict[str, AuthorityEntry] = {}

    @property
    def trusted_pubkeys(self) -> Set[str]:
        """Set of all trusted public key hex strings."""
        return {a.pubkey_hex for a in self._authorities.values()}

    def add(self, authority_id: str, name: str, pubkey_hex: str) -> None:
        """Register an authority's public key."""
        self._authorities[authority_id] = AuthorityEntry(
            id=authority_id, name=name, pubkey_hex=pubkey_hex
        )

    def remove(self, authority_id: str) -> bool:
        """Remove an authority. Returns True if it existed."""
        return self._authorities.pop(authority_id, None) is not None

    def get(self, authority_id: str) -> Optional[AuthorityEntry]:
        """Look up an authority by ID."""
        return self._authorities.get(authority_id)

    def contains_pubkey(self, pubkey_hex: str) -> bool:
        """Check if a public key is trusted."""
        return pubkey_hex in self.trusted_pubkeys

    def save(self, path: Path) -> None:
        """Persist keyring to JSON file."""
        data = {
            "authorities": [
                {"id": a.id, "name": a.name, "pubkey_hex": a.pubkey_hex}
                for a in self._authorities.values()
            ]
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Keyring":
        """Load keyring from JSON file."""
        kr = cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("authorities", []):
            kr.add(entry["id"], entry["name"], entry["pubkey_hex"])
        return kr

    def __len__(self) -> int:
        return len(self._authorities)

    def __repr__(self) -> str:
        return f"Keyring({len(self)} authorities)"
