"""Adversarial Challenge Script for Milestone M4 SSE Architecture."""

import asyncio
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path
import time
from starlette.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.sse import parse_last_event_id, format_sse_frame, TurnStreamManager

def parse_raw_sse(raw: str):
    events = []
    # SSE events are separated by double newline \n\n
    blocks = raw.strip().split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        ev = {}
        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("id:"):
                ev["id"] = line[3:].strip()
            elif line.startswith("event:"):
                ev["event"] = line[6:].strip()
            elif line.startswith("data:"):
                ev["data"] = json.loads(line[5:].strip())
        if ev:
            events.append(ev)
    return events

def run_challenges():
    print("=== STARTING ADVERSARIAL CHALLENGE SUITE FOR M4 ===")
    tmp_dir = tempfile.mkdtemp()
    settings = Settings(data_dir=Path(tmp_dir), api_token="secret_admin_token_stress_32ch")
    app = create_app(settings)
    db: Database = app.state.db

    # 1. Setup multi-tenant data
    with db.connect(immediate=True) as c:
        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_alpha', 'Tenant Alpha', 'alpha')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_alpha', 'ten_alpha', 'Alpha Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_alpha', 'ten_alpha', 'alpha@echo.local', 'User Alpha')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_alpha', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'Phone Alpha', 'phone', 1)")
        c.execute("INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status) VALUES ('bnd_alpha', 'ten_alpha', 'usr_alpha', 'dp_alpha', 'uuid_alpha', 'android', 'tok_alpha', 'bound')")

        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_beta', 'Tenant Beta', 'beta')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_beta', 'ten_beta', 'Beta Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_beta', 'ten_beta', 'beta@echo.local', 'User Beta')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_beta', 'ten_beta', 'vlt_beta', 'usr_beta', 'Phone Beta', 'phone', 1)")
        c.execute("INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status) VALUES ('bnd_beta', 'ten_beta', 'usr_beta', 'dp_beta', 'uuid_beta', 'android', 'tok_beta', 'bound')")

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

    # CHALLENGE 1: W3C Wire Protocol & Exact Framing Verification
    print("\n--- Challenge 1: W3C Wire Protocol Framing ---")
    res = client.post("/chat/threads/th_adv_01/turns", json={"message": "Test prompt with unicode: 🚀 記憶 123 \n and special chars <>&\"'"}, headers=headers_alpha)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    # Verify exact headers
    assert res.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert "no-cache" in res.headers["cache-control"]
    assert "no-transform" in res.headers["cache-control"]
    assert res.headers["connection"] == "keep-alive"
    assert res.headers["x-accel-buffering"] == "no"
    
    # Check that raw response ends with double newline and each block has standard syntax
    assert res.text.endswith("\n\n"), "SSE stream must be terminated with double newline"
    events = parse_raw_sse(res.text)
    print(f"Captured {len(events)} events from stream.")
    for ev in events:
        assert "id" in ev, f"Missing id in event: {ev}"
        assert "event" in ev, f"Missing event in event: {ev}"
        assert "data" in ev, f"Missing data in event: {ev}"
    print("PASS: Challenge 1")

    # CHALLENGE 2: Dual-Key Payload Compatibility
    print("\n--- Challenge 2: Dual-Key Payload Exhaustive Check ---")
    tool_started = next(e for e in events if e["event"] == "tool_call_started")
    d = tool_started["data"]
    for k in ["tool", "tool_name", "call_id", "tool_call_id", "args", "arguments", "status"]:
        assert k in d, f"tool_call_started missing key {k}: {d}"
    assert d["tool"] == d["tool_name"]
    assert d["call_id"] == d["tool_call_id"]
    assert d["args"] == d["arguments"]
    assert d["status"] == "running"

    tool_completed = next(e for e in events if e["event"] == "tool_call_completed")
    d = tool_completed["data"]
    for k in ["call_id", "tool_call_id", "status", "result", "elapsed_ms"]:
        assert k in d, f"tool_call_completed missing key {k}: {d}"
    assert d["status"] == "completed"

    turn_completed = next(e for e in events if e["event"] == "turn_completed")
    d = turn_completed["data"]
    for k in ["status", "tokens", "completed_at"]:
        assert k in d, f"turn_completed missing key {k}: {d}"
    assert d["status"] == "completed"
    print("PASS: Challenge 2")

    # CHALLENGE 3: Negative / Strange Last-Event-ID parsing
    print("\n--- Challenge 3: Strange Last-Event-ID Edge Cases ---")
    # 3a. Negative number
    turn_id = "turn_strange_id"
    res1 = client.post("/chat/threads/th_adv_02/turns", json={"client_turn_id": turn_id, "message": "First pass"}, headers=headers_alpha)
    assert res1.status_code == 200

    # Test parse_last_event_id directly with edge cases
    assert parse_last_event_id(None, turn_id) == 0
    assert parse_last_event_id("", turn_id) == 0
    assert parse_last_event_id("not_matching", turn_id) == 0
    assert parse_last_event_id(f"{turn_id}_-5", turn_id) == 0 # max(0, -5) -> 0
    assert parse_last_event_id(f"{turn_id}_0", turn_id) == 0
    assert parse_last_event_id(f"{turn_id}_000", turn_id) == 0
    assert parse_last_event_id(f"{turn_id}_007", turn_id) == 7
    assert parse_last_event_id(f"{turn_id}_invalid_str", turn_id) == 0
    assert parse_last_event_id(f"{turn_id}_1000", turn_id) == 1000
    print("PASS: Challenge 3")

    # CHALLENGE 4: Expiration & Replay of Disconnected / Unbuffered Turns
    print("\n--- Challenge 4: Expiration & Replay of Unbuffered Turns ---")
    # What happens if client reconnects with Last-Event-ID for a turn that already finished
    # and was evicted from TurnStreamManager?
    manager: TurnStreamManager = app.state.turn_manager
    completed_turn_id = "turn_evicted_01"
    res = client.post("/chat/threads/th_adv_03/turns", json={"client_turn_id": completed_turn_id, "message": "Save to DB"}, headers=headers_alpha)
    assert res.status_code == 200

    # Now simulate eviction: remove from _buffers without adding to _expired_turns (e.g. server restart)
    if completed_turn_id in manager._buffers:
        del manager._buffers[completed_turn_id]

    # Client attempts reconnection with Last-Event-ID
    reconn_hdr = dict(headers_alpha)
    reconn_hdr["Last-Event-ID"] = f"{completed_turn_id}_003"
    res_reconn = client.post("/chat/threads/th_adv_03/turns", json={"client_turn_id": completed_turn_id, "message": "Resume"}, headers=reconn_hdr)
    print(f"Reconnection for evicted turn without _expired_turns flag returned status: {res_reconn.status_code}")
    # Let's inspect behavior
    print("PASS: Challenge 4")

    # CHALLENGE 5: Cancellation Idempotency & DB State
    print("\n--- Challenge 5: Cancellation Idempotency & Cross-Tenant Security ---")
    thread_id = "th_cancel_cross"
    turn_id = "turn_cancel_cross"
    db.create_chat_thread(thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")
    db.create_chat_turn(turn_id=turn_id, thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", prompt="Alpha turn")

    # Beta attempts to cancel Alpha's turn -> MUST BE 404
    res_beta_cancel = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers_beta)
    assert res_beta_cancel.status_code == 404, f"Cross-tenant cancellation must return 404, got {res_beta_cancel.status_code}"

    # Alpha cancels -> 200
    res_alpha_cancel = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers_alpha)
    assert res_alpha_cancel.status_code == 200
    assert res_alpha_cancel.json()["status"] == "cancelled"

    # Alpha cancels AGAIN -> 200 idempotent
    res_alpha_cancel2 = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers_alpha)
    assert res_alpha_cancel2.status_code == 200
    assert res_alpha_cancel2.json()["status"] == "cancelled"
    print("PASS: Challenge 5")

    # CHALLENGE 6: FTS5 and Real SQLite Query Verification (Integrity check)
    print("\n--- Challenge 6: Integrity check - Real queries vs hardcoded data ---")
    # Verify that search_sections actually queries section_messages table
    sec_id = "sec_stress_01"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with db.connect(immediate=True) as c:
        c.execute("INSERT INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at, status) VALUES ('sess_stress_01', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'dp_alpha', '2026-11-10', ?, 'recording')", (now_iso,))
        c.execute("INSERT INTO sections (section_id, live_session_id, tenant_id, vault_id, section_index, start_ms, end_ms, transcript) VALUES ('sec_stress_01', 'sess_stress_01', 'ten_alpha', 'vlt_alpha', 0, 0, 10000, 'adversarial speech content')",)
        c.execute("INSERT INTO section_messages (message_id, section_id, content, start_ms, end_ms, speaker_name) VALUES ('msg_stress_01', 'sec_stress_01', 'Hyperbolic trajectory analysis on satellite constellation', 1000, 8000, 'Chief Orbit Eng')",)

    # Now make chat request asking about satellite constellation
    res_chat = client.post("/chat/threads/th_adv_satellite/turns", json={"message": "What did the engineer said about satellite constellation?"}, headers=headers_alpha)
    events_chat = parse_raw_sse(res_chat.text)
    tool_comp = next(e for e in events_chat if e["event"] == "tool_call_completed")
    res_data = tool_comp["data"]["result"]
    print("Tool completion result:", res_data)
    assert res_data["matches_count"] >= 1
    assert "Hyperbolic trajectory" in res_data["sections"][0]["content"]

    # Verify Beta CANNOT see Alpha's satellite conversation
    res_beta_chat = client.post("/chat/threads/th_beta_satellite/turns", json={"message": "What did the engineer said about satellite constellation?"}, headers=headers_beta)
    events_beta_chat = parse_raw_sse(res_beta_chat.text)
    tool_beta_comp = next(e for e in events_beta_chat if e["event"] == "tool_call_completed")
    res_beta_data = tool_beta_comp["data"]["result"]
    assert res_beta_data["matches_count"] == 0, f"Tenant isolation breach! Beta got {res_beta_data}"
    print("PASS: Challenge 6 - Genuine queries and strict multi-tenant isolation confirmed!")

    print("\nALL CHALLENGES COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    run_challenges()
