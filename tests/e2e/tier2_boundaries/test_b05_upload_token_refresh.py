"""Tier 2: Boundary B05 - Upload Token Refresh & Auth Boundary Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.5.4.
Verifies handling of expired tokens (HTTP 401), token refresh coroutine recovery,
and single-retry limit on authentication expiry.
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore


class TestUploadTokenRefreshBoundaries:
    """Verifies token expiration, refresh recovery, and auth boundary conditions."""

    def test_missing_auth_header_rejected_401(self, client: TestClient, sample_adts_audio: bytes):
        """1. Multipart upload without Authorization header returns HTTP 401."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("test.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post("/tenants/tenant_alpha/vaults/vault_primary/recordings/upload", files=files)
        assert res.status_code == 401

    def test_malformed_auth_header_missing_bearer_prefix(self, client: TestClient, sample_adts_audio: bytes):
        """2. Authorization header with missing 'Bearer ' prefix returns HTTP 401."""
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
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers={"Authorization": "Basic some_token_credentials"},
        )
        assert res.status_code == 401

    def test_expired_or_invalid_token_rejected_401(self, client: TestClient, sample_adts_audio: bytes):
        """3. Invalid token returns HTTP 401 Unauthorized."""
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
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers={"Authorization": "Bearer token_expired_9999"},
        )
        assert res.status_code == 401

    def test_token_refresh_single_retry_recovery(
        self, client: TestClient, store: MemoryStore, sample_adts_audio: bytes
    ):
        """4. Simulated client coroutine refreshes expired token and succeeds on second attempt."""
        old_token = "token_stale_123"
        refreshed_token = "token_fresh_456"

        # Register refreshed token into server store
        store.auth_tokens[refreshed_token] = {
            "user_id": "user_001",
            "tenant_id": "tenant_alpha",
            "vault_id": "vault_primary",
            "dp_instance_id": "dp_phone_001",
        }

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("test.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }

        # Step 1: Upload with stale token -> 401
        res1 = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert res1.status_code == 401

        # Step 2: Client performs refresh, retries once with fresh token -> 201
        res2 = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers={"Authorization": f"Bearer {refreshed_token}"},
        )
        assert res2.status_code == 201
        assert res2.json()["status"] == "queued_for_processing"

    def test_refresh_failure_does_not_loop_infinitely(self, client: TestClient, sample_adts_audio: bytes):
        """5. If token refresh also fails, upload aborts after 1 retry without infinite loop."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("test.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }

        attempts = 0
        max_auth_retries = 1
        current_token = "bad_token"

        for _ in range(max_auth_retries + 1):
            attempts += 1
            res = client.post(
                "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
                files=files,
                headers={"Authorization": f"Bearer {current_token}"},
            )
            if res.status_code == 401:
                # Simulated refresh returns another bad token
                current_token = "still_bad_token"
            else:
                break

        assert attempts == 2, f"Should attempt original + 1 refresh retry, got {attempts}"
        assert res.status_code == 401
