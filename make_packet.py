"""
Creates a signed BlackoutMesh test alert (79 bytes) and prints the two values
you paste into Config.kt.

Usage:
    pip install cryptography
    python make_packet.py --template 1 --param 2 --minutes 120

Packet layout (big-endian):
    version(1) msg_id(4) issued_at(4) valid_minutes(2) template(2) param(2)  = 15 bytes
    signature(64)                                                           = 64 bytes

TEST KEY ONLY. The private key is saved to authority.key next to this script.
Never reuse it for anything real.
"""
import argparse
import os
import random
import struct
import time

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)

KEY_FILE = "authority.key"

parser = argparse.ArgumentParser(description="Generate signed BlackoutMesh 79-byte binary test alert")
parser.add_argument("--template", type=int, default=1, help="Template ID (1=Evac, 2=Shelter, 3=All Clear)")
parser.add_argument("--param", type=int, default=2, help="Param (1=Town Hall, 2=TAURON Arena Gate 3, 3=Main Station)")
parser.add_argument("--minutes", type=int, default=120, help="Validity duration in minutes")
args = parser.parse_args()

if os.path.exists(KEY_FILE):
    with open(KEY_FILE, "rb") as f:
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(f.read())
else:
    priv = ed25519.Ed25519PrivateKey.generate()
    with open(KEY_FILE, "wb") as f:
        f.write(priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    print(f"(new test key saved to {KEY_FILE})")

pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

payload = struct.pack(
    ">BIIHHH",
    1,                          # version
    random.getrandbits(32),     # msg_id
    int(time.time()),           # issued_at (unix seconds)
    args.minutes,               # valid_minutes
    args.template,
    args.param,
)
packet = payload + priv.sign(payload)

print()
print("Paste these into Config.kt:")
print()
print(f'const val PUBLIC_KEY_HEX = "{pub.hex()}"')
print(f'const val SEED_PACKET_HEX = "{packet.hex()}"')
print()
print(f"(packet is {len(packet)} bytes, valid for {args.minutes} min)")
