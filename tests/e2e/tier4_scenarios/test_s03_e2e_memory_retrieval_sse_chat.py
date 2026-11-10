"""Tier 4: Scenario S03 - End-to-End Memory Ingestion, Retrieval & Multi-Turn SSE Chat.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 4.2 & ORIGINAL_REQUEST.md R4.
Simulates:
1. Audio segment ingestion and speaker diarization labeling.
2. Conversational reasoning turn with memory retrieval tool call.
3. Multi-turn context preservation and token streaming.
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


class TestE2EMemoryRetrievalAndChat:
    """Verifies end-to-end flow from audio ingestion to episodic memory recall and streaming chat."""

    def test_full_memory_retrieval_and_conversational_turns(
        self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str], sample_adts_audio: bytes
    ):
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        vault_id = "vault_primary"

        # 1. Ingest audio segment
        meta = {
            "recording_id": "rec_finance_meeting",
            "dp_instance_id": "dp_phone_001",
            "split_reason": "silence_window",
        }
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "ambient"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("meeting.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        upload_res = client.post(
            f"/tenants/tenant_alpha/vaults/{vault_id}/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert upload_res.status_code == 201

        # 2. Enroll CFO speaker
        spk_res = client.post(
            "/speakers",
            json={"name": "Elena Rossi", "role_label": "CFO"},
            headers=auth_headers,
        )
        assert spk_res.status_code == 201
        cfo_spk_id = spk_res.json()["speaker_id"]

        # Pre-seed section message
        store.section_messages["msg_cfo_01"] = {
            "message_id": "msg_cfo_01",
            "vault_id": vault_id,
            "speaker_id": cfo_spk_id,
            "content": "The Q4 infrastructure budget is approved at $250k.",
        }

        # 3. Turn 1: User asks "What did Elena say about the Q4 budget?"
        thread_id = "th_budget_inquiry"
        t1_payload = {
            "client_turn_id": "turn_q1",
            "message": "What did Elena say about the Q4 budget?",
        }
        t1_res = client.post(f"/chat/threads/{thread_id}/turns", json=t1_payload, headers=auth_headers)
        assert t1_res.status_code == 200
        events_t1 = parse_sse_events(t1_res.text)

        # Check tool execution
        tool_start = next(e for e in events_t1 if e["event"] == "tool_call_started")
        assert tool_start["data"]["tool_name"] == "search_memory"
        assert "Q4 budget" in tool_start["data"]["arguments"]["query"]

        tool_comp = next(e for e in events_t1 if e["event"] == "tool_call_completed")
        assert tool_comp["data"]["status"] == "completed"

        turn_comp = next(e for e in events_t1 if e["event"] == "turn_completed")
        assert turn_comp["data"]["status"] == "completed"

        # 4. Turn 2: Follow-up question in the same thread
        t2_payload = {
            "client_turn_id": "turn_q2",
            "message": "Did she mention any caveats?",
        }
        t2_res = client.post(f"/chat/threads/{thread_id}/turns", json=t2_payload, headers=auth_headers)
        assert t2_res.status_code == 200
        events_t2 = parse_sse_events(t2_res.text)

        event_names = [e["event"] for e in events_t2]
        assert "turn_progress" in event_names
        assert "text_delta" in event_names
        assert "turn_completed" in event_names
