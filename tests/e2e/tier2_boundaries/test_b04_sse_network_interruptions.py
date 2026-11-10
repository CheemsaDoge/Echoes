"""Tier 2: Boundary B04 - SSE Network Interruption & Reconnection Edge Cases.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 4.2.3.
Verifies Last-Event-ID boundaries: seq 0, final seq, out-of-range seq, and cancel edge cases.
"""

from __future__ import annotations
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.tier1_features.test_f07_sse_chat_turns import parse_sse_events


class TestSseNetworkInterruptionBoundaries:
    """Verifies edge conditions during SSE stream disconnections and resumptions."""

    def test_reconnect_at_sequence_0_replays_entire_stream(self, client: TestClient, auth_headers: dict):
        """1. Reconnecting with Last-Event-ID at seq 0 replays all events from event 1."""
        thread_id = "th_seq0"
        turn_id = "turn_seq0"
        payload = {"client_turn_id": turn_id, "message": "Test seq 0 replay"}

        # Initial turn
        res1 = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=auth_headers)
        all_events = parse_sse_events(res1.text)

        # Reconnect with seq 0
        reconnect_headers = dict(auth_headers)
        reconnect_headers["Last-Event-ID"] = f"{turn_id}_000"
        res2 = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=reconnect_headers)
        resumed = parse_sse_events(res2.text)

        assert len(resumed) == len(all_events)
        assert resumed[0]["id"] == f"{turn_id}_001"

    def test_reconnect_at_final_sequence_returns_zero_remaining_events(self, client: TestClient, auth_headers: dict):
        """2. Reconnecting with Last-Event-ID matching the last event ID yields 0 new events."""
        thread_id = "th_seq_end"
        turn_id = "turn_seq_end"
        payload = {"client_turn_id": turn_id, "message": "Test end seq"}

        res1 = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=auth_headers)
        all_events = parse_sse_events(res1.text)
        last_id = all_events[-1]["id"]

        reconnect_headers = dict(auth_headers)
        reconnect_headers["Last-Event-ID"] = last_id
        res2 = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=reconnect_headers)
        resumed = parse_sse_events(res2.text)

        assert len(resumed) == 0, "No new events should be emitted after the final event"

    def test_reconnect_with_malformed_last_event_id_falls_back_gracefully(self, client: TestClient, auth_headers: dict):
        """3. Malformed Last-Event-ID (non-numeric suffix) starts from beginning rather than erroring."""
        thread_id = "th_malformed"
        turn_id = "turn_malformed"
        payload = {"client_turn_id": turn_id, "message": "Test malformed id"}

        reconnect_headers = dict(auth_headers)
        reconnect_headers["Last-Event-ID"] = "not_a_valid_id"
        res = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=reconnect_headers)
        assert res.status_code == 200
        events = parse_sse_events(res.text)
        assert len(events) >= 6

    def test_reconnect_with_future_sequence_number_returns_empty(self, client: TestClient, auth_headers: dict):
        """4. Sequence number greater than all emitted events (e.g. 999) yields 0 events."""
        thread_id = "th_future_seq"
        turn_id = "turn_future_seq"
        payload = {"client_turn_id": turn_id, "message": "Test future seq"}

        reconnect_headers = dict(auth_headers)
        reconnect_headers["Last-Event-ID"] = f"{turn_id}_999"
        res = client.post(f"/chat/threads/{thread_id}/turns", json=payload, headers=reconnect_headers)
        events = parse_sse_events(res.text)
        assert len(events) == 0

    def test_cancelling_already_cancelled_turn_is_idempotent(self, client: TestClient, auth_headers: dict):
        """5. Cancelling an already cancelled turn returns status cancelled idempotently."""
        thread_id = "th_cancel_idem"
        turn_id = "turn_cancel_idem"

        res1 = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=auth_headers)
        assert res1.status_code == 200
        assert res1.json()["status"] == "cancelled"

        res2 = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=auth_headers)
        assert res2.status_code == 200
        assert res2.json()["status"] == "cancelled"
