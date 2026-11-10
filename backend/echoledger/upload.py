"""
backend/echoledger/upload.py
6-Field Multipart Audio Ingestion Controller & 7-byte ADTS Bitstream Verification Engine.
Conforms to HEMORY_SYSTEM_SPECIFICATION.md §2.4.4, §3.4.4, §4.1.5, & Contract C2.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, UploadFile, status

from .db import Database
from .lease import LeaseErrorCode, LeaseRejectionException, UploadLeaseValidator
from .models import UploadRecordingResponse
from .storage import StorageService


@dataclass(frozen=True)
class AdtsMetadata:
    frame_count: int
    duration_ms: int
    sample_rate: int = 16000
    channels: int = 1
    bitrate_kbps: int = 48
    file_size_bytes: int = 0
    sha256: str = ""


class AdtsValidationError(ValueError):
    """Raised when an audio bitstream is not a valid 7-byte MPEG-4 ADTS AAC-LC stream."""
    pass


class AudioBitstreamException(HTTPException):
    """Exception carrying standardized error response for invalid audio streams."""

    def __init__(self, error_code: str, message: str, status_code: int = 400):
        detail = {
            "error": error_code,
            "error_code": error_code,
            "message": message,
            "detail": message,
        }
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code
        self.error_message = message


def validate_and_parse_adts(data: bytes) -> AdtsMetadata:
    """
    Parses and verifies 7-byte MPEG-4 ADTS header bitstream across all frames:
    - Syncword: 0xFFF (12 bits)
    - ID: 0 (MPEG-4 Audio)
    - Layer: 00
    - Protection absent: 1 (no CRC)
    - Profile: 1 (AAC-LC)
    - Sample rate index: 8 (16000 Hz)
    - Channel config: 1 (Mono)
    """
    n = len(data)
    if n < 7:
        raise AdtsValidationError(f"Audio stream too short ({n} bytes, minimum 7 required)")

    mv = memoryview(data)
    offset = 0
    frame_count = 0

    while offset < n:
        remaining = n - offset
        if remaining < 7:
            raise AdtsValidationError(f"Truncated ADTS header at offset {offset} ({remaining} bytes remaining)")

        b0 = mv[offset]
        b1 = mv[offset + 1]
        b2 = mv[offset + 2]
        b3 = mv[offset + 3]
        b4 = mv[offset + 4]
        b5 = mv[offset + 5]

        # 1. Syncword: 12 bits 0xFFF
        if b0 != 0xFF or (b1 & 0xF0) != 0xF0:
            raise AdtsValidationError(f"Invalid ADTS syncword (0x{b0:02X}{(b1 & 0xF0) >> 4:X}) at frame {frame_count}")

        # 2. MPEG ID: 0 (MPEG-4 Audio)
        if (b1 & 0x08) != 0:
            raise AdtsValidationError(f"Invalid MPEG ID (expected MPEG-4) at frame {frame_count}")

        # 3. Layer: 00
        if (b1 & 0x06) != 0:
            raise AdtsValidationError(f"Invalid layer bits at frame {frame_count}")

        # 4. Protection Absent: 1 (no CRC, 7-byte header)
        if (b1 & 0x01) != 1:
            raise AdtsValidationError(f"Protection absent must be 1 (no CRC) at frame {frame_count}")

        # 5. Profile: 1 (AAC-LC)
        profile = (b2 & 0xC0) >> 6
        if profile != 1:
            raise AdtsValidationError(f"Invalid profile {profile} (expected 1 [AAC-LC]) at frame {frame_count}")

        # 6. Sampling Frequency Index: 8 (16000 Hz)
        sample_rate_idx = (b2 & 0x3C) >> 2
        if sample_rate_idx != 8:
            raise AdtsValidationError(
                f"Invalid sample rate index {sample_rate_idx} (expected 8 [16kHz]) at frame {frame_count}"
            )

        # 7. Channel Configuration: 1 (Mono)
        channel_config = ((b2 & 0x01) << 2) | ((b3 & 0xC0) >> 6)
        if channel_config != 1:
            raise AdtsValidationError(
                f"Invalid channel configuration {channel_config} (expected 1 [Mono]) at frame {frame_count}"
            )

        # 8. Frame Length (13 bits)
        frame_length = ((b3 & 0x03) << 11) | (b4 << 3) | ((b5 & 0xE0) >> 5)
        if frame_length < 7:
            raise AdtsValidationError(f"Invalid frame length {frame_length} (< 7) at frame {frame_count}")
        if offset + frame_length > n:
            raise AdtsValidationError(
                f"Truncated frame at index {frame_count}: needs {frame_length} bytes, only {remaining} available"
            )

        offset += frame_length
        frame_count += 1

    duration_ms = frame_count * 64  # 1024 samples / 16000 Hz * 1000 = 64 ms
    sha256_hash = hashlib.sha256(data).hexdigest()
    bitrate_kbps = int(round((n * 8) / duration_ms)) if duration_ms > 0 else 48

    return AdtsMetadata(
        frame_count=frame_count,
        duration_ms=duration_ms,
        sample_rate=16000,
        channels=1,
        bitrate_kbps=bitrate_kbps,
        file_size_bytes=n,
        sha256=sha256_hash,
    )


class UploadService:
    """Ingestion service coordinating lease pre-flight validation, ADTS verification, and storage."""

    def __init__(self, db: Database, storage: StorageService, max_upload_bytes: int = 15_000_000):
        self.db = db
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes

    async def handle_upload(
        self,
        tenant_id: str,
        vault_id: str,
        started_at: str,
        format_str: str,
        source: str,
        capture_timezone: str | None,
        capture_metadata_str: str,
        file: UploadFile,
        auth_context: dict[str, Any] | None = None,
        uploading_dp_id_header: str | None = None,
    ) -> UploadRecordingResponse:
        """
        Executes the 6-field ingestion pipeline:
        1. Form constraints & format validation
        2. Clock drift & time validation
        3. Lease pre-flight check & listening schedule window check
        4. Binary ADTS verification
        5. Physical partitioned storage persistence
        6. Database session & segment persistence
        """
        # 1. Format validation
        if format_str != "aac":
            raise HTTPException(
                status_code=422,
                detail=f"Invalid format: expected 'aac', got '{format_str}'",
            )

        valid_sources = {"ambient", "phone", "watch", "app_default", "mic"}
        if source not in valid_sources:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid source: expected one of {sorted(valid_sources)}, got '{source}'",
            )

        try:
            metadata = json.loads(capture_metadata_str)
            if not isinstance(metadata, dict):
                raise ValueError("capture_metadata must be a JSON object")
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"capture_metadata must be valid JSON: {exc}",
            ) from exc

        raw_idx = metadata.get("segment_index", 0)
        if isinstance(raw_idx, (bool, float)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="segment_index in capture_metadata must be an integer, not float or boolean",
            )
        try:
            if isinstance(raw_idx, str):
                clean_str = raw_idx.strip()
                if not (
                    clean_str.isdigit()
                    or (clean_str.startswith(("-", "+")) and clean_str[1:].isdigit())
                ):
                    raise ValueError(f"invalid literal for integer: '{raw_idx}'")
            segment_index = int(raw_idx)
        except (ValueError, TypeError, OverflowError) as ex:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid segment_index in capture_metadata: must be integer ({ex})",
            ) from ex

        if segment_index < 0 or segment_index > 9223372036854775807:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="segment_index in capture_metadata must be between 0 and 9223372036854775807",
            )

        # 2. Timing & Clock drift verification
        seg_dt = UploadLeaseValidator.validate_segment_time(started_at)

        # 3. Resolve uploading device profile ID
        user_id = auth_context.get("user_id") if auth_context else None
        effective_user_id = user_id or "usr_default"

        if auth_context and not auth_context.get("is_admin") and auth_context.get("dp_instance_id"):
            bound_dp = auth_context["dp_instance_id"]
            if uploading_dp_id_header and uploading_dp_id_header != bound_dp:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Header x-dp-instance-id does not match authenticated binding",
                )
            meta_dp = metadata.get("dp_instance_id") or metadata.get("device_id")
            if meta_dp and meta_dp != bound_dp:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="capture_metadata device ID does not match authenticated binding",
                )
            uploading_dp_id = bound_dp
        else:
            uploading_dp_id = (
                uploading_dp_id_header
                or metadata.get("dp_instance_id")
                or metadata.get("device_id")
                or (auth_context.get("dp_instance_id") if auth_context else None)
            )
        effective_dp_id = uploading_dp_id or "dp_default"

        # 4. Enforce Primary Device Lease & Listening Schedule Window
        with self.db.connect() as conn:
            UploadLeaseValidator.validate_primary_device_lease(
                conn=conn,
                tenant_id=tenant_id,
                vault_id=vault_id,
                uploading_dp_id=uploading_dp_id,
                user_id=user_id,
            )
            UploadLeaseValidator.validate_listening_schedule_window(
                conn=conn,
                tenant_id=tenant_id,
                vault_id=vault_id,
                dp_instance_id=uploading_dp_id,
                seg_dt=seg_dt,
                capture_timezone_str=capture_timezone,
                user_id=user_id,
            )

        # 5. Read audio stream with bounded limit to prevent memory exhaustion
        audio_bytes = await file.read(self.max_upload_bytes + 1)
        if not audio_bytes:
            raise AudioBitstreamException(
                error_code="empty_audio_file",
                message="Uploaded audio file is empty.",
            )
        if len(audio_bytes) > self.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Audio file exceeds maximum size limit of {self.max_upload_bytes} bytes.",
            )

        # 6. Verify 7-byte MPEG-4 ADTS bitstream
        try:
            adts_meta = validate_and_parse_adts(audio_bytes)
        except AdtsValidationError as err:
            raise AudioBitstreamException(
                error_code="INVALID_AUDIO_STREAM",
                message=f"Audio file is not a valid ADTS AAC stream: {err}",
            ) from err

        # 7. Check for idempotent retry before storage/db write
        session_id = (
            metadata.get("live_session_id")
            or metadata.get("session_id")
            or f"sess_{seg_dt.strftime('%Y%m%d_%H%M%S')}"
        )

        existing_sess = self.db.one(
            "SELECT * FROM recording_sessions WHERE live_session_id=?",
            (session_id,),
        )
        if existing_sess:
            sess_dict = dict(existing_sess)
            if (
                sess_dict["tenant_id"] != tenant_id
                or sess_dict["vault_id"] != vault_id
                or (user_id is not None and sess_dict["user_id"] != user_id)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Recording session belongs to a different tenant/vault/user",
                )
            if user_id is None:
                effective_user_id = sess_dict["user_id"]

        existing_seg = self.db.one(
            "SELECT * FROM recording_segments WHERE live_session_id=? AND segment_index=? AND tenant_id=? AND vault_id=?",
            (session_id, segment_index, tenant_id, vault_id),
        )
        if existing_seg:
            existing_seg_dict = dict(existing_seg)
            rec_id = metadata.get("recording_id") or f"rec_{session_id}_{segment_index}"
            token_part = existing_seg_dict["segment_id"].removeprefix("seg_")
            return UploadRecordingResponse(
                recording_id=rec_id,
                asset_id=f"ast_{token_part}",
                artifact_id=f"art_{token_part}",
                storage_key=existing_seg_dict.get("storage_key") or "",
                status="queued_for_processing",
            )

        # 8. Physical Partitioned Storage Persistence
        enriched_meta = {
            **metadata,
            "sample_rate": adts_meta.sample_rate,
            "channels": adts_meta.channels,
            "bitrate_kbps": adts_meta.bitrate_kbps,
            "duration_ms": adts_meta.duration_ms,
            "frame_count": adts_meta.frame_count,
            "sha256": adts_meta.sha256,
            "file_size_bytes": adts_meta.file_size_bytes,
            "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        storage_key, meta_key = self.storage.save_recording(
            tenant_id=tenant_id,
            vault_id=vault_id,
            session_id=session_id,
            segment_index=segment_index,
            started_at=started_at,
            audio_bytes=audio_bytes,
            meta_dict=enriched_meta,
        )

        # 9. Database Session & Segment Persistence
        rec_id = metadata.get("recording_id") or f"rec_{secrets.token_hex(8)}"
        seg_token = secrets.token_hex(8)
        seg_id = f"seg_{seg_token}"
        asset_id = f"ast_{seg_token}"
        artifact_id = f"art_{seg_token}"

        resolved_tz = datetime.timezone.utc
        if capture_timezone:
            try:
                resolved_tz = ZoneInfo(capture_timezone)
            except Exception:
                resolved_tz = datetime.timezone.utc
        local_day_key = seg_dt.astimezone(resolved_tz).strftime("%Y-%m-%d")

        effective_dp_id = uploading_dp_id or "dp_default"

        try:
            self.db.upsert_recording_session(
                live_session_id=session_id,
                tenant_id=tenant_id,
                vault_id=vault_id,
                user_id=effective_user_id,
                dp_instance_id=effective_dp_id,
                local_day_key=local_day_key,
                started_at=started_at,
            )
        except PermissionError as err:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(err),
            ) from err

        try:
            self.db.insert_recording_segment(
                segment_id=seg_id,
                live_session_id=session_id,
                tenant_id=tenant_id,
                vault_id=vault_id,
                user_id=effective_user_id,
                dp_instance_id=effective_dp_id,
                segment_index=segment_index,
                storage_key=storage_key,
                started_at=started_at,
                audio_format="aac",
                sample_rate=adts_meta.sample_rate,
                bitrate_kbps=adts_meta.bitrate_kbps,
                duration_ms=adts_meta.duration_ms,
                file_size_bytes=adts_meta.file_size_bytes,
                split_reason=metadata.get("split_reason", "window"),
                upload_status="uploaded",
            )
        except sqlite3.IntegrityError as err:
            # Race condition on concurrent identical upload
            existing_seg = self.db.one(
                "SELECT * FROM recording_segments WHERE live_session_id=? AND segment_index=? AND tenant_id=? AND vault_id=?",
                (session_id, segment_index, tenant_id, vault_id),
            )
            if existing_seg:
                existing_seg_dict = dict(existing_seg)
                rec_id = metadata.get("recording_id") or f"rec_{session_id}_{segment_index}"
                token_part = existing_seg_dict["segment_id"].removeprefix("seg_")
                return UploadRecordingResponse(
                    recording_id=rec_id,
                    asset_id=f"ast_{token_part}",
                    artifact_id=f"art_{token_part}",
                    storage_key=existing_seg_dict.get("storage_key") or "",
                    status="queued_for_processing",
                )
            other_seg = self.db.one(
                "SELECT * FROM recording_segments WHERE live_session_id=? AND segment_index=?",
                (session_id, segment_index),
            )
            if other_seg:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Recording session belongs to a different tenant/vault/user",
                ) from err
            raise

        return UploadRecordingResponse(
            recording_id=rec_id,
            asset_id=asset_id,
            artifact_id=artifact_id,
            storage_key=storage_key,
            status="queued_for_processing",
        )
