"""Timeline & Speaker Diarization harness conforming to Contract C5 & Contract C6."""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any

# Priority tiers per Contract C5
P0_LIVE_CARD = 0
P1_ANALYZING_CHAIN = 1
P2_ACTIVITY = 2
P3_SCATTERED_CHAT = 3
P4_COVERAGE = 4


@dataclass
class TimelineInterval:
    interval_id: str
    start_ms: int
    end_ms: int
    tier: int  # 0 (P0) to 4 (P4)
    item_type: str
    payload: dict[str, Any] | None = None

    def __post_init__(self):
        if self.start_ms > self.end_ms:
            raise ValueError(f"Invalid interval: start_ms {self.start_ms} > end_ms {self.end_ms}")

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def clip_timeline_intervals(intervals: list[TimelineInterval]) -> list[TimelineInterval]:
    """
    Contract C5: 5-Tier Priority Interval Clipping Engine.
    Higher-priority intervals (lower tier number) subtract from overlapping lower-priority intervals.
    Lower intervals are truncated, split into disjoint sub-intervals, or dropped if fully covered.
    Resulting intervals are deterministically sorted by start_ms without overlaps.
    """
    if not intervals:
        return []

    # Sort intervals initially by tier ascending (P0 first, P4 last), then by start_ms
    sorted_input = sorted(intervals, key=lambda x: (x.tier, x.start_ms, x.end_ms))

    # We will build a list of non-overlapping intervals, starting with highest priority
    placed: list[TimelineInterval] = []

    for item in sorted_input:
        if item.duration_ms <= 0:
            continue

        # Subtract all existing placed higher-priority intervals from current item
        current_pieces: list[tuple[int, int]] = [(item.start_ms, item.end_ms)]

        for higher in placed:
            h_start, h_end = higher.start_ms, higher.end_ms
            new_pieces = []
            for p_start, p_end in current_pieces:
                # Check overlap
                if h_end <= p_start or h_start >= p_end:
                    # No overlap
                    new_pieces.append((p_start, p_end))
                else:
                    # Overlap exists: subtract [h_start, h_end] from [p_start, p_end]
                    # Left remainder
                    if p_start < h_start:
                        new_pieces.append((p_start, h_start))
                    # Right remainder
                    if p_end > h_end:
                        new_pieces.append((h_end, p_end))
            current_pieces = new_pieces
            if not current_pieces:
                break

        # For all surviving pieces of current item, create intervals
        for idx, (p_start, p_end) in enumerate(current_pieces):
            if p_end > p_start:
                sub_id = f"{item.interval_id}_part{idx}" if len(current_pieces) > 1 else item.interval_id
                placed.append(TimelineInterval(
                    interval_id=sub_id,
                    start_ms=p_start,
                    end_ms=p_end,
                    tier=item.tier,
                    item_type=item.item_type,
                    payload=item.payload,
                ))

    # Sort final placed intervals strictly by start_ms, then tier
    return sorted(placed, key=lambda x: (x.start_ms, x.end_ms, x.tier))


def resolve_speaker_name(
    speaker_id: str | None,
    utterance_context: dict[str, Any],
) -> str:
    """
    Contract C6: 3-Tier Dynamic Speaker Fallback Resolution.
    - Tier 1: If speaker_id is present in SpeakerProfiles and has non-empty name -> return name.
    - Tier 2: If speaker_id is in active activity roster (MemoryEventSpeakerRosterEntry) with suspected_name -> return suspected_name.
    - Tier 3: Else resolve via session-scoped unknown_speaker_index -> return 'Speaker {unknown_speaker_index}'.
    """
    speaker_profiles = utterance_context.get("speaker_profiles", {})
    roster_suspects = utterance_context.get("active_roster", {})

    # Tier 1: Enrolled / Named Speaker Profiles
    if speaker_id and speaker_id in speaker_profiles:
        profile = speaker_profiles[speaker_id]
        name = profile.get("name") if isinstance(profile, dict) else getattr(profile, "name", None)
        if name and name.strip():
            return name.strip()

    # Tier 2: Activity Roster Suspects
    if speaker_id and speaker_id in roster_suspects:
        roster_entry = roster_suspects[speaker_id]
        suspected = roster_entry.get("suspected_name") if isinstance(roster_entry, dict) else getattr(roster_entry, "suspected_name", None)
        if suspected and suspected.strip():
            return suspected.strip()

    # Tier 3: Sequential Unknown Speaker Index
    unknown_index = utterance_context.get("unknown_speaker_index", 1)
    return f"Speaker {unknown_index}"


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two 512-dim embedding vectors."""
    if len(v1) != len(v2) or len(v1) == 0:
        raise ValueError(f"Vector dimension mismatch: {len(v1)} vs {len(v2)}")
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


def unit_normalize(v: list[float]) -> list[float]:
    """Normalize vector to unit L2 norm."""
    norm = math.sqrt(sum(a * a for a in v))
    if norm == 0.0:
        return [0.0] * len(v)
    return [a / norm for a in v]


def compute_centroid(embeddings: list[list[float]]) -> list[float]:
    """Compute unit-normalized centroid from a list of embeddings."""
    if not embeddings:
        return []
    dim = len(embeddings[0])
    centroid = [0.0] * dim
    for emb in embeddings:
        for d in range(dim):
            centroid[d] += emb[d]
    return unit_normalize(centroid)
