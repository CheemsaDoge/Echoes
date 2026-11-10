"""Tier 4: Scenario S02 - Multi-Tenant Multi-Device Ambient Room Capture & Arbitration.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 1.3, 3.1 & ORIGINAL_REQUEST.md.
Simulates a real-world conference room with multiple co-located users and devices:
- Alice (Tenant Alpha, Vault Alpha) carrying Phone (primary) and Watch (secondary).
- Bob (Tenant Beta, Vault Beta) running Laptop recorder.
- Dynamic lease transfer between Alice's Phone and Watch.
- Independent, concurrent recording and upload with zero cross-tenant interference.
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.audio_harness import synthesize_adts_file


class TestMultiTenantMultiDeviceRoomArbitration:
    """Verifies room multi-device arbitration and tenant micro-isolation."""

    def test_conference_room_arbitration_and_isolation_flow(
        self, client: TestClient, store: MemoryStore, sample_adts_audio: bytes
    ):
        # 1. Setup Alice's devices under Tenant Alpha
        store.dp_instances["dp_phone_001"]["is_primary"] = False
        store.dp_instances["alice_phone"] = {
            "dp_instance_id": "alice_phone",
            "tenant_id": "tenant_alpha",
            "vault_id": "vault_primary",
            "user_id": "user_001",
            "is_primary": True,
            "device_name": "Pixel 8",
        }
        store.dp_instances["alice_watch"] = {
            "dp_instance_id": "alice_watch",
            "tenant_id": "tenant_alpha",
            "vault_id": "vault_primary",
            "user_id": "user_001",
            "is_primary": False,
            "device_name": "Pixel Watch",
        }
        token_alice = "token_alpha_user_001"
        headers_alice = {"Authorization": f"Bearer {token_alice}"}

        # 2. Setup Bob's device under Tenant Beta
        t_beta = "tenant_beta"
        v_beta = "vault_beta"
        u_bob = "user_bob"
        store.tenants[t_beta] = {"tenant_id": t_beta, "name": "Beta Inc"}
        store.vaults[v_beta] = {"vault_id": v_beta, "tenant_id": t_beta, "name": "Bob Vault"}
        store.users[u_bob] = {"user_id": u_bob, "tenant_id": t_beta, "vault_id": v_beta, "email": "bob@beta.com"}
        store.dp_instances["bob_laptop"] = {
            "dp_instance_id": "bob_laptop",
            "tenant_id": t_beta,
            "vault_id": v_beta,
            "user_id": u_bob,
            "is_primary": True,
            "device_name": "ThinkPad X1",
        }
        token_bob = "token_bob_user"
        store.auth_tokens[token_bob] = {
            "user_id": u_bob,
            "tenant_id": t_beta,
            "vault_id": v_beta,
            "dp_instance_id": "bob_laptop",
        }
        store.listening_schedules[u_bob] = {
            "user_id": u_bob,
            "revision": 1,
            "timezone": "UTC",
            "weekly_windows": [
                {"weekday": i, "window_start": "00:00", "window_end": "23:59", "start_minutes": 0, "end_minutes": 1439}
                for i in range(1, 8)
            ],
        }
        headers_bob = {"Authorization": f"Bearer {token_bob}"}

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 3. Alice's Phone uploads chunk 1 as Primary -> 201
        meta_phone_1 = {"recording_id": "rec_alice_room", "dp_instance_id": "alice_phone"}
        files_phone_1 = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta_phone_1)),
            "file": ("p1.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res_phone_1 = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files_phone_1,
            headers=headers_alice,
        )
        assert res_phone_1.status_code == 201

        # 4. Meanwhile, Bob's Laptop uploads Bob's ambient chunk -> 201
        meta_bob = {"recording_id": "rec_bob_room", "dp_instance_id": "bob_laptop"}
        files_bob = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "app_default"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta_bob)),
            "file": ("b1.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res_bob = client.post(
            f"/tenants/{t_beta}/vaults/{v_beta}/recordings/upload",
            files=files_bob,
            headers=headers_bob,
        )
        assert res_bob.status_code == 201

        # 5. Alice transfers primary lease to Watch
        res_switch = client.put(
            "/primary-device",
            json={"dp_instance_id": "alice_watch", "vault_id": "vault_primary"},
            headers=headers_alice,
        )
        assert res_switch.status_code == 200

        # 6. Alice's Phone attempts to upload chunk 2 -> Rejected with 409 not_primary
        meta_phone_2 = {"recording_id": "rec_alice_room", "dp_instance_id": "alice_phone"}
        files_phone_2 = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta_phone_2)),
            "file": ("p2.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res_phone_2 = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files_phone_2,
            headers=headers_alice,
        )
        assert res_phone_2.status_code == 409
        assert res_phone_2.json()["error_code"] == "recording.upload_rejected_not_primary"

        # 7. Alice's Watch uploads chunk 2 -> 201
        meta_watch_2 = {"recording_id": "rec_alice_room", "dp_instance_id": "alice_watch"}
        files_watch_2 = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "watch"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta_watch_2)),
            "file": ("w2.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res_watch_2 = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files_watch_2,
            headers=headers_alice,
        )
        assert res_watch_2.status_code == 201

        # 8. Bob's laptop continues uninterrupted
        res_bob_2 = client.post(
            f"/tenants/{t_beta}/vaults/{v_beta}/recordings/upload",
            files=files_bob,
            headers=headers_bob,
        )
        assert res_bob_2.status_code == 201
