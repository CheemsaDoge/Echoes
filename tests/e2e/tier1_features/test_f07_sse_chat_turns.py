"""Tier 1: Feature F4.1 & F4.2 - SSE Streaming Chat Turn Protocol Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 4.2 & Contract C4 & ORIGINAL_REQUEST.md R4.
Wire events:
- turn_progress (recalling -> executing -> summarizing)
- tool_call_started / tool_call_completed (search_memory, search_sections)
- text_delta
- turn_completed
- Last-Event-ID resumption & cancellation
"""

from __future__ import annotations
import json
import pytest
from starlette.testclient import TestClient


def parse_sse_events(raw_sse_text: str) -> list[dict]:
    """Parse raw W3C SSE text into structured event objects."""
    events = []
    current_event = {}
    for line in raw_sse_text.splitlines():
        line = line.strip()
        if not line:
            if current_event:
                events.append(current_event)
                current_event = {}
            continue
        if line.startswith("id:"):
            current_event["id"] = line[3:].strip()
        elif line.startswith("event:"):
            current_event["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current_event["data"] = json.loads(line[5:].strip())
    if current_event:
        events.append(current_event)
    return events


class TestSseChatTurns:
    """Verifies Server-Sent Events chat turns and long-memory reasoning stream."""

    def test_sse_stream_headers_and_event_stream_type(self, client: TestClient, auth_headers: dict[str, str]):
        """1. Stream endpoint responds with text/event-stream."""
        thread_id = "th_01"
        payload = {"message": "Where did I park my car this morning?"}
        res = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=auth_headers)
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]

    def test_complete_turn_event_lifecycle(self, client: TestClient, auth_headers: dict[str, str]):
        """2. Event lifecycle contains recalling, tool calling, summarizing, text_delta, turn_completed."""
        thread_id = "th_lifecycle"
        payload = {"message": "Find memory about project planning"}
        res = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=auth_headers)
        events = parse_sse_events(res.text)

        event_types = [e["event"] for e in events]
        assert "turn_progress" in event_types
        assert "tool_call_started" in event_types
        assert "tool_call_completed" in event_types
        assert "text_delta" in event_types
        assert "turn_completed" in event_types

        # Verify phase progression
        progress_events = [e for e in events if e["event"] == "turn_progress"]
        phases = [e["data"]["phase"] for e in progress_events]
        assert "recalling" in phases
        assert "summarizing" in phases

    def test_tool_call_execution_structure(self, client: TestClient, auth_headers: dict[str, str]):
        """3. Tool call event contains tool_name, call_id, arguments, and result."""
        thread_id = "th_tool"
        payload = {"message": "Where are my keys?"}
        res = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=auth_headers)
        events = parse_sse_events(res.text)

        start_ev = next(e for e in events if e["event"] == "tool_call_started")
        assert start_ev["data"]["tool_name"] == "search_memory"
        assert "arguments" in start_ev["data"]
        assert start_ev["data"]["status"] == "running"

        comp_ev = next(e for e in events if e["event"] == "tool_call_completed")
        assert comp_ev["data"]["status"] == "completed"
        assert "result" in comp_ev["data"]

    def test_reconnection_with_last_event_id(self, client: TestClient, auth_headers: dict[str, str]):
        """4. Sending Last-Event-ID resumes event stream from the next sequential event."""
        thread_id = "th_reconnect"
        turn_id = "turn_rec_01"
        payload = {"client_turn_id": turn_id, "message": "Resume this stream"}

        # First connection: get all events
        res1 = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=auth_headers)
        events1 = parse_sse_events(res1.text)
        assert len(events1) >= 6

        # Simulate drop after event seq 3
        last_id = f"{turn_id}_003"
        reconnect_headers = dict(auth_headers)
        reconnect_headers["Last-Event-ID"] = last_id

        res2 = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=reconnect_headers)
        events2 = parse_sse_events(res2.text)

        # Resumed events should start strictly after event 3 (seq >= 4)
        for ev in events2:
            ev_seq = int(ev["id"].split("_")[-1])
            assert ev_seq > 3, f"Resumed stream should not replay already acknowledged event {ev_seq}"

    def test_client_cancellation_protocol(self, client: TestClient, auth_headers: dict[str, str]):
        """5. Explicit turn cancellation triggers cancelled status."""
        thread_id = "th_cancel"
        turn_id = "turn_canc_01"

        res = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "cancelled"
        assert data["turn_id"] == turn_id
