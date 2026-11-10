"""Server-Sent Events (SSE) Streaming Engine & Circular Replay Buffer.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 4.2 & Contract C4.
"""

from __future__ import annotations
import asyncio
import collections
import dataclasses
import datetime
import json
import time
from typing import Any, AsyncGenerator

SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@dataclasses.dataclass
class BufferedEvent:
    seq: int
    event_id: str
    event_type: str
    data: dict[str, Any]
    raw_frame: str
    timestamp: float


def format_sse_frame(event_id: str, event_type: str, data: dict[str, Any]) -> str:
    """Format standard W3C Server-Sent Event frame terminated by double newlines."""
    data_str = json.dumps(data, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data_str}\n\n"


def parse_last_event_id(last_event_id: str | None, turn_id: str) -> int:
    """Parse Last-Event-ID header into sequence integer.
    
    - If absent: returns 0.
    - If matching {turn_id}_{seq}: returns int(seq) (seq=0 replays from beginning).
    - If malformed (non-numeric or non-matching): gracefully falls back to 0.
    """
    if not last_event_id:
        return 0
    if last_event_id.startswith(f"{turn_id}_"):
        suffix = last_event_id[len(turn_id) + 1 :]
        try:
            val = int(suffix)
            return max(0, val)
        except ValueError:
            return 0
    # Malformed ID fallback
    return 0


class TurnEventBuffer:
    """Circular event buffer for an active or recently completed chat turn."""

    def __init__(
        self,
        turn_id: str,
        thread_id: str,
        max_events: int = 256,
        retention_seconds: float = 60.0,
    ):
        self.turn_id = turn_id
        self.thread_id = thread_id
        self.max_events = max_events
        self.retention_seconds = retention_seconds
        self.events: collections.deque[BufferedEvent] = collections.deque(maxlen=max_events)
        self.next_seq: int = 1
        self.is_completed: bool = False
        self.is_cancelled: bool = False
        self.created_at: float = time.time()
        self.completed_at: float | None = None
        self._subscribers: set[asyncio.Queue[BufferedEvent | None]] = set()

    def append_event(self, event_type: str, data: dict[str, Any]) -> BufferedEvent:
        seq = self.next_seq
        self.next_seq += 1
        event_id = f"{self.turn_id}_{seq:03d}"
        payload = dict(data)
        payload.setdefault("thread_id", self.thread_id)
        payload.setdefault("turn_id", self.turn_id)

        # Dual-key normalization for cross-contract client compatibility
        if event_type == "tool_call_started":
            tool_name = payload.get("tool_name") or payload.get("tool") or "search_memory"
            call_id = payload.get("tool_call_id") or payload.get("call_id") or f"tc_{seq:03d}"
            args = payload.get("arguments") or payload.get("args") or {}
            payload["tool"] = tool_name
            payload["tool_name"] = tool_name
            payload["call_id"] = call_id
            payload["tool_call_id"] = call_id
            payload["args"] = args
            payload["arguments"] = args
            payload["status"] = "running"
        elif event_type == "tool_call_completed":
            tool_name = payload.get("tool_name") or payload.get("tool") or "search_memory"
            call_id = payload.get("tool_call_id") or payload.get("call_id") or f"tc_{seq:03d}"
            payload["tool"] = tool_name
            payload["tool_name"] = tool_name
            payload["call_id"] = call_id
            payload["tool_call_id"] = call_id
            payload["status"] = "completed"
            payload.setdefault("result", {})
            payload.setdefault("elapsed_ms", 120)
        elif event_type == "turn_completed":
            payload.setdefault("status", "completed")
            payload.setdefault("completed_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
            payload.setdefault("tokens", 24)
        elif event_type == "turn_failed":
            payload.setdefault("status", "failed")
            payload.setdefault("error_code", "ERROR")
            payload.setdefault("turn_error_kind", "error")
            payload.setdefault("turn_failed_at", datetime.datetime.now(datetime.timezone.utc).isoformat())

        raw_frame = format_sse_frame(event_id, event_type, payload)
        bev = BufferedEvent(
            seq=seq,
            event_id=event_id,
            event_type=event_type,
            data=payload,
            raw_frame=raw_frame,
            timestamp=time.time(),
        )
        self.events.append(bev)

        # Notify active streaming subscribers
        for q in list(self._subscribers):
            try:
                q.put_nowait(bev)
            except Exception:
                pass

        if event_type in ("turn_completed", "turn_failed"):
            self.is_completed = True
            self.completed_at = time.time()
            for q in list(self._subscribers):
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

        return bev

    def mark_cancelled(self) -> None:
        if self.is_completed or self.is_cancelled:
            return
        self.is_cancelled = True
        self.append_event(
            "turn_failed",
            {
                "error_code": "CANCELLED",
                "turn_error_kind": "cancelled",
                "message": "Turn was cancelled by client",
                "turn_failed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
        )

    def get_events_after(self, last_seq: int) -> list[BufferedEvent]:
        return [ev for ev in self.events if ev.seq > last_seq]

    def is_expired(self, now: float | None = None) -> bool:
        now = now or time.time()
        if self.completed_at is not None:
            return (now - self.completed_at) > self.retention_seconds
        return (now - self.created_at) > 180.0


class TurnStreamManager:
    """Registry managing active and completed turn circular replay buffers."""

    def __init__(self, retention_seconds: float = 60.0):
        self.retention_seconds = retention_seconds
        self._buffers: dict[str, TurnEventBuffer] = {}
        self._cancellations: set[str] = set()
        self._expired_turns: set[str] = set()

    def get_or_create(self, turn_id: str, thread_id: str) -> TurnEventBuffer:
        self.cleanup()
        if turn_id not in self._buffers:
            self._buffers[turn_id] = TurnEventBuffer(
                turn_id=turn_id,
                thread_id=thread_id,
                retention_seconds=self.retention_seconds,
            )
        return self._buffers[turn_id]

    def get(self, turn_id: str) -> TurnEventBuffer | None:
        self.cleanup()
        return self._buffers.get(turn_id)

    def is_expired_turn(self, turn_id: str) -> bool:
        self.cleanup()
        return turn_id in self._expired_turns

    def mark_expired(self, turn_id: str) -> None:
        if turn_id in self._buffers:
            del self._buffers[turn_id]
        self._expired_turns.add(turn_id)

    def cancel(self, turn_id: str) -> None:
        self._cancellations.add(turn_id)
        buf = self._buffers.get(turn_id)
        if buf and not buf.is_completed:
            buf.mark_cancelled()

    def is_cancelled(self, turn_id: str) -> bool:
        return turn_id in self._cancellations

    def cleanup(self) -> None:
        now = time.time()
        expired = [tid for tid, buf in self._buffers.items() if buf.is_expired(now)]
        for tid in expired:
            del self._buffers[tid]
            self._expired_turns.add(tid)
