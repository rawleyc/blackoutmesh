"""
Official BlackoutMesh BLE Broadcaster Node (Windows).

Turns this laptop into the root emergency transmitter over Bluetooth Low Energy.
Broadcasts the emergency service beacon over the air and serves the signed
79-byte alert packet over a local GATT server with ZERO internet or Wi-Fi required.

Compatible with the BlackoutMesh Android MVP (Alert.kt / Mesh.kt).
"""

import argparse
import asyncio
import os
import random
import struct
import sys
import time
import uuid

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)

import winsdk.windows.devices.bluetooth.genericattributeprofile as gap
import winsdk.windows.devices.bluetooth.advertisement as adv
import winsdk.windows.storage.streams as streams

SERVICE_UUID = uuid.UUID("6e0b1a10-7b1d-4a52-9c1e-5a6f0a1d0001")
CHAR_UUID = uuid.UUID("6e0b1a10-7b1d-4a52-9c1e-5a6f0a1d0002")
KEY_FILE = "authority.key"


def get_or_create_authority_key(key_path: str = KEY_FILE) -> ed25519.Ed25519PrivateKey:
    """Load existing authority key or generate a new one."""
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return ed25519.Ed25519PrivateKey.from_private_bytes(f.read())
    priv = ed25519.Ed25519PrivateKey.generate()
    with open(key_path, "wb") as f:
        f.write(priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    return priv


def pack_and_sign_alert(
    priv_key: ed25519.Ed25519PrivateKey,
    template: int = 1,
    param: int = 2,
    valid_minutes: int = 120,
    msg_id: int | None = None,
    issued_at: int | None = None,
) -> bytes:
    """Pack and sign 79-byte binary alert for Android MVP.

    Layout:
        version(1) msg_id(4) issued_at(4) valid_minutes(2) template(2) param(2) = 15 bytes
        signature(64)                                                           = 64 bytes
    """
    mid = msg_id if msg_id is not None else random.getrandbits(32)
    ts = issued_at if issued_at is not None else int(time.time())
    payload = struct.pack(">BIIHHH", 1, mid, ts, valid_minutes, template, param)
    sig = priv_key.sign(payload)
    return payload + sig


class LaptopBleBroadcaster:
    """Manages the laptop's Bluetooth adapter as the Official Emergency Transmitter."""

    def __init__(self, key_path: str = KEY_FILE):
        self.priv_key = get_or_create_authority_key(key_path)
        self.pub_key_bytes = self.priv_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.pub_key_hex = self.pub_key_bytes.hex()
        self.current_packet: bytes = b""
        self.provider: gap.GattServiceProvider | None = None
        self.characteristic: gap.GattLocalCharacteristic | None = None
        self.adv_publisher: adv.BluetoothLEAdvertisementPublisher | None = None
        self.read_token: int | None = None
        self.is_broadcasting: bool = False
        self.served_count: int = 0
        self.loop: asyncio.AbstractEventLoop | None = None

    async def initialize(self):
        """Create the GATT service and characteristic on the Windows Bluetooth controller."""
        self.loop = asyncio.get_running_loop()
        res = await gap.GattServiceProvider.create_async(SERVICE_UUID)
        if res.error != 0:
            raise RuntimeError(f"GattServiceProvider create failed with error: {res.error}")
        self.provider = res.service_provider
        service = self.provider.service

        char_params = gap.GattLocalCharacteristicParameters()
        char_params.characteristic_properties = gap.GattCharacteristicProperties.READ
        char_params.read_protection_level = gap.GattProtectionLevel.PLAIN

        char_res = await service.create_characteristic_async(CHAR_UUID, char_params)
        if char_res.error != 0:
            raise RuntimeError(f"Characteristic creation failed with error: {char_res.error}")

        self.characteristic = char_res.characteristic
        self.read_token = self.characteristic.add_read_requested(self._on_read_requested)

    def _on_read_requested(self, sender, args):
        """Handle incoming GATT read from nearby phones."""
        deferral = args.get_deferral()

        async def reply():
            try:
                req = await args.get_request_async()
                if req and self.current_packet:
                    writer = streams.DataWriter()
                    # Honor offset if requested
                    offset = req.offset
                    slice_data = self.current_packet[offset:] if offset < len(self.current_packet) else b""
                    writer.write_bytes(slice_data)
                    req.respond_with_value(writer.detach_buffer())
                    self.served_count += 1
                    print(
                        f"\n>>> [OVER-THE-AIR ALERT SERVED] Transferred {len(slice_data)} bytes to nearby phone! "
                        f"(Total served: {self.served_count})"
                    )
            except Exception as e:
                print(f"[BLE ERROR] Failed serving read: {e}", file=sys.stderr)
            finally:
                deferral.complete()

        # Run reply thread-safely in the active event loop
        target_loop = self.loop
        if not target_loop or not target_loop.is_running():
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        if target_loop and target_loop.is_running():
            asyncio.run_coroutine_threadsafe(reply(), target_loop)
        else:
            asyncio.run(reply())

    def update_alert(
        self,
        template: int = 1,
        param: int = 2,
        valid_minutes: int = 120,
        msg_id: int | None = None,
    ) -> bytes:
        """Sign and update the active alert packet."""
        self.current_packet = pack_and_sign_alert(
            self.priv_key, template, param, valid_minutes, msg_id=msg_id
        )
        return self.current_packet

    def start_broadcasting(self):
        """Start over-the-air BLE advertisement."""
        if not self.provider:
            raise RuntimeError("Broadcaster not initialized. Call initialize() first.")
        if self.is_broadcasting:
            return

        # 1. Start Windows GattServiceProvider
        adv_params = gap.GattServiceProviderAdvertisingParameters()
        adv_params.is_connectable = True
        adv_params.is_discoverable = True
        try:
            w_sd = streams.DataWriter()
            w_sd.write_bytes(b"BKOUT")
            adv_params.service_data = w_sd.detach_buffer()
        except Exception:
            pass

        self.provider.start_advertising(adv_params)

        # 2. Companion Manufacturer Data Beacon (Company ID 0xFFFF, payload BKOUT)
        try:
            self.adv_publisher = adv.BluetoothLEAdvertisementPublisher()
            md = adv.BluetoothLEManufacturerData()
            md.company_id = 0xFFFF
            w_md = streams.DataWriter()
            w_md.write_bytes(b"BKOUT")
            md.data = w_md.detach_buffer()
            self.adv_publisher.advertisement.manufacturer_data.append(md)
            self.adv_publisher.start()
        except Exception as e:
            print(f"[BLE] Note on beacon publisher: {e}")

        self.is_broadcasting = True

    def stop_broadcasting(self):
        """Stop advertising."""
        if self.adv_publisher:
            try:
                self.adv_publisher.stop()
            except Exception:
                pass
            self.adv_publisher = None
        if self.provider and self.is_broadcasting:
            self.provider.stop_advertising()
            self.is_broadcasting = False


async def run_standalone():
    parser = argparse.ArgumentParser(
        description="Official BlackoutMesh BLE Transmitter (Laptop Command Post)"
    )
    parser.add_argument("--template", type=int, default=1, help="Template ID (1=Evac, 2=Shelter, 3=All Clear)")
    parser.add_argument("--param", type=int, default=2, help="Param (1=Town Hall, 2=TAURON Arena Gate 3, 3=Main Station)")
    parser.add_argument("--minutes", type=int, default=120, help="Validity duration in minutes")
    args = parser.parse_args()

    # Ensure UTF-8 output and line buffering on Windows consoles
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    broadcaster = LaptopBleBroadcaster()
    print("=" * 65)
    print("[*] BLACKOUTMESH -- OFFICIAL TRANSMITTER NODE (OFFLINE BLE)")
    print("=" * 65)
    print(f"Authority Public Key: {broadcaster.pub_key_hex}")
    print(f"Service UUID:         {SERVICE_UUID}")
    print(f"Characteristic UUID:  {CHAR_UUID}")
    print("-" * 65)

    packet = broadcaster.update_alert(
        template=args.template, param=args.param, valid_minutes=args.minutes
    )
    print(f"Active Alert Packet:  {len(packet)} bytes (signed with authority.key)")
    print(f"Packet Hex:           {packet.hex()}")
    print("-" * 65)

    print("Initializing Windows Bluetooth controller...")
    await broadcaster.initialize()
    broadcaster.start_broadcasting()

    print("\n[ACTIVE] BROADCAST RUNNING OVER-THE-AIR!")
    print("Your laptop is now actively shouting the BlackoutMesh beacon.")
    print("Phones within 30-50m with the app open will detect this beacon,")
    print("pull the alert over GATT, and sound the emergency alarm.")
    print("\nPress Ctrl+C to stop broadcasting.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping broadcast...")
        broadcaster.stop_broadcasting()
        print("Broadcast stopped.")


if __name__ == "__main__":
    asyncio.run(run_standalone())
