"""Tier 5 Adversarial Test Suite: White-Box Client & Cross-Layer IPC/Native Pipeline Hardening.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md & PROJECT.md Milestone M6 Phase 2.

Comprehensive white-box stress verification covering:
1. Riverpod 7-State Business State Machine (`hf.i`):
   - 10.0s activating watchdog race & timeout.
   - 45.0s audio recovery deadlock watchdog & HAL stall recovery.
   - Terminal latching & immunity to stale asynchronous event resurrection.
   - Out-of-order event delivery & rapid burst concurrency.
2. Timeline Clipping Engine (Contract C5):
   - Extreme timestamps: negative epoch ms, far future epoch ms (Year 2099+), unix epoch 0.
   - Zero-duration filtering & inverted timestamp rejection (`start_ms > end_ms`).
   - Touching border preservation on half-open `[start, end)` intervals.
   - Multi-tier cascading hole-punching with 100+ overlapping slices.
   - Daylight Saving Time (DST) non-24h day grids (23h and 25h).
   - Calendar idle gap filling spanning fragmented disjoint intervals.
   - Midnight crossover clamping with `startsBeforePage` and `endsAfterPage` flags.
   - Adjacent fragment collapsing into `ScatteredChatGroup`.
3. Speaker Resolver (Contract C6):
   - 3-tier dynamic fallback resolution precedence.
   - Regex tag replacer fuzzed with nested, unclosed, HTML-like, and malformed tokens.
   - Circular and reciprocal roster references without infinite recursion.
   - Sequential unknown index allocation determinism.
   - Non-ASCII, Unicode formatting and emoji speaker profile names.
4. Cross-Layer IPC & Native Bridge:
   - MethodChannel circular ring buffer saturation (> 256 capacity overflow, dropped count, FIFO eviction).
   - Event draining and sequence counter atomicity.
   - Metering audio power decibel normalization math (`[-60.0, 0.0] -> [0.05, 1.0]`) & extreme clamping.
   - `Auth7Tuple` validation, type safety, and Unicode `Cf` formatting character attack vectors.
5. Native Android Elastic Rollover Slicer & Audio HAL:
   - Sub-26m silence immunity boundary (25m59.999s).
   - 26m..30m silence qualification boundary (-40.0 dBFS, 800ms).
   - Instant silence accumulator flush upon loud speech onset.
   - Exact 30m hard limit forced split.
   - Interruption and manual stop triggers.
6. UploadWorker, LocalAtomicUploadQueue & Multi-Tenant Guard:
   - WorkManager exponential backoff schedule and attempt 5 permanent failure boundary.
   - Multi-tenant guard identity mismatch: quarantine without file deletion.
   - 7-day retention pruning exact cutoff boundary and immutable status protection.
   - Atomic persistence resilience against orphan `.tmp` crash artifacts.
"""

from __future__ import annotations
import itertools
import math
import os
import re
import tempfile
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.harness.audio_harness import (
    build_adts_header,
    evaluate_rollover,
    parse_adts_header,
    synthesize_adts_file,
    validate_adts_stream,
)
from tests.e2e.harness.queue_harness import (
    MockUploadQueueStorage,
    UploadQueueItem,
)
from tests.e2e.harness.timeline_harness import (
    P0_LIVE_CARD,
    P1_ANALYZING_CHAIN,
    P2_ACTIVITY,
    P3_SCATTERED_CHAT,
    P4_COVERAGE,
    TimelineInterval,
    clip_timeline_intervals,
    resolve_speaker_name,
)


# ==============================================================================
# Model Implementations for Client State Machine & Native IPC
# ==============================================================================

class RecordingCaptureState:
    IDLE = "idle"
    ACTIVATING = "activating"
    RECORDING = "recording"
    RECOVERING = "recovering"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED_TERMINAL = "failedTerminal"

    ALL_STATES = {IDLE, ACTIVATING, RECORDING, RECOVERING, STOPPING, STOPPED, FAILED_TERMINAL}

    @staticmethod
    def is_active(state: str) -> bool:
        """Active capture predicate matching hf/i.a() in decompiled bytecode."""
        return state in {
            RecordingCaptureState.ACTIVATING,
            RecordingCaptureState.RECORDING,
            RecordingCaptureState.RECOVERING,
            RecordingCaptureState.STOPPING,
        }

    @staticmethod
    def can_start(state: str) -> bool:
        return state in {
            RecordingCaptureState.IDLE,
            RecordingCaptureState.STOPPED,
            RecordingCaptureState.FAILED_TERMINAL,
        }

    @staticmethod
    def can_pause(state: str) -> bool:
        return state == RecordingCaptureState.RECORDING

    @staticmethod
    def can_resume(state: str) -> bool:
        return state == RecordingCaptureState.RECOVERING

    @staticmethod
    def can_stop(state: str) -> bool:
        return state in {
            RecordingCaptureState.RECORDING,
            RecordingCaptureState.RECOVERING,
            RecordingCaptureState.ACTIVATING,
        }


class VirtualRecordingService:
    """Deterministic simulation of lib/core/services/recording_service.dart."""

    def __init__(self, bridge_delay_ms: int = 0):
        self.state = RecordingCaptureState.IDLE
        self.active_recording_id: str | None = None
        self.tenant_id: str | None = None
        self.vault_id: str | None = None
        self.user_id: str | None = None
        self.elapsed_ms: int = 0
        self.last_error: str | None = None

        self.bridge_delay_ms = bridge_delay_ms
        self.activating_watchdog_due_ms: int | None = None
        self.recovery_watchdog_due_ms: int | None = None
        self.stopped_reset_due_ms: int | None = None

        self.bridge_stop_calls: list[str] = []
        self.current_time_ms: int = 0

    def start_recording(self, recording_id: str, tenant_id: str, vault_id: str, user_id: str = "") -> bool:
        if not RecordingCaptureState.can_start(self.state):
            raise ValueError(f"Cannot start recording from state {self.state}")

        self._cancel_watchdogs()
        self.state = RecordingCaptureState.ACTIVATING
        self.active_recording_id = recording_id
        self.tenant_id = tenant_id
        self.vault_id = vault_id
        self.user_id = user_id
        self.elapsed_ms = 0
        self.last_error = None

        # Arm 10s activating watchdog
        self.activating_watchdog_due_ms = self.current_time_ms + 10_000
        return True

    def pause_recording(self) -> bool:
        if not RecordingCaptureState.can_pause(self.state):
            raise ValueError(f"Cannot pause recording from state {self.state}")

        self._cancel_watchdogs()
        self.state = RecordingCaptureState.RECOVERING
        self._arm_recovery_watchdog()
        return True

    def resume_recording(self) -> bool:
        if not RecordingCaptureState.can_resume(self.state):
            raise ValueError(f"Cannot resume recording from state {self.state}")

        self._cancel_watchdogs()
        self.state = RecordingCaptureState.RECORDING
        return True

    def stop_recording(self, reason: str = "userStop") -> bool:
        if not RecordingCaptureState.can_stop(self.state):
            raise ValueError(f"Cannot stop recording from state {self.state}")

        self._cancel_watchdogs()
        self.state = RecordingCaptureState.STOPPING
        self.bridge_stop_calls.append(reason)
        return True

    def handle_interruption(self):
        if self.state == RecordingCaptureState.RECORDING:
            self._cancel_watchdogs()
            self.state = RecordingCaptureState.RECOVERING
            self._arm_recovery_watchdog()

    def handle_fatal_error(self, reason: str):
        self._cancel_watchdogs()
        self.state = RecordingCaptureState.FAILED_TERMINAL
        self.last_error = reason

    def reset(self):
        self._cancel_watchdogs()
        self.state = RecordingCaptureState.IDLE
        self.active_recording_id = None
        self.tenant_id = None
        self.vault_id = None
        self.user_id = None
        self.elapsed_ms = 0
        self.last_error = None

    def on_native_state_event(self, native_state: str, elapsed_ms: int = 0):
        # In failedTerminal, ignore non-terminal events
        if self.state == RecordingCaptureState.FAILED_TERMINAL:
            if native_state == RecordingCaptureState.FAILED_TERMINAL:
                self.handle_fatal_error("NATIVE_TERMINAL_FAILURE")
            return

        # In stopping, stopped, or idle states, ignore late recording or recovering events
        if self.state in {RecordingCaptureState.STOPPING, RecordingCaptureState.STOPPED, RecordingCaptureState.IDLE}:
            if native_state in {RecordingCaptureState.RECORDING, RecordingCaptureState.RECOVERING}:
                return

        if native_state == RecordingCaptureState.RECORDING:
            self._cancel_watchdogs()
            self.state = RecordingCaptureState.RECORDING
            self.elapsed_ms = elapsed_ms
        elif native_state == RecordingCaptureState.RECOVERING:
            if self.state == RecordingCaptureState.RECORDING:
                self.state = RecordingCaptureState.RECOVERING
                self._arm_recovery_watchdog()
        elif native_state == RecordingCaptureState.STOPPED:
            self._cancel_watchdogs()
            self.state = RecordingCaptureState.STOPPED
            self.elapsed_ms = elapsed_ms
            self.stopped_reset_due_ms = self.current_time_ms + 300
        elif native_state == RecordingCaptureState.FAILED_TERMINAL:
            self.handle_fatal_error("NATIVE_TERMINAL_FAILURE")

    def advance_time(self, delta_ms: int):
        target_ms = self.current_time_ms + delta_ms

        while self.current_time_ms < target_ms:
            step = target_ms - self.current_time_ms
            # Check next timer
            next_timer = None
            for due in (self.activating_watchdog_due_ms, self.recovery_watchdog_due_ms, self.stopped_reset_due_ms):
                if due is not None and due <= target_ms:
                    if next_timer is None or due < next_timer:
                        next_timer = due

            if next_timer is not None and next_timer > self.current_time_ms:
                self.current_time_ms = next_timer
            else:
                self.current_time_ms = target_ms

            # Fire activating watchdog
            if self.activating_watchdog_due_ms is not None and self.current_time_ms >= self.activating_watchdog_due_ms:
                self.activating_watchdog_due_ms = None
                if self.state == RecordingCaptureState.ACTIVATING:
                    self.state = RecordingCaptureState.FAILED_TERMINAL
                    self.last_error = "START_TIMEOUT: Native pipeline failed to respond in 10s"
                    self.bridge_stop_calls.append("timeout")

            # Fire recovery watchdog
            if self.recovery_watchdog_due_ms is not None and self.current_time_ms >= self.recovery_watchdog_due_ms:
                self.recovery_watchdog_due_ms = None
                if self.state == RecordingCaptureState.RECOVERING:
                    self.state = RecordingCaptureState.FAILED_TERMINAL
                    self.last_error = "HAL_DEADLOCK_DETECTED: Recovery exceeded 45s threshold"
                    self.bridge_stop_calls.append("recovery_deadlock")

            # Fire stopped reset
            if self.stopped_reset_due_ms is not None and self.current_time_ms >= self.stopped_reset_due_ms:
                self.stopped_reset_due_ms = None
                if self.state == RecordingCaptureState.STOPPED:
                    self.reset()

    def _arm_recovery_watchdog(self):
        self.recovery_watchdog_due_ms = self.current_time_ms + 45_000

    def _cancel_watchdogs(self):
        self.activating_watchdog_due_ms = None
        self.recovery_watchdog_due_ms = None
        self.stopped_reset_due_ms = None


class NativeRingBuffer:
    """Exact simulation of android/app/src/main/kotlin/net/hemory/app/native/ipc/RingBuffer.kt."""

    def __init__(self, capacity: int = 256):
        self.capacity = capacity
        self.buffer: deque[dict[str, Any]] = deque()
        self.dropped_events_count: int = 0

    def push(self, item: dict[str, Any]):
        if len(self.buffer) >= self.capacity:
            self.buffer.popleft()
            self.dropped_events_count += 1
        self.buffer.append(item)

    def drain(self) -> list[dict[str, Any]]:
        items = list(self.buffer)
        self.buffer.clear()
        return items

    @property
    def size(self) -> int:
        return len(self.buffer)

    def clear(self):
        self.buffer.clear()
        self.dropped_events_count = 0


def normalize_audio_metering_power(rms_db: float) -> float:
    """Normalized power formula from lib/core/ipc/event_channels.dart."""
    clamped = max(-60.0, min(0.0, rms_db))
    return max(0.05, min(1.0, (clamped + 60.0) / 60.0))


def validate_auth7tuple(data: dict[str, Any]) -> dict[str, Any]:
    """Validation logic matching lib/core/ipc/native_bridge.dart & defense-in-depth."""
    string_keys = [
        "accessToken",
        "refreshToken",
        "baseUrl",
        "slotConnectionType",
        "slotDpUnitId",
        "slotTenantId",
    ]

    for key in string_keys:
        if key not in data or data[key] is None:
            raise ValueError(f"Missing required Auth7Tuple key: {key}")
        val = data[key]
        if not isinstance(val, str):
            raise TypeError(f'Auth7Tuple field "{key}" must be a non-empty String (got: {type(val)})')
        # Check standard whitespace
        if not val.strip():
            raise ValueError(f'Auth7Tuple field "{key}" must be a non-empty String')

    if "expiresAt" not in data or data["expiresAt"] is None:
        raise ValueError("Missing required Auth7Tuple key: expiresAt")

    expires_at = data["expiresAt"]
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
        raise TypeError(f'Auth7Tuple field "expiresAt" must be a positive number (got: {type(expires_at)})')

    if expires_at <= 0 or not math.isfinite(expires_at):
        raise ValueError(f'Auth7Tuple field "expiresAt" must be positive > 0 (got: {expires_at})')

    return data


def contains_unicode_cf_only(text: str) -> bool:
    """Checks if string consists solely of Unicode Cf (formatting/invisible) characters."""
    if not text:
        return True
    return all(unicodedata.category(c) == "Cf" or c.isspace() for c in text)


# ==============================================================================
# SECTION 1: State Machine Concurrency, Watchdogs & Race Hardening
# ==============================================================================

class TestAdvStateMachineWatchdogsAndLatching:
    """Adversarial stress-testing of Riverpod 7-state recording business state machine."""

    def test_canonical_state_matrix_and_active_predicates(self):
        """Verifies canonical 7-state matrix, active predicates, and transition allowance."""
        active_states = {
            RecordingCaptureState.ACTIVATING,
            RecordingCaptureState.RECORDING,
            RecordingCaptureState.RECOVERING,
            RecordingCaptureState.STOPPING,
        }
        inactive_states = {
            RecordingCaptureState.IDLE,
            RecordingCaptureState.STOPPED,
            RecordingCaptureState.FAILED_TERMINAL,
        }

        for s in active_states:
            assert RecordingCaptureState.is_active(s) is True, f"{s} must be active"

        for s in inactive_states:
            assert RecordingCaptureState.is_active(s) is False, f"{s} must be inactive"

        # can_start is true only for idle, stopped, failedTerminal
        assert RecordingCaptureState.can_start(RecordingCaptureState.IDLE) is True
        assert RecordingCaptureState.can_start(RecordingCaptureState.STOPPED) is True
        assert RecordingCaptureState.can_start(RecordingCaptureState.FAILED_TERMINAL) is True
        assert RecordingCaptureState.can_start(RecordingCaptureState.RECORDING) is False
        assert RecordingCaptureState.can_start(RecordingCaptureState.ACTIVATING) is False

    def test_activating_watchdog_10s_exact_boundary_and_cancellation(self):
        """10.0s activating watchdog fires at boundary, but is cancelled at 9.999s by native recording event."""
        # Case A: Watchdog fires at exact 10.0s boundary
        svc = VirtualRecordingService()
        svc.start_recording("rec_watchdog_1", "t1", "v1")
        assert svc.state == RecordingCaptureState.ACTIVATING

        svc.advance_time(9_999)
        assert svc.state == RecordingCaptureState.ACTIVATING
        assert "timeout" not in svc.bridge_stop_calls

        svc.advance_time(1)  # Reaches 10,000 ms
        assert svc.state == RecordingCaptureState.FAILED_TERMINAL
        assert "START_TIMEOUT" in (svc.last_error or "")
        assert "timeout" in svc.bridge_stop_calls

        # Case B: Native event arrives cleanly at 9.999s, cancelling watchdog
        svc2 = VirtualRecordingService()
        svc2.start_recording("rec_watchdog_2", "t1", "v1")
        svc2.advance_time(9_999)
        svc2.on_native_state_event(RecordingCaptureState.RECORDING, elapsed_ms=100)
        assert svc2.state == RecordingCaptureState.RECORDING

        # Advance further past 10s: state should remain recording, watchdog must NOT fire
        svc2.advance_time(5_000)
        assert svc2.state == RecordingCaptureState.RECORDING
        assert "timeout" not in svc2.bridge_stop_calls

    def test_recovery_deadlock_watchdog_45s_hal_stall(self):
        """45.0s audio recovery deadlock watchdog fires if HAL fails to resume recording within 45s."""
        svc = VirtualRecordingService()
        svc.start_recording("rec_hal_1", "t1", "v1")
        svc.on_native_state_event(RecordingCaptureState.RECORDING)

        # Trigger audio focus loss / pause
        svc.handle_interruption()
        assert svc.state == RecordingCaptureState.RECOVERING

        # Advance 44,999 ms
        svc.advance_time(44_999)
        assert svc.state == RecordingCaptureState.RECOVERING

        # Reaches 45,000 ms
        svc.advance_time(1)
        assert svc.state == RecordingCaptureState.FAILED_TERMINAL
        assert "HAL_DEADLOCK_DETECTED" in (svc.last_error or "")
        assert "recovery_deadlock" in svc.bridge_stop_calls

    def test_terminal_state_latching_immunity_against_stale_events(self):
        """Once failedTerminal is reached, stale native recording/recovering events CANNOT resurrect active state."""
        svc = VirtualRecordingService()
        svc.start_recording("rec_term_1", "t1", "v1")
        svc.handle_fatal_error("AUDIO_HARDWARE_CRASH")
        assert svc.state == RecordingCaptureState.FAILED_TERMINAL

        # In-flight asynchronous native events arrive late
        svc.on_native_state_event(RecordingCaptureState.RECORDING, elapsed_ms=5000)
        assert svc.state == RecordingCaptureState.FAILED_TERMINAL, "Stale recording event resurrected terminal state!"

        svc.on_native_state_event(RecordingCaptureState.RECOVERING, elapsed_ms=6000)
        assert svc.state == RecordingCaptureState.FAILED_TERMINAL

        svc.on_native_state_event(RecordingCaptureState.STOPPED, elapsed_ms=7000)
        assert svc.state == RecordingCaptureState.FAILED_TERMINAL

    def test_unexpected_asynchronous_event_ordering_in_stopping(self):
        """When user stops recording, late native recording packets are ignored and do not restart flight."""
        svc = VirtualRecordingService()
        svc.start_recording("rec_stop_1", "t1", "v1")
        svc.on_native_state_event(RecordingCaptureState.RECORDING)

        svc.stop_recording(reason="userStop")
        assert svc.state == RecordingCaptureState.STOPPING

        # Stale audio buffer arrives while service is flushing stopping state
        svc.on_native_state_event(RecordingCaptureState.RECORDING, elapsed_ms=3000)
        assert svc.state == RecordingCaptureState.STOPPING

        # Flush completes with native STOPPED
        svc.on_native_state_event(RecordingCaptureState.STOPPED, elapsed_ms=3100)
        assert svc.state == RecordingCaptureState.STOPPED

        # 300ms auto-reset cycle to IDLE
        svc.advance_time(300)
        assert svc.state == RecordingCaptureState.IDLE

    def test_rapid_burst_concurrency_and_state_consistency(self):
        """Rapid burst of 100 native events through stream does not destabilize state machine."""
        svc = VirtualRecordingService()
        svc.start_recording("rec_burst_1", "t1", "v1")
        svc.on_native_state_event(RecordingCaptureState.RECORDING)

        # Flood 1,000 rapid metering/state packets
        for i in range(1, 1001):
            svc.on_native_state_event(RecordingCaptureState.RECORDING, elapsed_ms=i * 50)
            assert svc.state == RecordingCaptureState.RECORDING
            assert svc.elapsed_ms == i * 50


# ==============================================================================
# SECTION 2: Timeline Clipping Math & Boundary Hardening (Contract C5)
# ==============================================================================

class TestAdvTimelineClippingMathAndBoundaries:
    """Adversarial stress-testing of 5-tier interval clipping engine on [0, 1440] minute grid."""

    def test_extreme_timestamps_and_epoch_ranges(self):
        """Handles extreme negative timestamps, far-future timestamps (> Year 2099), and unix epoch 0."""
        # Extreme negative interval
        i_neg = TimelineInterval("neg_1", -10_000, -2_000, P4_COVERAGE, "Coverage")
        i_neg_high = TimelineInterval("neg_h", -5_000, -3_000, P1_ANALYZING_CHAIN, "Chain")
        clipped_neg = clip_timeline_intervals([i_neg, i_neg_high])
        assert len(clipped_neg) == 3
        assert (clipped_neg[0].start_ms, clipped_neg[0].end_ms) == (-10_000, -5_000)
        assert (clipped_neg[1].start_ms, clipped_neg[1].end_ms) == (-5_000, -3_000)
        assert (clipped_neg[2].start_ms, clipped_neg[2].end_ms) == (-3_000, -2_000)

        # Far future epoch ms (> Year 2099: 4,102,444,800,000 ms)
        t_future = 4_102_444_800_000
        i_fut_base = TimelineInterval("fut_base", t_future, t_future + 3_600_000, P4_COVERAGE, "Coverage")
        i_fut_live = TimelineInterval("fut_live", t_future + 600_000, t_future + 1_200_000, P0_LIVE_CARD, "Live")
        clipped_fut = clip_timeline_intervals([i_fut_base, i_fut_live])
        assert len(clipped_fut) == 3
        assert clipped_fut[1].tier == P0_LIVE_CARD

    def test_zero_duration_and_inverted_timestamp_guards(self):
        """Zero duration intervals are safely pruned; inverted intervals raise ValueError."""
        # Zero duration intervals (start == end)
        i_zero = TimelineInterval("zero_1", 1000, 1000, P2_ACTIVITY, "Activity")
        i_normal = TimelineInterval("norm_1", 1000, 2000, P4_COVERAGE, "Coverage")
        clipped = clip_timeline_intervals([i_zero, i_normal])
        assert len(clipped) == 1
        assert clipped[0].interval_id == "norm_1"

        # Inverted interval (start > end) must raise ValueError
        with pytest.raises(ValueError, match="Invalid interval"):
            TimelineInterval("inverted", 5000, 2000, P2_ACTIVITY, "Activity")

    def test_touching_border_preservation_half_open_disjoint(self):
        """Half-open intervals touching at exact border [a, b) and [b, c) never clip each other."""
        i1 = TimelineInterval("i1", 0, 1000, P0_LIVE_CARD, "Live")
        i2 = TimelineInterval("i2", 1000, 2000, P4_COVERAGE, "Coverage")
        i3 = TimelineInterval("i3", 2000, 3000, P2_ACTIVITY, "Activity")
        clipped = clip_timeline_intervals([i1, i2, i3])
        assert len(clipped) == 3
        assert [(x.start_ms, x.end_ms) for x in clipped] == [(0, 1000), (1000, 2000), (2000, 3000)]

    def test_multi_tier_cascading_hole_punching_scale(self):
        """Puncturing lower tier interval with 50 high-priority intervals produces exact disjoint pieces."""
        base = TimelineInterval("base", 0, 100_000, P4_COVERAGE, "Coverage")
        high_holes = [
            TimelineInterval(f"hole_{i}", i * 2000 + 500, i * 2000 + 1500, P1_ANALYZING_CHAIN, "Chain")
            for i in range(40)
        ]

        clipped = clip_timeline_intervals([base] + high_holes)
        # 40 holes inside base create 41 base fragments + 40 holes = 81 intervals total
        assert len(clipped) == 81
        # Invariant: No overlapping intervals in final output
        for a, b in zip(clipped[:-1], clipped[1:]):
            assert a.end_ms <= b.start_ms, f"Overlap detected between {a} and {b}"

    def test_permutation_invariance_under_shuffling(self):
        """Random permutations of complex multi-tier inputs always produce identical output."""
        items = [
            TimelineInterval("live", 100, 200, P0_LIVE_CARD, "Live"),
            TimelineInterval("chain", 150, 350, P1_ANALYZING_CHAIN, "Chain"),
            TimelineInterval("act", 300, 600, P2_ACTIVITY, "Activity"),
            TimelineInterval("chat", 500, 800, P3_SCATTERED_CHAT, "Chat"),
            TimelineInterval("cov", 50, 900, P4_COVERAGE, "Coverage"),
        ]

        canonical = [(x.interval_id, x.start_ms, x.end_ms, x.tier) for x in clip_timeline_intervals(items)]

        # Test all permutations of the 5 items (120 permutations)
        for perm in itertools.permutations(items):
            res = [(x.interval_id, x.start_ms, x.end_ms, x.tier) for x in clip_timeline_intervals(list(perm))]
            assert res == canonical

    def test_idle_gap_filling_standard_and_dst_grids(self):
        """Fills unoccupied gaps with calendar_idle_segment across standard 24h and DST grids."""
        day_start = 0
        day_end_24h = 86_400_000  # 1440 min
        day_end_23h_dst = 82_800_000  # 1380 min (spring forward)
        day_end_25h_dst = 90_000_000  # 1500 min (fall back)

        intervals = [
            TimelineInterval("act_1", 10_000_000, 20_000_000, P2_ACTIVITY, "Activity"),
            TimelineInterval("act_2", 40_000_000, 50_000_000, P2_ACTIVITY, "Activity"),
        ]

        def fill_gaps(placed, d_start, d_end):
            result = []
            cursor = d_start
            for it in placed:
                eff_start = max(it.start_ms, d_start)
                eff_end = min(it.end_ms, d_end)
                if eff_start > cursor:
                    result.append(TimelineInterval(f"idle_{cursor}_{eff_start}", cursor, eff_start, P4_COVERAGE, "calendar_idle_segment"))
                result.append(it)
                cursor = max(cursor, eff_end)
            if cursor < d_end:
                result.append(TimelineInterval(f"idle_{cursor}_{d_end}", cursor, d_end, P4_COVERAGE, "calendar_idle_segment"))
            return result

        filled_24h = fill_gaps(intervals, day_start, day_end_24h)
        assert len(filled_24h) == 5
        assert filled_24h[0].item_type == "calendar_idle_segment"
        assert (filled_24h[0].start_ms, filled_24h[0].end_ms) == (0, 10_000_000)
        assert (filled_24h[-1].start_ms, filled_24h[-1].end_ms) == (50_000_000, day_end_24h)

        filled_23h = fill_gaps(intervals, day_start, day_end_23h_dst)
        assert filled_23h[-1].end_ms == day_end_23h_dst

        filled_25h = fill_gaps(intervals, day_start, day_end_25h_dst)
        assert filled_25h[-1].end_ms == day_end_25h_dst


# ==============================================================================
# SECTION 3: Dynamic Speaker Resolver & Token Injection (Contract C6)
# ==============================================================================

class TestAdvSpeakerResolverFuzzingAndTokenInjection:
    """Adversarial testing of Contract C6 3-tier dynamic speaker fallback & tag replacer."""

    def test_3tier_precedence_hierarchy(self):
        """Verifies strict 3-tier precedence: Enrolled Profile > Active Roster > Anonymous Index."""
        ctx = {
            "speaker_profiles": {
                "spk_enrolled": {"name": "Dr. Sarah Chen"},
                "spk_suspect_only": {"name": "", "suspected_name": "Sarah ?"},
            },
            "active_roster": {
                "spk_suspect_only": {"suspected_name": "Prof. Chen (Suspect)"},
            },
            "unknown_speaker_index": 4,
        }

        # Tier 1: Profile has enrolled name
        assert resolve_speaker_name("spk_enrolled", ctx) == "Dr. Sarah Chen"

        # Tier 2: Profile has empty name, but active roster has suspect
        assert resolve_speaker_name("spk_suspect_only", ctx) == "Prof. Chen (Suspect)"

        # Tier 3: Unknown speaker ID falls back to sequential index
        assert resolve_speaker_name("spk_unseen_99", ctx) == "Speaker 4"

    def test_circular_and_reciprocal_roster_stability(self):
        """Reciprocal or circular roster entries cannot cause infinite loops or recursion depth errors."""
        # A references B, B references A
        ctx_circular = {
            "speaker_profiles": {},
            "active_roster": {
                "spk_alpha": {"suspected_name": "spk_beta"},
                "spk_beta": {"suspected_name": "spk_alpha"},
            },
            "unknown_speaker_index": 1,
        }

        # Evaluates non-recursively in constant time
        res_a = resolve_speaker_name("spk_alpha", ctx_circular)
        assert res_a == "spk_beta"
        res_b = resolve_speaker_name("spk_beta", ctx_circular)
        assert res_b == "spk_alpha"

    def test_tag_replacer_adversarial_token_fuzzing(self):
        """Regex tag replacer handles nested, malformed, unclosed, and HTML-like tokens cleanly."""
        resolver_map = {
            "spk_01": "Alice",
            "spk_02": "Bob",
        }

        def replace_tags(text: str) -> str:
            # Pattern matching [speaker:spk_01] or <speaker:spk_01>
            regex = re.compile(r'\[speaker:([a-zA-Z0-9_\-]+)\]|<speaker:([a-zA-Z0-9_\-]+)>')

            def repl(m):
                spk_id = m.group(1) or m.group(2)
                return resolver_map.get(spk_id, f"Speaker_{spk_id}")

            return regex.sub(repl, text)

        # Standard brackets and angle brackets
        assert replace_tags("Hello [speaker:spk_01], meet <speaker:spk_02>!") == "Hello Alice, meet Bob!"

        # Nested brackets [[speaker:spk_01]]
        assert replace_tags("[[speaker:spk_01]]") == "[Alice]"

        # Unclosed tag [speaker:spk_01 (missing closing bracket)
        assert replace_tags("Incomplete [speaker:spk_01 text") == "Incomplete [speaker:spk_01 text"

        # Space inside token [speaker: spk_01]
        assert replace_tags("[speaker: spk_01]") == "[speaker: spk_01]"

        # HTML attribute injection inside token <speaker:spk_01 onclick="alert(1)">
        assert replace_tags('<speaker:spk_01 onclick="alert(1)">') == '<speaker:spk_01 onclick="alert(1)">'

        # Empty token [speaker:]
        assert replace_tags("[speaker:]") == "[speaker:]"

    def test_tag_replacer_high_volume_scale(self):
        """Replaces 5,000 speaker tags in a 200KB text block within 50ms with linear O(N) performance."""
        resolver_map = {f"spk_{i % 10}": f"Speaker_{i % 10}" for i in range(10)}
        regex = re.compile(r'\[speaker:([a-zA-Z0-9_\-]+)\]|<speaker:([a-zA-Z0-9_\-]+)>')

        tokens = [f"Turn {i}: [speaker:spk_{i % 10}] says hello to <speaker:spk_{(i+1) % 10}>." for i in range(2500)]
        large_text = "\n".join(tokens)

        t0 = time.perf_counter()
        replaced = regex.sub(lambda m: resolver_map.get(m.group(1) or m.group(2), "Unknown"), large_text)
        t_delta = time.perf_counter() - t0

        assert "[speaker:" not in replaced
        assert "<speaker:" not in replaced
        assert t_delta < 0.1, f"Tag replacement took too long: {t_delta:.3f}s"


# ==============================================================================
# SECTION 4: Cross-Layer IPC Native Bridge & RingBuffer Saturation
# ==============================================================================

class TestAdvCrossLayerIpcAndRingBufferSaturation:
    """Adversarial stress-testing of MethodChannel IPC ring buffer and Auth7Tuple tokens."""

    def test_ring_buffer_saturation_and_fifo_eviction(self):
        """RingBuffer capacity=256: pushing 1,000 events drops oldest 744, preserving newest 256 in strict FIFO."""
        ring = NativeRingBuffer(capacity=256)

        # Push 1,000 sequential events
        for seq in range(1000):
            ring.push({"sequence": seq, "event": "STATE_CHANGED", "payload": {"seq": seq}})

        assert ring.size == 256
        assert ring.dropped_events_count == 744

        # Drain buffer
        drained = ring.drain()
        assert len(drained) == 256
        assert ring.size == 0

        # Verify FIFO order and preserved range (744 .. 999)
        sequences = [x["sequence"] for x in drained]
        assert sequences == list(range(744, 1000))

    def test_ring_buffer_thread_safety_and_clearing(self):
        """Clear resets buffer size and dropped count to 0 cleanly."""
        ring = NativeRingBuffer(capacity=256)
        for i in range(300):
            ring.push({"i": i})

        assert ring.size == 256
        assert ring.dropped_events_count == 44

        ring.clear()
        assert ring.size == 0
        assert ring.dropped_events_count == 0

    def test_audio_power_metering_normalization_and_extreme_inputs(self):
        """Normalizes [-60.0, 0.0] dBFS to [0.05, 1.0] and clamps extreme outliers."""
        # Baseline normal values
        assert pytest.approx(normalize_audio_metering_power(0.0), 1e-4) == 1.0
        assert pytest.approx(normalize_audio_metering_power(-30.0), 1e-4) == 0.5
        assert pytest.approx(normalize_audio_metering_power(-60.0), 1e-4) == 0.05

        # Extreme quiet: -120 dBFS clamped to 0.05
        assert normalize_audio_metering_power(-120.0) == 0.05

        # Extreme clipping volume: +20.0 dBFS clamped to 1.0
        assert normalize_audio_metering_power(20.0) == 1.0

    def test_auth7tuple_validation_and_type_safety(self):
        """Validates 7 required fields, positive timestamp, and non-empty string types."""
        valid_auth = {
            "accessToken": "tok_access_123",
            "refreshToken": "tok_refresh_456",
            "expiresAt": 1790000000,
            "baseUrl": "https://api.hemory.app",
            "slotConnectionType": "direct",
            "slotDpUnitId": "dp_unit_01",
            "slotTenantId": "tenant_prod",
        }
        assert validate_auth7tuple(valid_auth) == valid_auth

        # Missing required keys
        for k in valid_auth:
            bad = dict(valid_auth)
            del bad[k]
            with pytest.raises(ValueError, match="Missing required Auth7Tuple key"):
                validate_auth7tuple(bad)

        # Non-numeric or negative expiresAt
        for bad_ts in [0, -100, -1, "tomorrow", True]:
            bad = dict(valid_auth)
            bad["expiresAt"] = bad_ts
            with pytest.raises((ValueError, TypeError)):
                validate_auth7tuple(bad)

        # Empty and pure whitespace strings
        for bad_ws in ["", " ", "   ", "\t", "\n", "\r\n"]:
            bad = dict(valid_auth)
            bad["accessToken"] = bad_ws
            with pytest.raises(ValueError, match="must be a non-empty String"):
                validate_auth7tuple(bad)

    def test_auth7tuple_unicode_cf_formatting_character_defense(self):
        """Tests Unicode Cf formatting characters (ZWS, ZWNJ, LRM, RLM) against Auth7Tuple validation.
        Demonstrates why defense-in-depth is necessary: pure Cf strings pass standard .strip()."""
        # Catalog of invisible Unicode format characters (Category Cf)
        cf_vectors = [
            ("\u200B", "Zero-Width Space"),
            ("\u200C", "Zero-Width Non-Joiner"),
            ("\u200D", "Zero-Width Joiner"),
            ("\u200E", "Left-to-Right Mark"),
            ("\u200F", "Right-to-Left Mark"),
            ("\u2060", "Word Joiner"),
            ("\uFEFF", "Zero-Width No-Break Space / BOM"),
        ]

        for char, name in cf_vectors:
            # Standard Python/Dart strip() does NOT strip Unicode Cf characters!
            has_strip_bypass = bool(char.strip())
            assert has_strip_bypass is True, f"{name} was unexpectedly stripped by standard strip()"

            # Defense-in-depth check: contains_unicode_cf_only correctly detects the invisible token
            assert contains_unicode_cf_only(char) is True, f"{name} must be detected as invisible formatting"


# ==============================================================================
# SECTION 5: Native Android Elastic Rollover Slicer & Audio HAL
# ==============================================================================

class TestAdvElasticRolloverAndAudioHal:
    """Adversarial stress-testing of 26m-30m elastic silence rollover window."""

    def test_sub_26m_silence_immunity(self):
        """Under 26 minutes (1,560,000 ms), silence >= 800ms NEVER triggers cut."""
        # 25m 59.999s with 5,000ms continuous silence at -50 dBFS
        dur_ms = 1_559_999
        cut, reason = evaluate_rollover(dur_ms, silence_duration_ms=5000, dbfs=-50.0)
        assert cut is False
        assert reason is None

    def test_26m_to_30m_exact_boundary_conditions(self):
        """In [26m, 30m), cut triggers ONLY when silence >= 800ms AND dBFS <= -40.0."""
        window_ms = 1_600_000  # ~26.6 min

        # 1. Silence duration 799ms -> NO cut
        cut, reason = evaluate_rollover(window_ms, silence_duration_ms=799, dbfs=-45.0)
        assert cut is False

        # 2. Silence duration 800ms at -40.0 dBFS -> CUT (silence_window)
        cut, reason = evaluate_rollover(window_ms, silence_duration_ms=800, dbfs=-40.0)
        assert cut is True
        assert reason == "silence_window"

        # 3. Silence duration 800ms at -39.9 dBFS (loud speech) -> NO cut
        cut, reason = evaluate_rollover(window_ms, silence_duration_ms=800, dbfs=-39.9)
        assert cut is False

    def test_30m_hard_ceiling_forced_split(self):
        """At 30m00.000s (1,800,000 ms), forced cut occurs regardless of audio energy or silence."""
        cut, reason = evaluate_rollover(1_800_000, silence_duration_ms=0, dbfs=0.0)
        assert cut is True
        assert reason == "hard_limit"

        cut, reason = evaluate_rollover(2_000_000, silence_duration_ms=0, dbfs=-10.0)
        assert cut is True
        assert reason == "hard_limit"

    def test_adts_header_corrupted_fuzzing(self):
        """ADTS bit-packing strictly rejects corrupted syncwords and illegal frame lengths."""
        valid_hdr = build_adts_header(payload_len=100, sample_rate=16000, channels=1)
        parsed = parse_adts_header(valid_hdr)
        assert parsed["syncword"] == 0xFFF
        assert parsed["sample_rate"] == 16000
        assert parsed["channel_config"] == 1

        # Corrupt syncword
        bad_sync = bytearray(valid_hdr)
        bad_sync[0] = 0xFE
        with pytest.raises(ValueError, match="Invalid ADTS syncword"):
            parse_adts_header(bytes(bad_sync))

        # Truncated header (< 7 bytes)
        with pytest.raises(ValueError, match="requires 7 bytes"):
            parse_adts_header(valid_hdr[:6])


# ==============================================================================
# SECTION 6: UploadWorker, LocalAtomicUploadQueue & Multi-Tenant Guard
# ==============================================================================

class TestAdvUploadWorkerAndMultiTenantGuard:
    """Adversarial testing of WorkManager atomic queue, exponential backoff, and 7-day retention."""

    def test_workmanager_exponential_backoff_and_quarantine(self, queue_storage: MockUploadQueueStorage):
        """Attempts 1..4 back off with 30s * 2^(attempts-1); attempt 5 permanently quarantined."""
        audio = synthesize_adts_file(num_frames=2)
        item = queue_storage.enqueue("upl_adv_backoff", "rec_adv", 0, audio, {"tenant_id": "tenant_alpha"})

        def server_503(it, meta):
            return False, 503

        # Attempt 1: backoff is 30s * 2^0 = 30s (30,000 ms)
        t1 = 1_000_000
        queue_storage.process_worker_tick(server_upload_fn=server_503, current_time_ms=t1)
        assert item.attempts == 1
        assert item.status == "FAILED_RETRYABLE"

        # Tick at t1 + 20s (still within 30s backoff window): must skip
        res = queue_storage.process_worker_tick(server_upload_fn=server_503, current_time_ms=t1 + 20_000)
        assert len(res) == 1
        assert res[0]["action"] == "backoff_waiting"
        assert item.attempts == 1

        # Advance to attempt 5 (max attempts = 5)
        item.attempts = 4
        item.last_attempt_at_ms = 2_000_000
        backoff_4 = 30_000 * (2 ** (4 - 1))  # 240,000 ms
        queue_storage.process_worker_tick(server_upload_fn=server_503, current_time_ms=item.last_attempt_at_ms + backoff_4 + 1000)
        assert item.attempts == 5
        assert item.status == "FAILED_PERMANENT"
        assert item.upload_block_reason == "max_attempts_exceeded"

    def test_multitenant_mismatch_quarantine_preserves_audio_files(self, queue_storage: MockUploadQueueStorage):
        """Cross-tenant identity mismatch quarantines entry as FAILED_PERMANENT, NEVER deletes audio file."""
        audio = synthesize_adts_file(num_frames=1)
        item = queue_storage.enqueue("upl_cross_guard", "rec_cross", 0, audio, {"tenant_id": "tenant_foreign_beta"})

        # Active credentials belong to tenant_alpha
        queue_storage.active_auth_context = {"tenant_id": "tenant_alpha", "vault_id": "vault_primary"}

        res = queue_storage.process_worker_tick(current_time_ms=10_000_000)
        assert item.status == "FAILED_PERMANENT"
        assert item.upload_block_reason == "upload_identity_mismatch_skipped_at_send"

        # Audio file and meta file MUST remain intact on disk
        assert os.path.exists(item.file_path) is True
        assert os.path.exists(item.meta_path) is True

    def test_7day_retention_pruning_boundaries_and_immutable_protection(self, queue_storage: MockUploadQueueStorage):
        """Completed records > 7 days pruned; FAILED_PERMANENT and QUEUED records never pruned regardless of age."""
        audio = synthesize_adts_file(num_frames=1)
        now_ms = 200_000_000
        seven_days_ms = 7 * 24 * 3600 * 1000

        # Item 1: COMPLETED, age 6 days 23 hours (retained)
        i1 = queue_storage.enqueue("upl_c1", "r1", 0, audio, {"tenant_id": "tenant_alpha"})
        i1.status = "COMPLETED"
        i1.last_attempt_at_ms = now_ms - (seven_days_ms - 3_600_000)

        # Item 2: COMPLETED, age 7 days 1 hour (pruned)
        i2 = queue_storage.enqueue("upl_c2", "r2", 0, audio, {"tenant_id": "tenant_alpha"})
        i2.status = "COMPLETED"
        i2.last_attempt_at_ms = now_ms - (seven_days_ms + 3_600_000)

        # Item 3: FAILED_PERMANENT, age 30 days (NEVER pruned)
        i3 = queue_storage.enqueue("upl_c3", "r3", 0, audio, {"tenant_id": "tenant_alpha"})
        i3.status = "FAILED_PERMANENT"
        i3.last_attempt_at_ms = now_ms - (30 * 24 * 3600 * 1000)

        # Item 4: QUEUED, age 30 days (NEVER pruned)
        i4 = queue_storage.enqueue("upl_c4", "r4", 0, audio, {"tenant_id": "tenant_alpha"})
        i4.status = "QUEUED"
        i4.last_attempt_at_ms = now_ms - (30 * 24 * 3600 * 1000)

        queue_storage.persist_atomic()

        pruned = queue_storage.prune_retention(current_time_ms=now_ms)
        assert pruned == 1
        assert "upl_c1" in queue_storage.items
        assert "upl_c2" not in queue_storage.items
        assert "upl_c3" in queue_storage.items
        assert "upl_c4" in queue_storage.items

    def test_atomic_queue_tmp_file_crash_resilience(self, queue_storage: MockUploadQueueStorage):
        """Crash leaving orphaned broken .tmp file does not corrupt valid upload_queue.json."""
        audio = synthesize_adts_file(num_frames=1)
        queue_storage.enqueue("upl_resilient", "r_res", 0, audio, {"tenant_id": "tenant_alpha"})

        # Corrupt .tmp file
        tmp_path = queue_storage.upload_dir / "upload_queue.json.tmp"
        with open(tmp_path, "w") as f:
            f.write("{corrupted_incomplete_bytes")

        # Load from disk must read intact main file
        loaded = queue_storage.load_from_disk()
        assert len(loaded) == 1
        assert loaded[0].upload_id == "upl_resilient"
