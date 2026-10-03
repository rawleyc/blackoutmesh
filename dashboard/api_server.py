"""
WP 3.0 — FastAPI backend serving simulation data to the dashboard.

Endpoints:
  GET  /api/world       -> Street network GeoJSON (edges + origin)
  POST /api/simulate    -> Run a simulation, return time-series data
  POST /api/dispatch    -> Sign and return a new alert packet (WP 3.4)

Run:
  cd BlackoutMesh
  python -m uvicorn dashboard.api_server:app --reload --port 8000
"""

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add parent to path so we can import blackoutmesh and simulation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env if present
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))

from contextlib import asynccontextmanager
from blackoutmesh.crypto import AuthoritySigner
from blackoutmesh.codebook import Codebook

# Authority & Official Laptop BLE Broadcaster
_ble_broadcaster = None
HAS_BLE = False

key_path = Path(__file__).resolve().parent.parent / "authority.key"
try:
    from official_broadcaster import (
        LaptopBleBroadcaster,
        get_or_create_authority_key,
        pack_and_sign_alert,
    )
    _priv_key = get_or_create_authority_key(str(key_path))
    _authority = AuthoritySigner(private_key=_priv_key)
    _ble_broadcaster = LaptopBleBroadcaster(key_path=str(key_path))
    HAS_BLE = True
except Exception as e:
    print(f"[*] BLE Broadcaster initialization deferred: {e}")
    _authority = AuthoritySigner()

_codebook = Codebook.load_default()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for FastAPI and the Windows Bluetooth GATT server."""
    if _ble_broadcaster:
        try:
            await _ble_broadcaster.initialize()
            # Set default initial alert
            _ble_broadcaster.update_alert(template=1, param=2, valid_minutes=120)
            print(f"[BLE] Official Laptop Broadcaster initialized. PubKey: {_ble_broadcaster.pub_key_hex}")
        except Exception as e:
            print(f"[BLE WARNING] Could not initialize Bluetooth controller on startup: {e}")
    yield
    if _ble_broadcaster and _ble_broadcaster.is_broadcasting:
        try:
            _ble_broadcaster.stop_broadcasting()
            print("[BLE] Broadcaster stopped cleanly.")
        except Exception:
            pass


app = FastAPI(title="BlackoutMesh Dashboard API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files from dashboard directory
dashboard_dir = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")


class CompactDispatchRequest(BaseModel):
    template_id: int = 101
    duration_minutes: int = 120
    seq: int = 0
    loc_type: int = 1
    loc_ref: str = "KRK_TAURON_G3"
    custom_text: str = ""
    ttl: int = 15


class SimulationRequest(BaseModel):
    num_civilians: int = 75
    duration_s: int = 900
    radio_range_m: float = 50.0
    seed: int = 42


@app.get("/")
async def index():
    return FileResponse(str(dashboard_dir / "index.html"))


@app.get("/api/authority")
async def get_authority():
    """Return the authority's public key and CARTO visualizer key."""
    return {
        "pubkey_hex": _authority.public_key_hex,
        "carto_api_key": os.environ.get("CARTO__API_KEY", ""),
    }


@app.get("/api/config")
async def get_config():
    """Return runtime configuration."""
    return {
        "carto_api_key": os.environ.get("CARTO__API_KEY", ""),
        "pubkey_hex": _authority.public_key_hex,
    }


@app.get("/api/codebook")
async def get_codebook():
    """WP 1.1: Return active emergency codebook and tactical shelter directory."""
    return {
        "version": _codebook.codebook_version,
        "authority_id": _codebook.authority_id,
        "authority_name": _codebook.authority_name,
        "templates": _codebook.templates,
        "shelters": _codebook.shelters,
    }


@app.post("/api/dispatch")
async def dispatch_alert(req: CompactDispatchRequest):
    """WP 1.1 / 3.4: Sign and return a new compact templated alert packet."""
    packet = _authority.create_compact_alert(
        template_id=req.template_id,
        duration_minutes=req.duration_minutes,
        seq=req.seq,
        loc_type=req.loc_type,
        loc_ref=req.loc_ref,
        custom_text=req.custom_text,
        ttl=req.ttl,
    )
    rendered = _codebook.render(
        template_id=req.template_id,
        loc_type=req.loc_type,
        loc_ref=req.loc_ref,
        custom_text=req.custom_text,
    )
    res = packet.to_wire_dict()
    res["wire_size_bytes"] = packet.raw_wire_size_bytes()
    res["single_gatt_mtu_transfer"] = res["wire_size_bytes"] < 240
    res["rendered"] = {
        "category": rendered.category,
        "severity": rendered.severity,
        "pl": rendered.text_pl,
        "en": rendered.text_en,
        "ua": rendered.text_ua,
        "lat": rendered.lat,
        "lon": rendered.lon,
        "is_fallback": rendered.is_fallback,
    }
    # Broadcast over the air via laptop's Bluetooth adapter if available
    ble_active = False
    if _ble_broadcaster:
        try:
            tpl_map = {
                101: 1,  # Evacuation
                102: 2,  # Air Raid
                103: 3,  # Power grid failure / Water point
                104: 4,  # All Clear / Safe return
                105: 4,
                999: 1,
            }
            loc_map = {
                "KRK_RYNEK_PODZ": 1,      # Town Hall / Rynek Bunker
                "KRK_TAURON_G3": 2,       # TAURON Arena Gate 3
                "KRK_DWORZEC_GL": 3,      # Main Station Tunnel
                "KRK_NOWA_HUTA_CAHTS": 4, # Nowa Huta Shelter
            }
            t_id = tpl_map.get(req.template_id, 1)
            p_id = loc_map.get(str(req.loc_ref), 2)

            _ble_broadcaster.update_alert(
                template=t_id, param=p_id, valid_minutes=req.duration_minutes
            )
            if not _ble_broadcaster.is_broadcasting and _ble_broadcaster.provider:
                _ble_broadcaster.start_broadcasting()
            ble_active = _ble_broadcaster.is_broadcasting
            res["ble_broadcasting"] = ble_active
            res["ble_packet_hex"] = _ble_broadcaster.current_packet.hex() if _ble_broadcaster.current_packet else None
            res["ble_served_count"] = _ble_broadcaster.served_count
            print(f"[BLE OTA] Updated active alert: template={t_id}, param={p_id}, bytes={len(_ble_broadcaster.current_packet)}")
        except Exception as e:
            print(f"[BLE] Error updating broadcast: {e}")

    return res


@app.get("/api/ble/status")
async def get_ble_status():
    """Return status of laptop's official BLE broadcaster."""
    if not _ble_broadcaster:
        return {"available": False, "reason": "winsdk or BLE controller not initialized"}
    return {
        "available": True,
        "is_broadcasting": _ble_broadcaster.is_broadcasting,
        "served_count": _ble_broadcaster.served_count,
        "pubkey_hex": _ble_broadcaster.pub_key_hex,
        "service_uuid": "6e0b1a10-7b1d-4a52-9c1e-5a6f0a1d0001",
        "char_uuid": "6e0b1a10-7b1d-4a52-9c1e-5a6f0a1d0002",
        "current_packet_hex": _ble_broadcaster.current_packet.hex() if _ble_broadcaster.current_packet else None,
        "current_packet_bytes": len(_ble_broadcaster.current_packet) if _ble_broadcaster.current_packet else 0,
    }


@app.post("/api/ble/toggle")
async def toggle_ble():
    """Toggle the laptop's BLE over-the-air broadcast on/off."""
    if not _ble_broadcaster or not _ble_broadcaster.provider:
        return JSONResponse(status_code=400, content={"error": "BLE controller not initialized"})
    try:
        if _ble_broadcaster.is_broadcasting:
            _ble_broadcaster.stop_broadcasting()
        else:
            if not _ble_broadcaster.current_packet:
                _ble_broadcaster.update_alert(1, 2, 120)
            _ble_broadcaster.start_broadcasting()
        return {
            "success": True,
            "is_broadcasting": _ble_broadcaster.is_broadcasting,
            "served_count": _ble_broadcaster.served_count,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/simulate")
async def run_sim(req: SimulationRequest):
    """Run simulation and return results as JSON.

    This imports simulation.py at call-time to avoid loading OSMnx at startup.
    The first call will be slow (network fetch); subsequent calls reuse the cached graph.
    """
    try:
        from dataclasses import replace as dc_replace
        import simulation as sim

        params = dc_replace(
            sim.BASE,
            num_civilians=req.num_civilians,
            duration_s=req.duration_s,
            radio_range_m=req.radio_range_m,
        )

        # Build world (cached after first call)
        if not hasattr(run_sim, "_world"):
            run_sim._world = sim.build_world(params)

        result = sim.run_simulation(run_sim._world, params, seed=req.seed, verbose=False)

        return {
            "coverage_history": result["coverage_history"],
            "tx_history": result["tx_history"],
            "dup_history": result["dup_history"],
            "suppressed_history": result["suppressed_history"],
            "contact_history": result["contact_history"],
            "rx_history": result["rx_history"],
            "delivery_times": result["delivery_times"],
            "num_civilians": req.num_civilians,
            "duration_s": req.duration_s,
            "positions": [list(p) for p in result["positions"]],
            "node_types": [
                "seed" if n.is_seed else
                ("reached" if result["msg_id"] in n.received_alerts else "unreached")
                for n in result["nodes"]
            ],
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
