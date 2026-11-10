"""
backend/echoledger/storage.py
Physical Storage Partitioning Service for Multi-Tenant Audio Ingestion.
Partition layout: data/recordings/{tenant_id}/{vault_id}/{year}/{month}/{session_id}/{segment_index}_{started_at}.aac
Conforms to HEMORY_SYSTEM_SPECIFICATION.md §6.4.3 & Contract C2.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any


def atomic_write(path: Path, content: bytes) -> None:
    """Crash-resilient atomic file write using tempfile, fsync, and dir fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "xb") as f:
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        # Flush directory entry to disk on POSIX systems
        try:
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
    finally:
        temp.unlink(missing_ok=True)


class StorageService:
    """Manages physical storage partitioning and persistence of uploaded audio."""

    def __init__(self, data_dir: Path | str = "./data"):
        self.data_dir = Path(data_dir).resolve()
        self.recordings_root = self.data_dir / "recordings"
        self.recordings_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize_path_segment(segment: str) -> str:
        """Sanitizes path segment to prevent directory traversal attacks."""
        clean = segment.replace("..", "").replace("/", "_").replace("\\", "_")
        clean = re.sub(r"[^\w\-\.:+]", "_", clean)
        return clean.strip("._") or "default"

    def build_storage_key(
        self,
        tenant_id: str,
        vault_id: str,
        session_id: str,
        segment_index: int,
        started_at: str,
    ) -> str:
        """Builds virtual object storage key."""
        try:
            clean_ts = started_at.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(clean_ts)
            year = dt.strftime("%Y")
            month = dt.strftime("%m")
        except Exception:
            now = datetime.datetime.now(datetime.timezone.utc)
            year = now.strftime("%Y")
            month = now.strftime("%m")

        clean_tenant = self._sanitize_path_segment(tenant_id)
        clean_vault = self._sanitize_path_segment(vault_id)
        clean_session = self._sanitize_path_segment(session_id)
        clean_started = self._sanitize_path_segment(started_at)

        return (
            f"data/recordings/{clean_tenant}/{clean_vault}/{year}/{month}/"
            f"{clean_session}/{segment_index}_{clean_started}.aac"
        )

    @staticmethod
    def build_meta_key(storage_key: str) -> str:
        """Generates companion metadata key for the given audio storage key."""
        if storage_key.endswith(".aac"):
            return storage_key[:-4] + ".meta.json"
        return storage_key + ".meta.json"

    def resolve_path(self, storage_key: str) -> Path:
        """Resolves storage_key to an absolute local filesystem path under data_dir."""
        key = storage_key
        if key.startswith("data/"):
            key = key[5:]
        elif key.startswith("/data/"):
            key = key[6:]
        return self.data_dir / key

    def save_recording(
        self,
        tenant_id: str,
        vault_id: str,
        session_id: str,
        segment_index: int,
        started_at: str,
        audio_bytes: bytes,
        meta_dict: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """
        Atomically saves AAC audio stream and companion metadata JSON.
        Returns tuple of (storage_key, meta_key).
        """
        storage_key = self.build_storage_key(tenant_id, vault_id, session_id, segment_index, started_at)
        meta_key = self.build_meta_key(storage_key)

        audio_path = self.resolve_path(storage_key)
        meta_path = self.resolve_path(meta_key)

        atomic_write(audio_path, audio_bytes)
        if meta_dict is not None:
            meta_json = json.dumps(meta_dict, indent=2, ensure_ascii=False).encode("utf-8")
            atomic_write(meta_path, meta_json)

        return storage_key, meta_key

    def read_recording(self, storage_key: str) -> bytes:
        """Reads audio bytes from local filesystem."""
        path = self.resolve_path(storage_key)
        return path.read_bytes()

    def read_meta(self, meta_key: str) -> dict[str, Any]:
        """Reads JSON metadata from local filesystem."""
        path = self.resolve_path(meta_key)
        return json.loads(path.read_text(encoding="utf-8"))
