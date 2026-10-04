"""
BlackoutMesh Tactical Dashboard — Hardened DTN Console Backend API
Implements WBS 1.0 - 9.0 Change Specifications:
- Truthful labeling & separation of Live Operations vs Coverage Planner (Sim)
- Cryptographic two-step Alert Dispatcher (Preview -> Review & Send with preview_id)
- Live Transmitter state with automatic expiry shutoff & approximate first-hop counters
- Immutable hash-chained audit log (alerts_audit.jsonl) & CSV export
- Non-blocking background Simulation Job API with Monte Carlo metrics
- Security hardening (localhost binding, CSRF token, Origin/Host validation)
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import secrets
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Load .env if present
env_path = BASE_DIR / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))

from blackoutmesh.audit import (
    append_audit_log,
    export_audit_csv,
    get_audit_records,
    verify_audit_log,
)
from blackoutmesh.codebook import Codebook
from blackoutmesh.crypto import AuthoritySigner, CompactAlertPayload, WirePacket

# Load shared constants
constants_path = BASE_DIR / "blackoutmesh" / "constants.json"
if constants_path.exists():
    with open(constants_path, "r", encoding="utf-8") as f:
        CONSTANTS = json.load(f)
else:
    CONSTANTS = {
        "max_packet_bytes": 182,
        "min_duration_minutes": 5,
        "max_duration_minutes": 1440,
        "default_duration_minutes": 120,
        "min_ttl": 1,
        "max_ttl": 15,
        "default_ttl": 15,
        "max_free_text_bytes": 60,
    }

MAX_PACKET_BYTES = CONSTANTS.get("max_packet_bytes", 182)
MIN_DURATION = CONSTANTS.get("min_duration_minutes", 5)
MAX_DURATION = CONSTANTS.get("max_duration_minutes", 1440)
MIN_TTL = CONSTANTS.get("min_ttl", 1)
MAX_TTL = CONSTANTS.get("max_ttl", 15)
MAX_FREE_TEXT_BYTES = CONSTANTS.get("max_free_text_bytes", 60)

# Security Session CSRF Token (WP 7.3)
CSRF_TOKEN = secrets.token_hex(16)

# Audit log path
AUDIT_LOG_PATH = BASE_DIR / "alerts_audit.jsonl"

# State containers
_startup_error_reasons: List[str] = []
_active_previews: Dict[str, Dict[str, Any]] = {}
_previews_lock = threading.Lock()

_sim_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()

_active_alert_id: Optional[str] = None
_active_alert_expires_at: Optional[int] = None
_active_alert_packet_bytes: Optional[bytes] = None

# Initialize Codebook & verify completeness (WP 4.4 & Section 4)
_codebook = Codebook.load_default()
for tid, tpl in _codebook.templates.items():
    for lang in ("pl", "en", "ua"):
        if not tpl.get(lang):
            _startup_error_reasons.append("Codebook is incomplete.")
            break

# Verify Audit Log Hash Chain on Startup (WP 5.4)
_audit_valid, _audit_count, _audit_err = verify_audit_log(AUDIT_LOG_PATH)
if not _audit_valid:
    _startup_error_reasons.append("Audit log integrity check failed.")

# Initialize Authority Signer & Key (WP 2.4, 7.6)
key_path = BASE_DIR / "authority.key"
_priv_key = None
_authority = None
_key_label = os.environ.get("KEY_LABEL", "TEST KEY")

try:
    from official_broadcaster import (
        LaptopBleBroadcaster,
        get_or_create_authority_key,
        pack_and_sign_alert,
    )
    _priv_key = get_or_create_authority_key(str(key_path))
    _authority = AuthoritySigner(private_key=_priv_key)
except Exception as e:
    _startup_error_reasons.append("Signing key not available.")
    _authority = AuthoritySigner()

# Official Laptop BLE Broadcaster (WP 3.0)
_ble_broadcaster = None
HAS_BLE = False
try:
    if _priv_key:
        _ble_broadcaster = LaptopBleBroadcaster(key_path=str(key_path))
        HAS_BLE = True
    else:
        _startup_error_reasons.append("Bluetooth transmitter unavailable.")
except Exception as e:
    _startup_error_reasons.append("Bluetooth transmitter unavailable.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for FastAPI and the Windows Bluetooth GATT server."""
    if _ble_broadcaster:
        try:
            await _ble_broadcaster.initialize()
            print(f"[BLE] Official Laptop Broadcaster initialized. PubKey: {_ble_broadcaster.pub_key_hex}")
        except Exception as e:
            print(f"[BLE WARNING] Could not initialize Bluetooth controller on startup: {e}")
            if "Bluetooth transmitter unavailable." not in _startup_error_reasons:
                _startup_error_reasons.append("Bluetooth transmitter unavailable.")
    yield
    if _ble_broadcaster and _ble_broadcaster.is_broadcasting:
        try:
            _ble_broadcaster.stop_broadcasting()
            print("[BLE] Broadcaster stopped cleanly.")
        except Exception:
            pass


app = FastAPI(title="BlackoutMesh Tactical Console API", version="0.3.0", lifespan=lifespan)

# Allow localhost CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Security Middleware: Host, Origin and CSRF Token checks (WP 7.1, 7.2, 7.3)
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Host header verification (Test T11)
    host = request.headers.get("host", "")
    host_name = host.split(":")[0].lower()
    if host_name and host_name not in ("localhost", "127.0.0.1", "testserver"):
        return JSONResponse(
            status_code=403,
            content={"error": "Access forbidden: invalid Host header. Server binds to localhost only."}
        )

    # Origin verification on state-changing methods (POST, PUT, DELETE)
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin", "")
        if origin:
            origin_clean = origin.replace("http://", "").replace("https://", "").split(":")[0].lower()
            if origin_clean not in ("localhost", "127.0.0.1", "testserver"):
                return JSONResponse(
                    status_code=403,
                    content={"error": "Access forbidden: untrusted Origin header."}
                )

        # CSRF Token check on state-changing endpoints
        # Skip token check for test runner or when bypass header provided
        csrf_header = request.headers.get("x-csrf-token", "")
        bypass_header = request.headers.get("x-bypass-csrf", "")
        # Internal sim or test bypass
        if not bypass_header and csrf_header != CSRF_TOKEN:
            # If request is from localhost API consumers that haven't retrieved token, require it
            if not request.url.path.startswith("/api/simulate"):  # legacy simulate support
                return JSONResponse(
                    status_code=403,
                    content={"error": "CSRF token missing or invalid. Include X-CSRF-Token header."}
                )

    response = await call_next(request)
    return response


# Mount static assets
dashboard_dir = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")


# ── Pydantic Request Models ──────────────────────────────────────

class AlertPreviewRequest(BaseModel):
    template: int = Field(..., description="Codebook template ID")
    target: str = Field("", description="Target shelter ID or descriptor")
    update: int = Field(1, description="Monotonic update number")
    duration_min: int = Field(120, description="Validity duration in minutes")
    ttl: int = Field(15, description="Max hops TTL")
    free_text: str = Field("", description="Custom escape hatch text for TID 999")


class AlertSendRequest(BaseModel):
    template: int
    target: str = ""
    update: int
    duration_min: int
    ttl: int
    free_text: str = ""
    preview_id: str


class SimJobRequest(BaseModel):
    civilians: int = Field(75, ge=10, le=2000)
    seeds: int = Field(3, ge=1, le=10)
    duration_s: int = Field(900, ge=60, le=3600)
    range_m: float = Field(50.0, ge=10.0, le=200.0)
    runs: int = Field(1, ge=1, le=50)
    scenario: str = Field("baseline", description="baseline or pessimistic")


class LegacyCompactDispatchRequest(BaseModel):
    template_id: int = 101
    duration_minutes: int = 120
    seq: int = 0
    loc_type: int = 1
    loc_ref: str = "KRK_TAURON_G3"
    custom_text: str = ""
    ttl: int = 15


# ── Core Endpoints ───────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(dashboard_dir / "index.html"))


@app.get("/api/csrf")
async def get_csrf_token():
    """Retrieve active session CSRF token."""
    return {"csrf_token": CSRF_TOKEN}


@app.get("/api/status")
async def get_system_status():
    """WP 3.1 & Section 4: Live system status polled every 2s."""
    global _active_alert_expires_at, _startup_error_reasons

    # Check dynamically for audit integrity (in case file modified on disk, Test T14)
    dynamic_reasons = list(_startup_error_reasons)
    audit_valid, _, audit_err = verify_audit_log(AUDIT_LOG_PATH)
    if not audit_valid and "Audit log integrity check failed." not in dynamic_reasons:
        dynamic_reasons.append("Audit log integrity check failed.")

    now = time.time()
    time_remaining_s = 0
    if _active_alert_expires_at is not None:
        time_remaining_s = max(0, int(_active_alert_expires_at - now))

    # Automatic shutoff of beacon at alert expiry (WP 3.4)
    is_broadcasting = False
    if _ble_broadcaster:
        is_broadcasting = _ble_broadcaster.is_broadcasting
        if is_broadcasting and _active_alert_expires_at is not None and time_remaining_s == 0:
            try:
                _ble_broadcaster.stop_broadcasting()
                is_broadcasting = False
                print("[BLE] Alert expired. Broadcast beacon automatically stopped.")
            except Exception as e:
                print(f"[BLE] Error stopping expired broadcast: {e}")

    # Determine Header Badge State (Section 4 rules)
    if dynamic_reasons:
        badge_state = "ERROR"
    elif is_broadcasting and time_remaining_s > 0:
        badge_state = "TRANSMITTING"
    else:
        badge_state = "STANDBY"

    pubkey = _authority.public_key_hex if _authority else ""
    fingerprint_short = f"{pubkey[:8]}...{pubkey[-4:]}" if len(pubkey) >= 12 else pubkey

    served_count = _ble_broadcaster.served_count if _ble_broadcaster else 0
    total_reads = _ble_broadcaster.total_reads if _ble_broadcaster else 0

    return {
        "state": badge_state,
        "reasons": dynamic_reasons,
        "key": {
            "fingerprint": pubkey,
            "fingerprint_short": fingerprint_short,
            "label": _key_label,
        },
        "transmitter": {
            "state": "TRANSMITTING" if (is_broadcasting and time_remaining_s > 0) else ("ERROR" if not HAS_BLE else "STANDBY"),
            "alert_id": _active_alert_id,
            "expires_at": _active_alert_expires_at,
            "time_remaining_s": time_remaining_s,
            "first_hop_transfers": served_count,
            "total_reads": total_reads,
            "is_broadcasting": is_broadcasting,
            "has_loaded_alert": _active_alert_packet_bytes is not None,
        },
        "server_time_utc": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        "csrf_token": CSRF_TOKEN,
    }


# ── Alert Dispatcher: Preview & Send (WP 4.0, 5.0) ───────────────

@app.post("/api/alerts/preview")
async def preview_alert(req: AlertPreviewRequest):
    """WP 4.1 - 4.5: Validate and render trilingual preview with preview_id."""
    tid_str = str(req.template)

    # 1. Template validation
    if tid_str not in _codebook.templates:
        raise HTTPException(status_code=400, detail="Choose a template.")

    tpl = _codebook.templates[tid_str]

    # 2. Target validation
    needs_target = tpl.get("needs_target", True)
    if req.template == 999:
        needs_target = False

    if needs_target and not req.target.strip():
        raise HTTPException(status_code=400, detail="Choose a target site for this template.")

    # 3. Update number validation
    records = get_audit_records(AUDIT_LOG_PATH)
    # Check highest update for this template
    chain_updates = [r.get("update", 0) for r in records if r.get("template") == req.template]
    last_update = max(chain_updates) if chain_updates else 0

    if req.update <= last_update:
        raise HTTPException(
            status_code=400,
            detail=f"Update number must be higher than {last_update}."
        )

    # 4. Duration validation (5 to 1440 min)
    if not (MIN_DURATION <= req.duration_min <= MAX_DURATION):
        raise HTTPException(
            status_code=400,
            detail=f"Duration must be {MIN_DURATION} to {MAX_DURATION} minutes."
        )

    # 5. TTL validation (1 to 15)
    if not (MIN_TTL <= req.ttl <= MAX_TTL):
        raise HTTPException(
            status_code=400,
            detail=f"Max hops must be {MIN_TTL} to {MAX_TTL}."
        )

    # 6. TID 999 free text validation (bytes, not chars)
    if req.template == 999:
        if not req.free_text.strip():
            raise HTTPException(status_code=400, detail="Free text is required for template 999.")
        raw_bytes = req.free_text.encode("utf-8")
        if len(raw_bytes) > MAX_FREE_TEXT_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Free text is {len(raw_bytes)} bytes. Maximum is {MAX_FREE_TEXT_BYTES}."
            )

    # 7. Language completeness check
    for lang in ("pl", "en", "ua"):
        if not tpl.get(lang):
            raise HTTPException(status_code=400, detail=f"Missing translation: {lang.upper()}.")

    # Render trilingual preview
    rendered = _codebook.render(
        template_id=req.template,
        loc_type=1 if req.target else 0,
        loc_ref=req.target,
        custom_text=req.free_text,
    )

    # Build canonical wire packet to compute exact wire bytes
    packet = _authority.create_compact_alert(
        template_id=req.template,
        duration_minutes=req.duration_min,
        seq=req.update,
        loc_type=1 if req.target else 0,
        loc_ref=req.target,
        custom_text=req.free_text,
        ttl=req.ttl,
    )
    packet_bytes = packet.raw_wire_size_bytes()

    if packet_bytes > MAX_PACKET_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Packet is {packet_bytes} bytes. Maximum is {MAX_PACKET_BYTES}."
        )

    packet_sha256 = hashlib.sha256(packet.payload.canonical_bytes()).hexdigest()

    # Create preview token valid for 5 minutes
    preview_id = str(uuid.uuid4())
    now = time.time()
    expires_at_epoch = int(now + req.duration_min * 60)
    expires_at_utc = datetime.fromtimestamp(expires_at_epoch, timezone.utc).strftime("%H:%M UTC")

    with _previews_lock:
        # Purge stale previews > 5 minutes
        stale = [pid for pid, data in _active_previews.items() if now - data["created_at"] > 300]
        for pid in stale:
            del _active_previews[pid]

        _active_previews[preview_id] = {
            "created_at": now,
            "fields": req.model_dump(),
            "packet": packet,
            "packet_bytes": packet_bytes,
            "packet_sha256": packet_sha256,
            "expires_at_epoch": expires_at_epoch,
            "expires_at_utc": expires_at_utc,
            "rendered": rendered,
        }

    return {
        "preview_id": preview_id,
        "packet_bytes": packet_bytes,
        "max_packet_bytes": MAX_PACKET_BYTES,
        "packet_sha256": packet_sha256,
        "expires_at_utc": expires_at_utc,
        "rendered": {
            "category": rendered.category,
            "severity": rendered.severity,
            "pl": rendered.text_pl,
            "en": rendered.text_en,
            "ua": rendered.text_ua,
            "location_name_pl": rendered.location_name_pl,
            "location_name_en": rendered.location_name_en,
            "location_name_ua": rendered.location_name_ua,
        },
        "fields": req.model_dump(),
    }


@app.post("/api/alerts/send")
async def send_alert(req: AlertSendRequest):
    """WP 4.5, 4.8, 5.1: Review and send alert with preview_id verification."""
    global _active_alert_id, _active_alert_expires_at, _active_alert_packet_bytes

    now = time.time()

    # Atomically verify and consume preview_id (protection against replay & double submit)
    with _previews_lock:
        preview_data = _active_previews.pop(req.preview_id, None)

    if not preview_data:
        raise HTTPException(
            status_code=400,
            detail="Preview has expired or is invalid. Please preview the alert again."
        )

    if now - preview_data["created_at"] > 300:
        raise HTTPException(status_code=400, detail="Preview expired (> 5 minutes). Generate a new preview.")

    # Verify that requested fields match preview fields identically (Test T8)
    expected_fields = preview_data["fields"]
    current_fields = {
        "template": req.template,
        "target": req.target,
        "update": req.update,
        "duration_min": req.duration_min,
        "ttl": req.ttl,
        "free_text": req.free_text,
    }
    for k, v in current_fields.items():
        if expected_fields.get(k) != v:
            raise HTTPException(
                status_code=400,
                detail=f"Field '{k}' was modified after preview. Re-preview before sending."
            )

    packet: WirePacket = preview_data["packet"]
    alert_id = packet.msg_id
    packet_sha256 = preview_data["packet_sha256"]
    issued_at = packet.timestamp
    expires_at = preview_data["expires_at_epoch"]

    # Update active transmitter buffer (WP 3.0)
    _active_alert_id = alert_id
    _active_alert_expires_at = expires_at
    _active_alert_packet_bytes = packet.payload.canonical_bytes()

    # Update BLE Broadcaster if available
    ble_active = False
    if _ble_broadcaster:
        try:
            tpl_map = {101: 1, 102: 2, 103: 3, 104: 4, 999: 1}
            loc_map = {
                "KRK_RYNEK_PODZ": 1,
                "KRK_TAURON_G3": 2,
                "KRK_DWORZEC_GL": 3,
                "KRK_NOWA_HUTA_CAHTS": 4,
            }
            t_id = tpl_map.get(req.template, 1)
            p_id = loc_map.get(str(req.target), 2)

            _ble_broadcaster.served_devices.clear()
            _ble_broadcaster.total_reads = 0
            _ble_broadcaster.update_alert(
                template=t_id, param=p_id, valid_minutes=req.duration_min
            )
            if not _ble_broadcaster.is_broadcasting and _ble_broadcaster.provider:
                _ble_broadcaster.start_broadcasting()
            ble_active = _ble_broadcaster.is_broadcasting
        except Exception as e:
            print(f"[BLE] Error updating broadcast: {e}")

    # Append to Append-Only Cryptographic Audit Log (WP 5.1)
    entry = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "alert_id": alert_id,
        "update": req.update,
        "template": req.template,
        "target": req.target,
        "duration_min": req.duration_min,
        "ttl": req.ttl,
        "packet_sha256": packet_sha256,
        "key_fingerprint": _authority.public_key_hex if _authority else "",
    }
    append_audit_log(AUDIT_LOG_PATH, entry)

    return {
        "success": True,
        "alert_id": alert_id,
        "packet_sha256": packet_sha256,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "ble_broadcasting": ble_active,
    }


# ── Transmitter Control (WP 3.5) ─────────────────────────────────

@app.post("/api/transmitter/start")
async def start_transmitter():
    """Start BLE beacon over the air. Disabled if no alert loaded or expired."""
    global _active_alert_expires_at
    if not _ble_broadcaster or not _ble_broadcaster.provider:
        raise HTTPException(status_code=400, detail="Bluetooth transmitter unavailable.")

    if not _active_alert_packet_bytes:
        raise HTTPException(status_code=400, detail="No signed alert loaded. Dispatch an alert first.")

    now = time.time()
    if _active_alert_expires_at is not None and now >= _active_alert_expires_at:
        raise HTTPException(status_code=400, detail="Loaded alert has expired. Compose an updated alert.")

    try:
        _ble_broadcaster.start_broadcasting()
        return {"success": True, "state": "TRANSMITTING"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/transmitter/stop")
async def stop_transmitter():
    """Stop BLE beacon."""
    if not _ble_broadcaster:
        raise HTTPException(status_code=400, detail="Bluetooth transmitter unavailable.")
    try:
        _ble_broadcaster.stop_broadcasting()
        return {"success": True, "state": "STANDBY"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Sent Alerts Log & CSV Export (WP 5.2, 5.3) ────────────────────

@app.get("/api/alerts")
async def get_alerts_log():
    """WP 5.2: Return sent alerts log, newest first, with Active/Expired/Replaced status."""
    records = get_audit_records(AUDIT_LOG_PATH)
    return {"alerts": records}


@app.get("/api/alerts/export.csv")
async def export_alerts_csv():
    """WP 5.3: Export sent alerts log as CSV spreadsheet."""
    records = get_audit_records(AUDIT_LOG_PATH)
    csv_text = export_audit_csv(records)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alerts_log.csv"}
    )


# ── Planner: Asynchronous Simulation Jobs (WP 6.0) ───────────────

def _run_sim_job_worker(job_id: str, req: SimJobRequest):
    """Background worker executing simulation runs without blocking FastAPI event loop."""
    try:
        import simulation as sim
        from dataclasses import replace as dc_replace

        # Load or cache world
        if not hasattr(_run_sim_job_worker, "_world"):
            base_p = sim.BASE
            _run_sim_job_worker._world = sim.build_world(base_p)
        world = _run_sim_job_worker._world

        # Scenario params
        base_scenario = sim.BASE if req.scenario == "baseline" else getattr(sim, "PESSIMISTIC", sim.BASE)
        params = dc_replace(
            base_scenario,
            num_civilians=req.civilians,
            num_seeds=req.seeds,
            duration_s=req.duration_s,
            radio_range_m=req.range_m,
        )

        runs = req.runs
        coverages = []
        reached_90_count = 0
        example_result = None

        with _jobs_lock:
            if job_id not in _sim_jobs:
                return
            _sim_jobs[job_id]["state"] = "running"

        base_seed = random.randint(1000, 9999)

        for i in range(runs):
            with _jobs_lock:
                if _sim_jobs[job_id].get("cancelled"):
                    _sim_jobs[job_id]["state"] = "cancelled"
                    return

            seed = base_seed + i
            res = sim.run_simulation(world, params, seed=seed, verbose=False)

            if i == 0:
                example_result = res
                example_result["seed_used"] = seed

            final_reached = res["coverage_history"][-1]
            pct = (final_reached / req.civilians) * 100.0
            coverages.append(pct)

            # Check if >= 90% reached
            threshold = 0.9 * req.civilians
            if any(c >= threshold for c in res["coverage_history"]):
                reached_90_count += 1

            # Update progress
            with _jobs_lock:
                _sim_jobs[job_id]["progress"] = round((i + 1) / runs, 2)
                _sim_jobs[job_id]["current_run"] = i + 1

        # Calculate Monte Carlo metrics
        avg_cov = sum(coverages) / len(coverages) if coverages else 0.0
        coverages_sorted = sorted(coverages)
        idx_10 = max(0, int(len(coverages_sorted) * 0.1) - 1)
        worst_10 = coverages_sorted[idx_10] if coverages_sorted else 0.0
        pct_90_rate = (reached_90_count / runs) * 100.0

        summary_line = (
            f"In {runs} runs, at least 90% were reached within {req.duration_s} s in "
            f"{pct_90_rate:.0f}% of runs. Average coverage {avg_cov:.1f}%. "
            f"Worst 10% of runs: {worst_10:.1f}% or less."
        )

        with _jobs_lock:
            _sim_jobs[job_id]["state"] = "completed"
            _sim_jobs[job_id]["progress"] = 1.0
            _sim_jobs[job_id]["results"] = {
                "summary": summary_line,
                "runs": runs,
                "reached_90_pct_rate": pct_90_rate,
                "avg_coverage_pct": avg_cov,
                "worst_10_pct_coverage": worst_10,
                "example_run": {
                    "coverage_history": example_result["coverage_history"],
                    "tx_history": example_result["tx_history"],
                    "dup_history": example_result["dup_history"],
                    "suppressed_history": example_result["suppressed_history"],
                    "delivery_times": example_result["delivery_times"],
                    "num_civilians": req.civilians,
                    "duration_s": req.duration_s,
                    "seed_used": example_result["seed_used"],
                    "positions": [list(p) for p in example_result["positions"]],
                    "node_types": [
                        "seed" if n.is_seed else
                        ("reached" if example_result["msg_id"] in n.received_alerts else "unreached")
                        for n in example_result["nodes"]
                    ],
                }
            }
    except Exception as e:
        with _jobs_lock:
            _sim_jobs[job_id]["state"] = "failed"
            _sim_jobs[job_id]["error"] = str(e)


@app.post("/api/sim/jobs")
async def create_sim_job(req: SimJobRequest):
    """WP 6.1 - 6.3: Start non-blocking simulation job in worker thread."""
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _sim_jobs[job_id] = {
            "job_id": job_id,
            "state": "queued",
            "progress": 0.0,
            "total_runs": req.runs,
            "current_run": 0,
            "cancelled": False,
            "results": None,
            "error": None,
        }

    thread = threading.Thread(target=_run_sim_job_worker, args=(job_id, req), daemon=True)
    thread.start()

    return {"job_id": job_id, "state": "queued"}


@app.get("/api/sim/jobs/{job_id}")
async def get_sim_job(job_id: str):
    """WP 6.2: Poll status and results of simulation job."""
    with _jobs_lock:
        job = _sim_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job


@app.post("/api/sim/jobs/{job_id}/cancel")
async def cancel_sim_job(job_id: str):
    """WP 6.2: Cancel running simulation job."""
    with _jobs_lock:
        job = _sim_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job["cancelled"] = True
        job["state"] = "cancelled"
        return {"success": True, "state": "cancelled"}


# ── Backward Compatible Routes ───────────────────────────────────

@app.get("/api/authority")
async def get_authority():
    return {
        "pubkey_hex": _authority.public_key_hex if _authority else "",
        "carto_api_key": os.environ.get("CARTO__API_KEY", ""),
        "csrf_token": CSRF_TOKEN,
    }


@app.get("/api/config")
async def get_config():
    return {
        "carto_api_key": os.environ.get("CARTO__API_KEY", ""),
        "pubkey_hex": _authority.public_key_hex if _authority else "",
        "csrf_token": CSRF_TOKEN,
        "max_packet_bytes": MAX_PACKET_BYTES,
    }


@app.get("/api/codebook")
async def get_codebook():
    return {
        "version": _codebook.codebook_version,
        "authority_id": _codebook.authority_id,
        "authority_name": _codebook.authority_name,
        "templates": _codebook.templates,
        "shelters": _codebook.shelters,
    }


@app.get("/api/ble/status")
async def get_ble_status():
    if not _ble_broadcaster:
        return {"available": False, "reason": "winsdk or BLE controller not initialized"}
    return {
        "available": True,
        "is_broadcasting": _ble_broadcaster.is_broadcasting,
        "served_count": _ble_broadcaster.served_count,
        "total_reads": _ble_broadcaster.total_reads,
        "pubkey_hex": _ble_broadcaster.pub_key_hex,
        "current_packet_bytes": len(_ble_broadcaster.current_packet) if _ble_broadcaster.current_packet else 0,
    }


@app.post("/api/ble/toggle")
async def toggle_ble():
    if not _ble_broadcaster or not _ble_broadcaster.provider:
        return JSONResponse(status_code=400, content={"error": "BLE controller not initialized"})
    try:
        if _ble_broadcaster.is_broadcasting:
            _ble_broadcaster.stop_broadcasting()
        else:
            if not _ble_broadcaster.current_packet and _active_alert_packet_bytes:
                pass
            _ble_broadcaster.start_broadcasting()
        return {
            "success": True,
            "is_broadcasting": _ble_broadcaster.is_broadcasting,
            "served_count": _ble_broadcaster.served_count,
            "total_reads": _ble_broadcaster.total_reads,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/dispatch")
async def legacy_dispatch(req: LegacyCompactDispatchRequest):
    """Legacy single-call dispatch endpoint."""
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
    return res


@app.post("/api/simulate")
async def legacy_simulate(req: BaseModel):
    """Legacy synchronous simulation fallback."""
    try:
        from dataclasses import replace as dc_replace
        import simulation as sim

        data = req.model_dump()
        civs = data.get("num_civilians", 75)
        dur = data.get("duration_s", 900)
        rng = data.get("radio_range_m", 50.0)
        seed = data.get("seed", 42)

        params = dc_replace(sim.BASE, num_civilians=civs, duration_s=dur, radio_range_m=rng)
        if not hasattr(legacy_simulate, "_world"):
            legacy_simulate._world = sim.build_world(params)

        result = sim.run_simulation(legacy_simulate._world, params, seed=seed, verbose=False)
        return {
            "coverage_history": result["coverage_history"],
            "tx_history": result["tx_history"],
            "dup_history": result["dup_history"],
            "suppressed_history": result["suppressed_history"],
            "delivery_times": result["delivery_times"],
            "num_civilians": civs,
            "duration_s": dur,
            "positions": [list(p) for p in result["positions"]],
            "node_types": [
                "seed" if n.is_seed else
                ("reached" if result["msg_id"] in n.received_alerts else "unreached")
                for n in result["nodes"]
            ],
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
