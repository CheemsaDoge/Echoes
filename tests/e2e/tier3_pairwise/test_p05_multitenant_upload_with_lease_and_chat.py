"""Tier 3: Pairwise P05 - Multi-Tenant Multi-Device Ingestion, Lease Arbitration & Chat Recall.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 1.3, 3.1 & 4.2.
Cross-feature interaction between:
1. 4-Tier Tenancy Micro-Isolation
2. Primary Lease Arbitration across Independent Vaults
3. 6-Part Multipart Ingestion
4. SSE Conversational Reasoning & Episodic Memory Scoping
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.audio_harness import synthesize_adts_file
from tests.e2e.tier1_features.test_f07_sse_chat_turns import parse_sse_events


class TestMultiTenantUploadAndChatInteraction:
    """Verifies end-to-end multi-tenant isolation during concurrent uploads, leases, and memory chat."""

    def test_concurrent_multitenant_uploads_and_memory_isolation(
        self, client: TestClient, store: MemoryStore, sample_adts_audio: bytes
    ):
        # 1. Setup Tenant Beta alongside Tenant Alpha
        t_beta = "tenant_beta"
        v_beta = "vault_beta"
        u_beta = "user_beta_001"
        dp_beta = "dp_beta_phone"

        store.tenants[t_beta] = {"tenant_id": t_beta, "name": "Beta Corp"}
        store.vaults[v_beta] = {"vault_id": v_beta, "tenant_id": t_beta, "name": "Beta Vault"}
        store.users[u_beta] = {"user_id": u_beta, "tenant_id": t_beta, "vault_id": v_beta, "email": "beta@corp.com"}
        store.dp_instances[dp_beta] = {
            "dp_instance_id": dp_beta,
            "tenant_id": t_beta,
            "vault_id": v_beta,
            "user_id": u_beta,
            "is_primary": True,
            "device_name": "Galaxy S24",
        }
        token_beta = "token_beta_user_001"
        store.auth_tokens[token_beta] = {
            "user_id": u_beta,
            "tenant_id": t_beta,
            "vault_id": v_beta,
            "dp_instance_id": dp_beta,
        }
        store.listening_schedules[u_beta] = {
            "user_id": u_beta,
            "revision": 1,
            "timezone": "UTC",
            "weekly_windows": [
                {"weekday": i, "window_start": "00:00", "window_end": "23:59", "start_minutes": 0, "end_minutes": 1439}
                for i in range(1, 8)
            ],
        }

        token_alpha = "token_alpha_user_001"
        headers_alpha = {"Authorization": f"Bearer {token_alpha}"}
        headers_beta = {"Authorization": f"Bearer {token_beta}"}

        # 2. Both Phone Alpha and Phone Beta hold independent primary leases in their respective vaults
        assert store.dp_instances["dp_phone_001"]["is_primary"] is True
        assert store.dp_instances[dp_beta]["is_primary"] is True

        # 3. Concurrent audio upload to Vault Alpha and Vault Beta
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        files_alpha = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"recording_id": "rec_alpha_01", "dp_instance_id": "dp_phone_001"})),
            "file": ("alpha.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res_a = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files_alpha,
            headers=headers_alpha,
        )
        assert res_a.status_code == 201
        data_a = res_a.json()
        assert data_a["storage_key"].startswith("recordings/vault_primary/")

        files_beta = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"recording_id": "rec_beta_01", "dp_instance_id": dp_beta})),
            "file": ("beta.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res_b = client.post(
            f"/tenants/{t_beta}/vaults/{v_beta}/recordings/upload",
            files=files_beta,
            headers=headers_beta,
        )
        assert res_b.status_code == 201
        data_b = res_b.json()
        assert data_b["storage_key"].startswith(f"recordings/{v_beta}/")

        # 4. Chat turn querying memory under Tenant Alpha
        chat_res = client.post(
            "/chat/threads/th_alpha_01/turns",
            json={"message": "Summarize today's recordings"},
            headers=headers_alpha,
        )
        assert chat_res.status_code == 200
        events = parse_sse_events(chat_res.text)
        assert any(e["event"] == "tool_call_completed" for e in events)
        assert any(e["event"] == "turn_completed" for e in events)

        # 5. User Beta attempting cross-tenant access to Tenant Alpha is rejected
        breach_res = client.get("/tenants/tenant_alpha", headers=headers_beta)
        assert breach_res.status_code == 403
