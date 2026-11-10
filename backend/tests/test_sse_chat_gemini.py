"""Comprehensive Test Suite for Milestone M4:
SSE Chat Streaming, Gemini API Reasoning & Long Memory Tools, and Processing Chains / Mutation Sync.
"""

from __future__ import annotations
import asyncio
import datetime
import io
import json
import sqlite3
import pytest
from starlette.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.chat import (
    assemble_agent_system_instruction,
    execute_tool_call,
    SyntheticGeminiAgent,
)
from echoledger.sse import parse_last_event_id, format_sse_frame, TurnStreamManager


def parse_sse_events(raw_sse_text: str) -> list[dict]:
    """Parse raw W3C SSE wire stream into structured event dictionaries."""
    events = []
    current: dict = {}
    for line in raw_sse_text.splitlines():
        line = line.strip()
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("id:"):
            current["id"] = line[3:].strip()
        elif line.startswith("event:"):
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line[5:].strip())
    if current:
        events.append(current)
    return events


@pytest.fixture
def test_setup(tmp_path):
    """Sets up an isolated test database and FastAPI TestClient with seeded multi-tenant hierarchy."""
    settings = Settings(data_dir=tmp_path, api_token="test-admin-secret-token-32chars")
    app = create_app(settings)
    db = app.state.db

    # Seed Vault Alpha and Vault Beta
    with db.connect(immediate=True) as c:
        # Tenant Alpha
        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_alpha', 'Tenant Alpha', 'alpha')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_alpha', 'ten_alpha', 'Alpha Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_alpha', 'ten_alpha', 'alpha@echo.local', 'User Alpha')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_alpha', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'Phone Alpha', 'phone', 1)")
        c.execute(
            """INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
               VALUES ('bnd_alpha', 'ten_alpha', 'usr_alpha', 'dp_alpha', 'uuid_alpha', 'android', 'tok_alpha', 'bound')"""
        )

        # Tenant Beta
        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_beta', 'Tenant Beta', 'beta')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_beta', 'ten_beta', 'Beta Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_beta', 'ten_beta', 'beta@echo.local', 'User Beta')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_beta', 'ten_beta', 'vlt_beta', 'usr_beta', 'Phone Beta', 'phone', 1)")
        c.execute(
            """INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
               VALUES ('bnd_beta', 'ten_beta', 'usr_beta', 'dp_beta', 'uuid_beta', 'android', 'tok_beta', 'bound')"""
        )

    client = TestClient(app)
    headers_alpha = {
        "Authorization": "Bearer tok_alpha",
        "x-tenant-id": "ten_alpha",
        "x-vault-id": "vlt_alpha",
        "x-user-id": "usr_alpha",
        "x-dp-instance-id": "dp_alpha",
    }
    headers_beta = {
        "Authorization": "Bearer tok_beta",
        "x-tenant-id": "ten_beta",
        "x-vault-id": "vlt_beta",
        "x-user-id": "usr_beta",
        "x-dp-instance-id": "dp_beta",
    }
    admin_headers = {"Authorization": "Bearer test-admin-secret-token-32chars"}

    return {
        "app": app,
        "client": client,
        "db": db,
        "headers_alpha": headers_alpha,
        "headers_beta": headers_beta,
        "admin_headers": admin_headers,
    }


# =============================================================================
# 1. Database Schema & FTS5 Indexing Tests
# =============================================================================

class TestDatabaseAndFts5Schema:
    def test_tables_and_indexes_exist(self, test_setup):
        db: Database = test_setup["db"]
        tables = [r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")]
        assert "chat_threads" in tables
        assert "chat_turns" in tables
        assert "processing_chains" in tables
        assert "recording_mutations" in tables
        assert "memory_events" in tables
        assert "section_messages_fts" in tables

    def test_section_messages_fts5_triggers_synchronization(self, test_setup):
        db: Database = test_setup["db"]
        # Insert recording session and section
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with db.connect(immediate=True) as c:
            c.execute(
                "INSERT INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at, status) VALUES ('sess_fts_01', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'dp_alpha', '2026-11-10', ?, 'recording')",
                (now_iso,),
            )
            c.execute(
                "INSERT INTO sections (section_id, live_session_id, tenant_id, vault_id, section_index, start_ms, end_ms, transcript) VALUES ('sec_fts_01', 'sess_fts_01', 'ten_alpha', 'vlt_alpha', 0, 0, 5000, 'hello')",
            )
            c.execute(
                "INSERT INTO section_messages (message_id, section_id, content, start_ms, end_ms, speaker_name) VALUES ('msg_fts_01', 'sec_fts_01', 'Autonomous vehicle architecture review in Seattle', 1000, 4000, 'Alice')",
            )

        # FTS5 search
        matches = db.search_sections(vault_id="vlt_alpha", query="Seattle")
        assert len(matches) == 1
        assert matches[0]["message_id"] == "msg_fts_01"
        assert "Autonomous vehicle" in matches[0]["content"]

    def test_contract_c6_speaker_fallback_in_search_sections(self, test_setup):
        db: Database = test_setup["db"]
        # Seed speaker profile in vault
        db.create_speaker_profile(
            speaker_id="spk_c6_01",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha",
            user_id="usr_alpha",
            name="Dr. Evelyn Wright",
            suspected_name="Evelyn",
            role_label="Chief Scientist",
        )

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with db.connect(immediate=True) as c:
            c.execute(
                "INSERT INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at, status) VALUES ('sess_c6_01', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'dp_alpha', '2026-11-10', ?, 'recording')",
                (now_iso,),
            )
            c.execute(
                "INSERT INTO sections (section_id, live_session_id, tenant_id, vault_id, section_index, start_ms, end_ms, transcript) VALUES ('sec_c6_01', 'sess_c6_01', 'ten_alpha', 'vlt_alpha', 0, 0, 5000, 'quantum speech')",
            )
            c.execute(
                "INSERT INTO section_messages (message_id, section_id, speaker_id, content, start_ms, end_ms) VALUES ('msg_c6_01', 'sec_c6_01', 'spk_c6_01', 'Quantum computing error rates decreased significantly.', 500, 4500)",
            )

        results = db.search_sections(vault_id="vlt_alpha", query="Quantum computing")
        assert len(results) == 1
        # Contract C6 Tier 1 resolves profile name Dr. Evelyn Wright
        assert results[0]["speaker_name"] == "Dr. Evelyn Wright"


# =============================================================================
# 2. SSE Chat Streaming Lifecycle & Framing Tests
# =============================================================================

class TestSseChatStreamingLifecycle:
    def test_stream_headers_and_event_framing(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]

        res = client.post(
            "/chat/threads/th_test_stream/turns",
            json={"message": "Where did I park my vehicle this morning?"},
            headers=headers,
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        assert "no-cache" in res.headers["cache-control"]

        events = parse_sse_events(res.text)
        assert len(events) >= 6

        # Check standard event sequence
        types = [e["event"] for e in events]
        assert "turn_progress" in types
        assert "tool_call_started" in types
        assert "tool_call_completed" in types
        assert "text_delta" in types
        assert "turn_completed" in types

        # Check monotonic event ids
        for idx, ev in enumerate(events, start=1):
            assert ev["id"].endswith(f"_{idx:03d}")

    def test_dual_key_event_payloads(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]

        res = client.post(
            "/chat/threads/th_test_dualkey/turns",
            json={"message": "What did the team say about budget?"},
            headers=headers,
        )
        events = parse_sse_events(res.text)

        # Check tool_call_started dual keys
        tool_start = next(e for e in events if e["event"] == "tool_call_started")
        assert "tool" in tool_start["data"]
        assert "tool_name" in tool_start["data"]
        assert "args" in tool_start["data"]
        assert "arguments" in tool_start["data"]
        assert "call_id" in tool_start["data"]
        assert "tool_call_id" in tool_start["data"]
        assert tool_start["data"]["status"] == "running"

        # Check tool_call_completed dual keys
        tool_comp = next(e for e in events if e["event"] == "tool_call_completed")
        assert "status" in tool_comp["data"]
        assert tool_comp["data"]["status"] == "completed"
        assert "result" in tool_comp["data"]
        assert "elapsed_ms" in tool_comp["data"]

        # Check turn_completed
        turn_comp = next(e for e in events if e["event"] == "turn_completed")
        assert turn_comp["data"]["status"] == "completed"
        assert "tokens" in turn_comp["data"]


# =============================================================================
# 3. Circular Replay Buffer & Last-Event-ID Reconnection Tests
# =============================================================================

class TestSseReconnectionAndCircularBuffer:
    def test_reconnection_after_sequence_3(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        turn_id = "turn_reconn_test"

        # 1. Initial turn
        res1 = client.post(
            "/chat/threads/th_reconn/turns",
            json={"client_turn_id": turn_id, "message": "Remember this memory"},
            headers=headers,
        )
        all_events = parse_sse_events(res1.text)
        assert len(all_events) >= 6

        # 2. Reconnect after seq 3
        reconn_headers = dict(headers)
        reconn_headers["Last-Event-ID"] = f"{turn_id}_003"
        res2 = client.post(
            "/chat/threads/th_reconn/turns",
            json={"client_turn_id": turn_id, "message": "Resume"},
            headers=reconn_headers,
        )
        resumed_events = parse_sse_events(res2.text)

        # Resumed events must strictly begin at seq > 3
        assert len(resumed_events) > 0
        for ev in resumed_events:
            seq = int(ev["id"].split("_")[-1])
            assert seq > 3

    def test_reconnect_at_sequence_0_replays_from_beginning(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        turn_id = "turn_seq0_test"

        res1 = client.post(
            "/chat/threads/th_seq0/turns",
            json={"client_turn_id": turn_id, "message": "Test full replay"},
            headers=headers,
        )
        events1 = parse_sse_events(res1.text)

        reconn_headers = dict(headers)
        reconn_headers["Last-Event-ID"] = f"{turn_id}_000"
        res2 = client.post(
            "/chat/threads/th_seq0/turns",
            json={"client_turn_id": turn_id, "message": "Replay"},
            headers=reconn_headers,
        )
        events2 = parse_sse_events(res2.text)
        assert len(events2) == len(events1)
        assert events2[0]["id"] == f"{turn_id}_001"

    def test_reconnect_at_final_sequence_returns_zero_events(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        turn_id = "turn_final_test"

        res1 = client.post(
            "/chat/threads/th_final/turns",
            json={"client_turn_id": turn_id, "message": "Test end"},
            headers=headers,
        )
        events1 = parse_sse_events(res1.text)
        last_id = events1[-1]["id"]

        reconn_headers = dict(headers)
        reconn_headers["Last-Event-ID"] = last_id
        res2 = client.post(
            "/chat/threads/th_final/turns",
            json={"client_turn_id": turn_id, "message": "Resume at end"},
            headers=reconn_headers,
        )
        events2 = parse_sse_events(res2.text)
        assert len(events2) == 0

    def test_reconnect_with_malformed_last_event_id_falls_back_gracefully(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        turn_id = "turn_malformed_test"

        reconn_headers = dict(headers)
        reconn_headers["Last-Event-ID"] = "completely_invalid_header_value"
        res = client.post(
            "/chat/threads/th_malformed/turns",
            json={"client_turn_id": turn_id, "message": "Test malformed header"},
            headers=reconn_headers,
        )
        assert res.status_code == 200
        events = parse_sse_events(res.text)
        assert len(events) >= 6

    def test_reconnect_with_future_sequence_returns_empty(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        turn_id = "turn_future_test"

        res1 = client.post(
            "/chat/threads/th_future/turns",
            json={"client_turn_id": turn_id, "message": "Test future"},
            headers=headers,
        )
        assert res1.status_code == 200

        reconn_headers = dict(headers)
        reconn_headers["Last-Event-ID"] = f"{turn_id}_999"
        res2 = client.post(
            "/chat/threads/th_future/turns",
            json={"client_turn_id": turn_id, "message": "Future seq"},
            headers=reconn_headers,
        )
        events2 = parse_sse_events(res2.text)
        assert len(events2) == 0

    def test_expired_buffer_returns_410_gone(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        app = test_setup["app"]
        turn_id = "turn_expire_test"

        # Execute turn
        client.post(
            "/chat/threads/th_expire/turns",
            json={"client_turn_id": turn_id, "message": "Will expire"},
            headers=headers,
        )

        # Mark turn expired in TurnStreamManager
        manager: TurnStreamManager = app.state.turn_manager
        manager.mark_expired(turn_id)

        # Reconnection to expired turn must return 410 Gone
        reconn_headers = dict(headers)
        reconn_headers["Last-Event-ID"] = f"{turn_id}_003"
        res = client.post(
            "/chat/threads/th_expire/turns",
            json={"client_turn_id": turn_id, "message": "Resume expired"},
            headers=reconn_headers,
        )
        assert res.status_code == 410
        assert res.json()["error"] == "turn_replay_buffer_expired"


# =============================================================================
# 4. Turn Cancellation Protocol Tests
# =============================================================================

class TestTurnCancellationProtocol:
    def test_cancel_active_turn_returns_200_and_is_idempotent(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        db: Database = test_setup["db"]

        thread_id = "th_cancel_suite"
        turn_id = "turn_cancel_01"

        # Pre-create thread and turn
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")
        db.create_chat_turn(turn_id=turn_id, thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", prompt="Stop me")

        # First cancellation call
        res1 = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "cancelled"
        assert data1["turn_id"] == turn_id
        assert "cancelled_at" in data1

        # Second cancellation call (idempotent)
        res2 = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["status"] == "cancelled"

        # Verify DB status updated
        turn_row = db.get_chat_turn(turn_id)
        assert turn_row["status"] == "cancelled"


# =============================================================================
# 5. Gemini Long Memory Tools & Temporal Anchoring Tests
# =============================================================================

class TestGeminiLongMemoryToolsAndReasoning:
    def test_system_instruction_temporal_anchoring(self, test_setup):
        db: Database = test_setup["db"]
        instruction = assemble_agent_system_instruction(
            db=db,
            vault_id="vlt_alpha",
            user_id="usr_alpha",
            client_context={"client_timezone": "America/New_York"},
        )
        assert "TEMPORAL ANCHORING" in instruction
        assert "America/New_York" in instruction
        assert "VAULT AMBIENT CONTEXT" in instruction
        assert "search_memory" in instruction
        assert "search_sections" in instruction

    def test_search_memory_tool_execution(self, test_setup):
        db: Database = test_setup["db"]
        # Seed memory event
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        db.create_memory_event(
            event_id="ev_groceries_01",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha",
            title="Grocery Shopping List",
            summary="Bought almond milk, avocados, and dark roast coffee.",
            start_time=now_iso,
            end_time=now_iso,
            category="errand",
            keywords=["groceries", "coffee", "milk"],
        )

        out = execute_tool_call(db, "vlt_alpha", "search_memory", {"query": "almond milk"})
        assert out["status"] == "completed"
        matches = out["result"]["memories"]
        assert len(matches) >= 1
        assert "Grocery Shopping List" in matches[0]["title"]

    def test_synthetic_gemini_agent_offline_execution(self, test_setup):
        db: Database = test_setup["db"]
        agent = SyntheticGeminiAgent(db)
        buffer = TurnStreamManager().get_or_create("turn_synth_01", "th_synth_01")

        async def run():
            await agent.execute_turn(buffer, "Where did I park?", "vlt_alpha", "usr_alpha")

        asyncio.run(run())
        assert buffer.is_completed
        events = list(buffer.events)
        assert any(e.event_type == "tool_call_completed" for e in events)
        assert any(e.event_type == "turn_completed" for e in events)


# =============================================================================
# 6. Processing Chains & Recording Mutations Tests
# =============================================================================

class TestProcessingChainsAndMutations:
    def test_processing_chains_polling(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        db: Database = test_setup["db"]

        # Create active chain
        db.create_processing_chain(
            chain_id="chn_test_01",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha",
            recording_id="rec_test_01",
            current_step="vad",
            progress_pct=35.0,
            status="running",
        )

        # GET /processing-chains?status=active
        res = client.get("/processing-chains?status=active", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "chains" in data
        assert any(c["chain_id"] == "chn_test_01" for c in data["chains"])

        # GET /processing-chains/{chain_id}
        res_single = client.get("/processing-chains/chn_test_01", headers=headers)
        assert res_single.status_code == 200
        assert res_single.json()["current_step"] == "vad"

    def test_recording_mutations_cursor_sync_and_push(self, test_setup):
        client: TestClient = test_setup["client"]
        headers = test_setup["headers_alpha"]
        db: Database = test_setup["db"]

        # Initial cursor sync
        res1 = client.get("/recording-mutations?cursor=0&limit=50", headers=headers)
        assert res1.status_code == 200
        cursor1 = res1.json()["next_cursor"]

        # Push client redaction mutation
        redact_req = {
            "mutations": [
                {
                    "mutation_id": "mut_redact_01",
                    "live_session_id": "sess_privacy_01",
                    "action": "redact_audio",
                    "start_ms": 10000,
                    "end_ms": 25000,
                    "delete_audio": True,
                    "retain_transcript": False,
                }
            ]
        }
        push_res = client.post("/recording-mutations", json=redact_req, headers=headers)
        assert push_res.status_code == 200
        assert push_res.json()["status"] == "synced"
        assert push_res.json()["processed_count"] == 1

        # Subsequent cursor sync detects new mutation
        res2 = client.get(f"/recording-mutations?cursor={cursor1}&limit=50", headers=headers)
        assert res2.status_code == 200
        muts = res2.json()["mutations"]
        assert len(muts) >= 1
        assert muts[0]["mutation_type"] == "redact_audio"


# =============================================================================
# 7. Multi-Tenant Micro-Isolation & 404 Anti-Enumeration Tests
# =============================================================================

class TestMultiTenantMicroIsolation:
    def test_cross_vault_thread_access_returns_404_anti_enumeration(self, test_setup):
        client: TestClient = test_setup["client"]
        headers_alpha = test_setup["headers_alpha"]
        headers_beta = test_setup["headers_beta"]
        db: Database = test_setup["db"]

        # Thread created in Vault Alpha
        thread_id = "th_alpha_confidential"
        db.create_chat_thread(
            thread_id=thread_id,
            tenant_id="ten_alpha",
            vault_id="vlt_alpha",
            user_id="usr_alpha",
            title="Alpha Confidential",
        )

        # Alpha can read it
        res_a = client.get(f"/chat/threads/{thread_id}", headers=headers_alpha)
        assert res_a.status_code == 200

        # Beta must receive HTTP 404 Not Found (anti-enumeration: NOT 403)
        res_b = client.get(f"/chat/threads/{thread_id}", headers=headers_beta)
        assert res_b.status_code == 404

        # Beta attempting turn on Alpha thread receives 404
        res_turn_b = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"message": "Probe other tenant"},
            headers=headers_beta,
        )
        assert res_turn_b.status_code == 404

    def test_cross_vault_processing_chain_returns_404(self, test_setup):
        client: TestClient = test_setup["client"]
        headers_beta = test_setup["headers_beta"]
        db: Database = test_setup["db"]

        db.create_processing_chain(
            chain_id="chn_alpha_secret",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha",
            recording_id="rec_alpha_secret",
        )

        # User Beta query returns 404
        res = client.get("/processing-chains/chn_alpha_secret", headers=headers_beta)
        assert res.status_code == 404

    def test_recording_mutations_quarantine_isolation(self, test_setup):
        client: TestClient = test_setup["client"]
        headers_alpha = test_setup["headers_alpha"]
        headers_beta = test_setup["headers_beta"]
        db: Database = test_setup["db"]

        # Append mutation to Alpha and Beta
        db.append_recording_mutation("ten_alpha", "vlt_alpha", "segment", "seg_a", "segment_uploaded", {"test": "alpha"})
        db.append_recording_mutation("ten_beta", "vlt_beta", "segment", "seg_b", "segment_uploaded", {"test": "beta"})

        res_a = client.get("/recording-mutations?cursor=0", headers=headers_alpha)
        assert res_a.status_code == 200
        for m in res_a.json()["mutations"]:
            assert m["vault_id"] == "vlt_alpha"

        res_b = client.get("/recording-mutations?cursor=0", headers=headers_beta)
        assert res_b.status_code == 200
        for m in res_b.json()["mutations"]:
            assert m["vault_id"] == "vlt_beta"
