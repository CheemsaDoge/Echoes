"""Upload Queue & WorkManager harness conforming to HEMORY_SYSTEM_SPECIFICATION.md Section 2.3.2."""

from __future__ import annotations
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class UploadQueueItem:
    upload_id: str
    recording_id: str
    segment_index: int
    file_path: str
    meta_path: str
    byte_size: int
    sha256: str
    status: str  # QUEUED, IN_FLIGHT, COMPLETED, FAILED_RETRYABLE, FAILED_PERMANENT
    attempts: int = 0
    max_attempts: int = 5
    created_at_ms: int = 0
    last_attempt_at_ms: int = 0
    upload_block_reason: str | None = None


class MockUploadQueueStorage:
    """Simulates Android atomic files/upload/upload_queue.json persistence and WorkManager worker."""

    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            self._temp_dir = tempfile.TemporaryDirectory()
            self.base_dir = Path(self._temp_dir.name)
        else:
            self._temp_dir = None
            self.base_dir = Path(base_dir)

        self.upload_dir = self.base_dir / "files" / "upload"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.queue_file = self.upload_dir / "upload_queue.json"
        self.items: dict[str, UploadQueueItem] = {}
        self.is_network_connected: bool = True
        self.active_auth_context: dict[str, Any] = {
            "tenant_id": "tenant_alpha",
            "vault_id": "vault_primary",
            "user_id": "user_001",
        }

    def cleanup(self):
        if self._temp_dir:
            self._temp_dir.cleanup()

    def persist_atomic(self):
        """Atomically persist self.items to upload_queue.json via .tmp file rename."""
        tmp_file = self.upload_dir / "upload_queue.json.tmp"
        data = [asdict(item) for item in self.items.values()]
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # Atomic rename
        tmp_file.replace(self.queue_file)

    def load_from_disk(self) -> list[UploadQueueItem]:
        """Read queue from upload_queue.json."""
        if not self.queue_file.exists():
            return []
        with open(self.queue_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return [UploadQueueItem(**item) for item in raw]

    def enqueue(
        self,
        upload_id: str,
        recording_id: str,
        segment_index: int,
        audio_bytes: bytes,
        meta_dict: dict[str, Any],
    ) -> UploadQueueItem:
        seg_dir = self.base_dir / "files" / recording_id / "segments"
        seg_dir.mkdir(parents=True, exist_ok=True)
        now_ms = int(time.time() * 1000)

        aac_path = seg_dir / f"{now_ms}.aac"
        meta_path = seg_dir / f"{now_ms}.meta"

        with open(aac_path, "wb") as f:
            f.write(audio_bytes)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_dict, f)

        import hashlib
        sha = hashlib.sha256(audio_bytes).hexdigest()

        item = UploadQueueItem(
            upload_id=upload_id,
            recording_id=recording_id,
            segment_index=segment_index,
            file_path=str(aac_path),
            meta_path=str(meta_path),
            byte_size=len(audio_bytes),
            sha256=sha,
            status="QUEUED",
            attempts=0,
            max_attempts=5,
            created_at_ms=now_ms,
            last_attempt_at_ms=0,
            upload_block_reason=None,
        )
        self.items[upload_id] = item
        self.persist_atomic()
        return item

    def process_worker_tick(
        self,
        server_upload_fn: Any = None,
        current_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Simulate one Android WorkManager execution cycle.
        Respects:
        - Network connectivity constraint
        - Identity mismatch guard (tenant_id mismatch)
        - Exponential backoff (attempts * 30s)
        - Max retry attempts (5)
        """
        if current_time_ms is None:
            current_time_ms = int(time.time() * 1000)

        results = []

        if not self.is_network_connected:
            return [{"action": "skip", "reason": "no_network"}]

        for upload_id, item in list(self.items.items()):
            if item.status in ("COMPLETED", "FAILED_PERMANENT"):
                continue

            # Check backoff window
            if item.attempts > 0:
                backoff_ms = (30 * 1000) * (2 ** (item.attempts - 1))
                if current_time_ms - item.last_attempt_at_ms < backoff_ms:
                    results.append({"upload_id": upload_id, "action": "backoff_waiting"})
                    continue

            # Read segment meta for tenant validation
            try:
                with open(item.meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
            except Exception:
                meta = {}

            item_tenant = meta.get("tenant_id")
            active_tenant = self.active_auth_context.get("tenant_id")

            # Multi-tenant safety guard: identity mismatch check
            if item_tenant and active_tenant and item_tenant != active_tenant:
                item.status = "FAILED_PERMANENT"
                item.upload_block_reason = "upload_identity_mismatch_skipped_at_send"
                item.last_attempt_at_ms = current_time_ms
                results.append({
                    "upload_id": upload_id,
                    "action": "blocked_identity_mismatch",
                    "reason": "upload_identity_mismatch_skipped_at_send",
                })
                self.persist_atomic()
                continue

            # Execute upload
            item.status = "IN_FLIGHT"
            item.attempts += 1
            item.last_attempt_at_ms = current_time_ms

            success = False
            response_code = 201
            if server_upload_fn:
                try:
                    success, response_code = server_upload_fn(item, meta)
                except Exception:
                    success = False
                    response_code = 500
            else:
                success = True

            if success:
                item.status = "COMPLETED"
                results.append({"upload_id": upload_id, "action": "success", "code": response_code})
            else:
                if response_code in (400, 403, 404, 409, 422):
                    # Permanent rejection
                    item.status = "FAILED_PERMANENT"
                    item.upload_block_reason = f"http_{response_code}"
                    results.append({"upload_id": upload_id, "action": "permanent_failure", "code": response_code})
                elif item.attempts >= item.max_attempts:
                    item.status = "FAILED_PERMANENT"
                    item.upload_block_reason = "max_attempts_exceeded"
                    results.append({"upload_id": upload_id, "action": "max_attempts_exhausted"})
                else:
                    item.status = "FAILED_RETRYABLE"
                    results.append({"upload_id": upload_id, "action": "retryable_failure", "code": response_code})

            self.persist_atomic()

        return results

    def prune_retention(self, current_time_ms: int | None = None) -> int:
        """
        7-Day Retention Pruning:
        Removes COMPLETED records older than 7 days (7 * 24 * 3600 * 1000 ms).
        Physically deletes corresponding .aac and .meta files.
        """
        if current_time_ms is None:
            current_time_ms = int(time.time() * 1000)

        retention_ms = 7 * 24 * 3600 * 1000
        cutoff_ms = current_time_ms - retention_ms
        pruned_count = 0

        for upload_id, item in list(self.items.items()):
            if item.status == "COMPLETED" and item.last_attempt_at_ms < cutoff_ms:
                # Remove files
                if os.path.exists(item.file_path):
                    try:
                        os.remove(item.file_path)
                    except OSError:
                        pass
                if os.path.exists(item.meta_path):
                    try:
                        os.remove(item.meta_path)
                    except OSError:
                        pass
                del self.items[upload_id]
                pruned_count += 1

        if pruned_count > 0:
            self.persist_atomic()

        return pruned_count
