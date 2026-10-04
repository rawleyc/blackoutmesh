"""
WP 5.0 — Sent Alerts Log (Cryptographic Audit Chain)

Maintains an append-only JSONL audit log of all broadcast alerts.
Each line includes:
  time_utc, alert_id, update, template, target, duration_min, ttl,
  packet_sha256, key_fingerprint, and prev_hash (SHA-256 of previous line).
Genesis line uses prev_hash = "0" * 64.
"""

import csv
import hashlib
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

GENESIS_HASH = "0" * 64


def verify_audit_log(log_path: Path) -> Tuple[bool, int, Optional[str]]:
    """Verify hash chain integrity of the audit log file.

    Returns:
        (is_valid, record_count, error_message)
    """
    if not log_path.exists():
        return True, 0, None

    try:
        content = log_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, 0, f"Cannot read audit log: {e}"

    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return True, 0, None

    prev_line_raw = None
    for idx, line_str in enumerate(lines):
        try:
            record = json.loads(line_str)
        except json.JSONDecodeError:
            return False, idx, f"Corrupted JSON at line {idx + 1}"

        if idx == 0:
            expected_prev = GENESIS_HASH
        else:
            expected_prev = hashlib.sha256(prev_line_raw.encode("utf-8")).hexdigest()

        if record.get("prev_hash") != expected_prev:
            return False, idx, f"Audit log integrity check failed at line {idx + 1}"

        prev_line_raw = line_str

    return True, len(lines), None


def append_audit_log(log_path: Path, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Append a verified alert entry to the append-only audit log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
        lines = [line for line in content.splitlines() if line.strip()]

    if not lines:
        prev_hash = GENESIS_HASH
    else:
        prev_hash = hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()

    record = {
        "time_utc": entry.get("time_utc", datetime.now(timezone.utc).isoformat()),
        "alert_id": str(entry.get("alert_id", "")),
        "update": int(entry.get("update", 0)),
        "template": int(entry.get("template", 101)),
        "target": str(entry.get("target", "")),
        "duration_min": int(entry.get("duration_min", 120)),
        "ttl": int(entry.get("ttl", 15)),
        "packet_sha256": str(entry.get("packet_sha256", "")),
        "key_fingerprint": str(entry.get("key_fingerprint", "")),
        "prev_hash": prev_hash,
    }

    serialized = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(serialized + "\n")

    return record


def get_audit_records(log_path: Path) -> List[Dict[str, Any]]:
    """Return all audit records annotated with Active/Expired/Replaced status, newest first."""
    if not log_path.exists():
        return []

    try:
        content = log_path.read_text(encoding="utf-8")
    except Exception:
        return []

    lines = [line for line in content.splitlines() if line.strip()]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            continue

    now = time.time()

    # Track highest update seen per alert chain (key by template + target)
    max_update_by_chain: Dict[Tuple[int, str], int] = {}
    for r in records:
        chain_key = (r.get("template", 0), r.get("target", ""))
        update_num = r.get("update", 0)
        if update_num > max_update_by_chain.get(chain_key, -1):
            max_update_by_chain[chain_key] = update_num

    annotated = []
    for r in records:
        chain_key = (r.get("template", 0), r.get("target", ""))
        highest_update = max_update_by_chain.get(chain_key, 0)
        update_num = r.get("update", 0)

        # Parse timestamp
        try:
            ts = datetime.fromisoformat(r["time_utc"]).timestamp()
        except Exception:
            ts = now

        duration_sec = r.get("duration_min", 120) * 60
        is_expired = now > (ts + duration_sec)

        if update_num < highest_update:
            status = "Replaced by a higher update"
        elif is_expired:
            status = "Expired"
        else:
            status = "Active"

        item = dict(r)
        item["status"] = status
        annotated.append(item)

    # Return newest first
    annotated.reverse()
    return annotated


def export_audit_csv(records: List[Dict[str, Any]]) -> str:
    """Format audit records into CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Time (UTC)",
        "Alert ID",
        "Update Number",
        "Template",
        "Target Site",
        "Duration (min)",
        "Max Hops (TTL)",
        "Packet SHA-256",
        "Key Fingerprint",
        "Status",
    ])
    for r in records:
        writer.writerow([
            r.get("time_utc", ""),
            r.get("alert_id", ""),
            r.get("update", 0),
            r.get("template", ""),
            r.get("target", ""),
            r.get("duration_min", ""),
            r.get("ttl", ""),
            r.get("packet_sha256", ""),
            r.get("key_fingerprint", ""),
            r.get("status", ""),
        ])
    return output.getvalue()
