# ⚡ BlackoutMesh

**Hardened DTN Emergency Alert System for Infrastructure Blackout Scenarios**

Cryptographically signed, Trickle-suppressed (RFC 6206), delay-tolerant emergency alert dissemination over BLE mesh — validated on 12,584 real Kraków pedestrian network nodes.

## Quick Start

### 1. Install Dependencies

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
```

### 2. Run Unit & Adversarial Tests

```bash
pytest
```

### 3. Run Simulation

```bash
python simulation.py
```

### 4. Launch Tactical Dashboard

```bash
python -m uvicorn dashboard.api_server:app --reload --port 8000
```

Then open [http://localhost:8000](http://localhost:8000).

## Architecture

```
┌─────────────────────┐    Ed25519 Signed Packet    ┌──────────────┐
│  Authority HSM      │ ──────────────────────────► │  Seed Device │
│  (Dispatch Console) │                             │  (BLE TX)    │
└─────────────────────┘                             └──────┬───────┘
                                                           │ BLE Adv
                                             ┌─────────────┼─────────────┐
                                             ▼             ▼             ▼
                                       ┌──────────┐ ┌──────────┐ ┌──────────┐
                                       │ Phone A  │ │ Phone B  │ │ Phone C  │
                                       │ Trickle  │ │ Trickle  │ │ Trickle  │
                                       │ Verify→  │ │ Verify→  │ │ Verify→  │
                                       │ Store→TX │ │ Store→TX │ │ Store→TX │
                                       └──────────┘ └──────────┘ └──────────┘
```

## Key Design Decisions

1. **Verify-before-dedup**: Ed25519 signature check runs BEFORE the Trickle duplicate counter, preventing adversarial pre-suppression attacks.
2. **Signed expiry**: `expires_at` is inside the signed canonical payload — replays expire without requiring synchronized clocks.
3. **Trickle RFC 6206**: Adaptive broadcast suppression with $I_{\min}=1\text{s}$, $I_{\max}=16\text{s}$, $K=3$. Resets on neighbor churn for store-carry-forward.
