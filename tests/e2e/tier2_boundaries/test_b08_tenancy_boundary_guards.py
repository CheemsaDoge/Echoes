"""Tier 2: Boundary B08 - Multi-Tenancy Boundary Guards & Cross-Tenant Breach Prevention.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 1.3 & Section 3.1.
Verifies micro-isolation across URL paths, bearer tokens, vaults, and clusters.
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore


class TestTenancyBoundaryGuards:
    """Verifies strict boundary defenses against cross-tenant and cross-vault access attempts."""

    def test_url_tenant_mismatch_with_token_rejected_403(
        self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes
    ):
        """1. Token is for tenant_alpha, but upload URL targets tenant_beta -> HTTP 403."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("test.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_beta/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 403
        assert "micro-isolation breach" in res.json()["detail"]

    def test_url_vault_mismatch_with_token_rejected_403(
        self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes
    ):
        """2. Token is for vault_primary, but upload URL targets vault_secondary -> HTTP 403."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("test.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_secondary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 403

    def test_get_nonexistent_tenant_rejected_403_or_404(self, client: TestClient, auth_headers: dict):
        """3. Requesting a nonexistent tenant ID returns 403 (for token mismatch) or 404."""
        res = client.get("/tenants/tenant_nonexistent_99", headers=auth_headers)
        assert res.status_code == 403

    def test_foreign_vault_cluster_detail_returns_404(
        self, client: TestClient, store: MemoryStore, auth_headers: dict
    ):
        """4. Accessing a cluster that exists in another vault returns HTTP 404."""
        store.voiceprint_clusters["cls_foreign"] = {
            "cluster_id": "cls_foreign",
            "vault_id": "vault_foreign_99",
            "status": "pending",
        }
        res = client.get("/clusters/cls_foreign", headers=auth_headers)
        assert res.status_code == 404

    def test_foreign_vault_cluster_patch_returns_404(
        self, client: TestClient, store: MemoryStore, auth_headers: dict
    ):
        """5. Mutating a cluster in a foreign vault returns HTTP 404."""
        store.voiceprint_clusters["cls_foreign_2"] = {
            "cluster_id": "cls_foreign_2",
            "vault_id": "vault_foreign_99",
            "status": "pending",
        }
        patch_body = {"action": "dismiss"}
        res = client.patch("/clusters/cls_foreign_2", json=patch_body, headers=auth_headers)
        assert res.status_code == 404
