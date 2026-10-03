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

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add parent to path so we can import blackoutmesh and simulation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blackoutmesh.crypto import AuthoritySigner

app = FastAPI(title="BlackoutMesh Dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files from dashboard directory
dashboard_dir = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")

# Authority for dispatch console
_authority = AuthoritySigner()


class DispatchRequest(BaseModel):
    alert_type: str = "CIVIL_DEFENSE_EVAC"
    body: str = "Emergency alert from dispatch console."
    ttl: int = 15
    validity_seconds: int = 3600


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
    """Return the authority's public key for display."""
    return {"pubkey_hex": _authority.public_key_hex}


@app.post("/api/dispatch")
async def dispatch_alert(req: DispatchRequest):
    """WP 3.4: Sign and return a new alert packet."""
    packet = _authority.create_alert(
        alert_type=req.alert_type,
        body=req.body,
        ttl=req.ttl,
        validity_seconds=req.validity_seconds,
    )
    return packet.to_wire_dict()


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
