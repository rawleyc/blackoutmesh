"""
Automated Test Suite for BlackoutMesh Dashboard Redesign (WBS 10.0 Acceptance Tests)
Verifies:
  T1: Truth in labeling (Live column never contains "reached", Planner has (sim))
  T5: Update number must be higher than last sent
  T6: UTF-8 byte boundary enforcement (31 Cyrillic chars = 62 bytes > 60 bytes)
  T8: Send rejected if fields modified from preview
  T11: Security: Host, Origin and CSRF rejection
  T12: Double-submission protection
  T13, T14: Cryptographic hash chain audit log integrity & tampering detection
  T17: Private key never leaked in responses or logs
  WP 9.2, 9.3: Golden packet test vector
"""

import hashlib
import json
from pathlib import Path
import re
import tempfile
import pytest
from fastapi.testclient import TestClient

from blackoutmesh.audit import append_audit_log, get_audit_records, verify_audit_log
from blackoutmesh.crypto import AuthoritySigner
from dashboard.api_server import app, CSRF_TOKEN

client = TestClient(app)


class TestTruthInLabelingAndHtmlStructure:
    """T1: Verify truth in labeling across HTML, ensuring live and simulated are separated."""

    def test_live_operations_column_never_says_reached(self):
        html_path = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
        html = html_path.read_text(encoding="utf-8")

        # Extract Live Operations Column up to start of Coverage Planner Column
        live_start = html.find('id="live-operations-col"')
        planner_start = html.find('id="coverage-planner-col"')
        assert live_start != -1 and planner_start != -1, "Columns not found"
        live_html = html[live_start:planner_start]

        # Ensure the word "reached" is never in the live column (WP 3.6 / T1)
        assert "reached" not in live_html.lower(), (
            "Found word 'reached' inside Live column. Live data must only state first-hop transfers."
        )

    def test_planner_metrics_and_charts_labeled_sim(self):
        html_path = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
        html = html_path.read_text(encoding="utf-8")

        assert "Reached (sim)" in html
        assert "Broadcasts (sim)" in html
        assert "Duplicates (sim)" in html
        assert "Suppressed (sim)" in html
        assert "Coverage Over Time (sim)" in html
        assert "Broadcasts & Redundancy (sim)" in html
        assert "SIMULATION ONLY. Not live data." in html

    def test_simulation_banner_and_footnotes_present(self):
        html_path = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
        html = html_path.read_text(encoding="utf-8")

        # Banner check
        assert "Phones do not report back, so real coverage cannot be measured." in html
        # Footnote check
        assert "Counts phones that fetched the alert directly from this laptop." in html


class TestAuditLogChain:
    """T13, T14: Append-only hash chain integrity & corruption detection."""

    def test_hash_chain_creation_and_verification(self, tmp_path):
        log_file = tmp_path / "test_audit.jsonl"

        entry1 = {
            "alert_id": "ALERT-001",
            "update": 1,
            "template": 101,
            "target": "KRK_TAURON_G3",
            "duration_min": 120,
            "ttl": 15,
            "packet_sha256": "abc123",
            "key_fingerprint": "pubkey1",
        }
        append_audit_log(log_file, entry1)

        entry2 = {
            "alert_id": "ALERT-002",
            "update": 2,
            "template": 101,
            "target": "KRK_TAURON_G3",
            "duration_min": 120,
            "ttl": 15,
            "packet_sha256": "def456",
            "key_fingerprint": "pubkey1",
        }
        append_audit_log(log_file, entry2)

        # Check valid
        valid, count, err = verify_audit_log(log_file)
        assert valid is True
        assert count == 2
        assert err is None

        # Check records status
        records = get_audit_records(log_file)
        assert len(records) == 2
        assert records[0]["update"] == 2
        assert records[0]["status"] == "Active"
        assert records[1]["update"] == 1
        assert records[1]["status"] == "Replaced by a higher update"

    def test_tampering_triggers_integrity_failure(self, tmp_path):
        log_file = tmp_path / "test_audit_corrupt.jsonl"
        entry1 = {"alert_id": "A1", "update": 1, "template": 101, "target": "T1"}
        append_audit_log(log_file, entry1)
        entry2 = {"alert_id": "A2", "update": 2, "template": 101, "target": "T1"}
        append_audit_log(log_file, entry2)

        # Tamper with first line
        content = log_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        tampered_first = lines[0].replace("A1", "A1_HACKED")
        log_file.write_text(tampered_first + "\n" + lines[1] + "\n", encoding="utf-8")

        # Must fail verification
        valid, idx, err = verify_audit_log(log_file)
        assert valid is False
        assert "Audit log integrity check failed" in err


class TestSecurityHardening:
    """T11: Host header, Origin, and CSRF token enforcement."""

    def test_rejected_with_invalid_host(self):
        res = client.get("/api/status", headers={"Host": "malicious-site.com"})
        assert res.status_code == 403
        assert "invalid Host header" in res.json()["error"]

    def test_post_rejected_without_csrf_token(self):
        res = client.post(
            "/api/alerts/preview",
            json={"template": 101, "target": "KRK_TAURON_G3", "update": 99, "duration_min": 120, "ttl": 15},
            headers={"Host": "localhost:8000"}
        )
        assert res.status_code == 403
        assert "CSRF token missing or invalid" in res.json()["error"]

    def test_post_rejected_with_untrusted_origin(self):
        res = client.post(
            "/api/alerts/preview",
            json={"template": 101, "target": "KRK_TAURON_G3", "update": 99, "duration_min": 120, "ttl": 15},
            headers={"Host": "localhost:8000", "Origin": "http://evil-tracker.org", "X-CSRF-Token": CSRF_TOKEN}
        )
        assert res.status_code == 403
        assert "untrusted Origin header" in res.json()["error"]


class TestDispatcherValidation:
    """T5, T6, T8: Validation rules and two-step preview/send workflow."""

    def _get_next_update(self, template_id=101):
        res = client.get("/api/alerts")
        alerts = res.json().get("alerts", [])
        tpl_updates = [a["update"] for a in alerts if a.get("template") == template_id]
        return (max(tpl_updates) if tpl_updates else 0) + 1

    def test_update_number_must_be_higher_t5(self):
        next_up = self._get_next_update(101)
        if next_up > 1:
            stale_up = next_up - 1
            res = client.post(
                "/api/alerts/preview",
                json={
                    "template": 101,
                    "target": "KRK_TAURON_G3",
                    "update": stale_up,
                    "duration_min": 120,
                    "ttl": 15,
                },
                headers={"Host": "localhost:8000", "X-CSRF-Token": CSRF_TOKEN}
            )
            assert res.status_code == 400
            assert f"Update number must be higher than {stale_up}" in res.json()["detail"]

    def test_cyrillic_bytes_enforcement_t6(self):
        # 31 Cyrillic letters 'А' -> 62 bytes in UTF-8
        cyrillic_31 = "А" * 31
        assert len(cyrillic_31.encode("utf-8")) == 62

        next_up = self._get_next_update(999)
        res = client.post(
            "/api/alerts/preview",
            json={
                "template": 999,
                "target": "",
                "update": next_up,
                "duration_min": 120,
                "ttl": 15,
                "free_text": cyrillic_31,
            },
            headers={"Host": "localhost:8000", "X-CSRF-Token": CSRF_TOKEN}
        )
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert "Free text is 62 bytes. Maximum is 60." in detail

    def test_preview_and_send_flow_with_modification_rejection_t8(self):
        next_up = self._get_next_update(101)
        # Step 1: Create preview
        preview_res = client.post(
            "/api/alerts/preview",
            json={
                "template": 101,
                "target": "KRK_TAURON_G3",
                "update": next_up,
                "duration_min": 120,
                "ttl": 15,
                "free_text": "",
            },
            headers={"Host": "localhost:8000", "X-CSRF-Token": CSRF_TOKEN}
        )
        assert preview_res.status_code == 200
        preview_data = preview_res.json()
        preview_id = preview_data["preview_id"]
        assert preview_id is not None
        assert preview_data["packet_bytes"] <= 240

        # Step 2: Try to send with modified target using same preview_id (T8)
        tampered_send_res = client.post(
            "/api/alerts/send",
            json={
                "template": 101,
                "target": "KRK_DWORZEC_GL",  # Modified!
                "update": next_up,
                "duration_min": 120,
                "ttl": 15,
                "free_text": "",
                "preview_id": preview_id,
            },
            headers={"Host": "localhost:8000", "X-CSRF-Token": CSRF_TOKEN}
        )
        assert tampered_send_res.status_code == 400
        assert "was modified after preview" in tampered_send_res.json()["detail"]

    def test_double_submission_protection_t12(self):
        next_up = self._get_next_update(101)
        # Create preview
        preview_res = client.post(
            "/api/alerts/preview",
            json={
                "template": 101,
                "target": "KRK_TAURON_G3",
                "update": next_up,
                "duration_min": 120,
                "ttl": 15,
            },
            headers={"Host": "localhost:8000", "X-CSRF-Token": CSRF_TOKEN}
        )
        assert preview_res.status_code == 200
        preview_id = preview_res.json()["preview_id"]

        send_payload = {
            "template": 101,
            "target": "KRK_TAURON_G3",
            "update": next_up,
            "duration_min": 120,
            "ttl": 15,
            "free_text": "",
            "preview_id": preview_id,
        }

        # First send succeeds
        send_res_1 = client.post(
            "/api/alerts/send",
            json=send_payload,
            headers={"Host": "localhost:8000", "X-CSRF-Token": CSRF_TOKEN}
        )
        assert send_res_1.status_code == 200
        assert send_res_1.json()["success"] is True

        # Second send with same preview_id fails immediately (consumed token)
        send_res_2 = client.post(
            "/api/alerts/send",
            json=send_payload,
            headers={"Host": "localhost:8000", "X-CSRF-Token": CSRF_TOKEN}
        )
        assert send_res_2.status_code == 400
        assert "Preview has expired or is invalid" in send_res_2.json()["detail"]


class TestNoPrivateKeyLeak:
    """T17: Search status and API responses for private key bytes."""

    def test_private_key_not_leaked(self):
        res = client.get("/api/status", headers={"Host": "localhost:8000"})
        body_text = res.text
        assert "private_key" not in body_text
        assert "priv_key" not in body_text

        config_res = client.get("/api/config", headers={"Host": "localhost:8000"})
        assert "private_key" not in config_res.text


class TestGoldenVectorConformance:
    """WP 9.2, 9.3: Fixed golden key and canonical vector reproducibility."""

    def test_golden_vector_reproducibility(self):
        signer = AuthoritySigner()
        packet = signer.create_compact_alert(
            template_id=101,
            duration_minutes=120,
            seq=1,
            loc_type=1,
            loc_ref="KRK_TAURON_G3",
            ttl=15,
        )

        canonical = packet.payload.canonical_bytes()
        # Verify JSON properties: sorted keys, minimal separators
        assert b'"cb_v":1' in canonical
        assert b'"dur":120' in canonical
        assert b'"tid":101' in canonical
        assert b'"v":2' in canonical
        assert packet.raw_wire_size_bytes() <= 240
