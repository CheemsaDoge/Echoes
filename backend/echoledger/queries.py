from __future__ import annotations
import json
from .db import Database

def list_events(db: Database, from_ms=0, to_ms=9_000_000_000_000_000, limit=100) -> list[dict]:
    rows = db.all("""SELECT e.*,t.name AS type_name,t.color FROM events e
        JOIN event_types t ON t.id=e.type_id WHERE e.end_ms>=? AND e.start_ms<?
        ORDER BY e.start_ms DESC LIMIT ?""", (from_ms, to_ms, min(500, max(1, limit))))
    for row in rows:
        row["speaker_names"] = [r["name"] for r in db.all("""SELECT DISTINCT p.name FROM event_segments es
            JOIN segments s ON s.id=es.segment_id JOIN speakers p ON p.id=s.speaker_id
            WHERE es.event_id=? ORDER BY p.name""", (row["id"],))]
        row["unassigned_labels"] = db.one("""SELECT COUNT(DISTINCT s.local_speaker) AS n FROM segments s
            JOIN event_segments es ON es.segment_id=s.id WHERE es.event_id=? AND s.speaker_id IS NULL""", (row["id"],))["n"]
    return rows

def get_event(db: Database, event_id: str) -> dict | None:
    row = db.one("SELECT e.*, t.name AS type_name,t.color FROM events e JOIN event_types t ON t.id=e.type_id WHERE e.id=?", (event_id,))
    if row is None:
        return None
    segments = db.all("""SELECT s.*,p.name AS speaker_name FROM segments s JOIN event_segments es ON es.segment_id=s.id
        LEFT JOIN speakers p ON p.id=s.speaker_id WHERE es.event_id=? ORDER BY s.start_ms,s.ordinal""", (event_id,))
    for s in segments:
        s["source_spans"] = json.loads(s["source_spans"])
    row["segments"] = segments
    return row

def search_memories(db: Database, q="", confirmed_only=True, from_ms=0, to_ms=9_000_000_000_000_000, limit=100):
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return db.all("""SELECT m.*,e.start_ms,e.end_ms,e.type_id,e.is_demo FROM memories m
        JOIN events e ON e.id=m.event_id WHERE m.text LIKE ? ESCAPE '\\'
        AND (?=0 OR m.confirmed=1) AND e.end_ms>=? AND e.start_ms<?
        ORDER BY e.start_ms DESC LIMIT ?""", ("%" + escaped + "%", int(confirmed_only), from_ms, to_ms, min(200, max(1, limit))))
