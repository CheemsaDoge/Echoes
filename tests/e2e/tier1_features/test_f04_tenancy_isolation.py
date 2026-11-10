"""Tier 1: Feature F2.1 - 4-Tier Tenancy Micro-Isolation Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 1.3 & Section 3.1 & ORIGINAL_REQUEST.md R3.
Hierarchy: tenant_id -> vault_id -> user_id -> dp_instance_id.
"""

from __future__ import annotations
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore


class TestTenancyMicroIsolation:
    """Verifies that data access and physical boundaries are strictly quarantined across the 4 tiers."""

    def test_hierarchy_relationship_integrity(self, store: MemoryStore):
        """1. Verify that tenant -> vault -> user -> dp_instance relationships are properly linked."""
        tenant = store.tenants["tenant_alpha"]
        vault = store.vaults["vault_primary"]
        user = store.users["user_001"]
        dp = store.dp_instances["dp_phone_001"]

        assert vault["tenant_id"] == tenant["tenant_id"]
        assert user["tenant_id"] == tenant["tenant_id"]
        assert user["vault_id"] == vault["vault_id"]
        assert dp["user_id"] == user["user_id"]
        assert dp["vault_id"] == vault["vault_id"]

    def test_authorized_tenant_and_vault_retrieval(self, client: TestClient, auth_headers: dict[str, str]):
        """2. User with valid credentials accesses own tenant and vault successfully."""
        res_t = client.get("/tenants/tenant_alpha", headers=auth_headers)
        assert res_t.status_code == 200
        assert res_t.json()["name"] == "Alpha Corp"

        res_v = client.get("/tenants/tenant_alpha/vaults/vault_primary", headers=auth_headers)
        assert res_v.status_code == 200
        assert res_v.json()["name"] == "Primary Vault"

    def test_cross_tenant_access_rejection(self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]):
        """3. Access attempt to a foreign tenant returns 403 Forbidden."""
        # Create second tenant
        store.tenants["tenant_foreign"] = {"tenant_id": "tenant_foreign", "name": "Foreign Corp"}
        store.vaults["vault_foreign"] = {"vault_id": "vault_foreign", "tenant_id": "tenant_foreign", "name": "Foreign Vault"}

        res = client.get("/tenants/tenant_foreign", headers=auth_headers)
        assert res.status_code == 403, "Access to foreign tenant must be forbidden"

    def test_cross_vault_access_rejection(self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]):
        """4. Access attempt to a second vault under same tenant returns 403 Forbidden."""
        # Create a second vault under tenant_alpha
        store.vaults["vault_secondary"] = {
            "vault_id": "vault_secondary",
            "tenant_id": "tenant_alpha",
            "name": "Secondary Vault",
        }

        res = client.get("/tenants/tenant_alpha/vaults/vault_secondary", headers=auth_headers)
        assert res.status_code == 403, "Access to non-assigned vault within same tenant must be forbidden"

    def test_unauthenticated_request_rejected(self, client: TestClient):
        """5. Unauthenticated request without Authorization header returns 401 Unauthorized."""
        res = client.get("/tenants/tenant_alpha")
        assert res.status_code == 401

    def test_vault_scoped_speaker_profiles(self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]):
        """6. Speakers list returns only speakers belonging to the user's vault."""
        # Add speaker in user's vault
        store.speaker_profiles["spk_alpha"] = {
            "speaker_id": "spk_alpha",
            "vault_id": "vault_primary",
            "name": "Alice Alpha",
        }
        # Add speaker in another vault
        store.speaker_profiles["spk_other"] = {
            "speaker_id": "spk_other",
            "vault_id": "vault_other",
            "name": "Bob Beta",
        }

        res = client.get("/speakers", headers=auth_headers)
        assert res.status_code == 200
        speakers = res.json()["speakers"]
        speaker_ids = [s["speaker_id"] for s in speakers]
        assert "spk_alpha" in speaker_ids
        assert "spk_other" not in speaker_ids, "Foreign vault speaker must not leak"
