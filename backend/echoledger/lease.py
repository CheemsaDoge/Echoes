"""
backend/echoledger/lease.py
Primary Device Lease Arbitration & Ingestion Pre-flight Check Engine.
Conforms to HEMORY_SYSTEM_SPECIFICATION.md §3.1.3, §3.4.4, §4.1.5, & Contract C3.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .models import PrimaryDeviceRequest, PrimaryDeviceResponse


class LeaseErrorCode:
    NO_PRIMARY = "recording.upload_rejected_no_primary"
    NOT_PRIMARY = "recording.upload_rejected_not_primary"
    OUT_OF_PRIMARY_WINDOW = "recording.upload_rejected_out_of_primary_window"
    INVALID_TIME = "recording.upload_rejected_invalid_time"
    INVALID_DP = "invalid_dp_instance"
    NOT_AUTHORIZED = "not_authorized"


class LeaseRejectionException(HTTPException):
    """Exception carrying canonical lease rejection error code and metadata."""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ):
        detail: dict[str, Any] = {
            "error": error_code,
            "error_code": error_code,
            "message": message,
            "detail": message,
        }
        if extra:
            detail.update(extra)
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code
        self.error_message = message
        self.extra = extra or {}


class PrimaryLeaseArbiter:
    """Manages atomic election and assignment of the single primary recording device."""

    @staticmethod
    def acquire_primary_device(
        conn: sqlite3.Connection,
        dp_instance_id: str,
        vault_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> PrimaryDeviceResponse:
        """
        Atomically switches primary lease to target dp_instance_id within the specified vault.
        Must execute inside a transaction.
        """
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        row = conn.execute(
            """SELECT dp_instance_id, tenant_id, vault_id, user_id, is_primary
               FROM dp_instances
               WHERE dp_instance_id = ?""",
            (dp_instance_id,),
        ).fetchone()

        if not row:
            raise LeaseRejectionException(
                status_code=400,
                error_code=LeaseErrorCode.INVALID_DP,
                message=f"DP instance '{dp_instance_id}' not found in vault.",
            )

        row_dict = dict(row)
        if row_dict["vault_id"] != vault_id:
            raise LeaseRejectionException(
                status_code=400,
                error_code=LeaseErrorCode.INVALID_DP,
                message=f"DP instance '{dp_instance_id}' does not belong to vault '{vault_id}'.",
            )

        eff_tenant = row_dict["tenant_id"]
        eff_user = row_dict["user_id"]

        if tenant_id and eff_tenant != tenant_id:
            raise LeaseRejectionException(
                status_code=403,
                error_code=LeaseErrorCode.NOT_AUTHORIZED,
                message="Cannot alter lease of foreign tenant.",
            )
        if user_id and eff_user != user_id:
            raise LeaseRejectionException(
                status_code=403,
                error_code=LeaseErrorCode.NOT_AUTHORIZED,
                message="Cannot alter lease of foreign user.",
            )

        # Atomic reset of all instances in this vault for this tenant & user
        conn.execute(
            """UPDATE dp_instances
               SET is_primary = 0, updated_at = ?
               WHERE tenant_id = ? AND vault_id = ? AND user_id = ? AND is_primary != 0""",
            (now_iso, eff_tenant, vault_id, eff_user),
        )

        # Set target instance as primary
        conn.execute(
            """UPDATE dp_instances
               SET is_primary = 1, updated_at = ?, last_active_at = ?
               WHERE dp_instance_id = ?""",
            (now_iso, now_iso, dp_instance_id),
        )

        return PrimaryDeviceResponse(
            dp_instance_id=dp_instance_id,
            is_primary=True,
            effective_from=now_iso,
        )


class UploadLeaseValidator:
    """Pre-flight validation engine verifying timing, lease ownership, and listening schedule."""

    @staticmethod
    def validate_segment_time(
        started_at_str: str,
        server_now: datetime.datetime | None = None,
    ) -> datetime.datetime:
        """
        Validates ISO 8601 parsing and clock drift thresholds:
        - Future drift > 30s -> HTTP 409 recording.upload_rejected_invalid_time
        - Stale drift > 7 days -> HTTP 409 recording.upload_rejected_invalid_time
        """
        if server_now is None:
            server_now = datetime.datetime.now(datetime.timezone.utc)

        try:
            cleaned = started_at_str.replace("Z", "+00:00")
            seg_dt = datetime.datetime.fromisoformat(cleaned)
            if seg_dt.tzinfo is None:
                seg_dt = seg_dt.replace(tzinfo=datetime.timezone.utc)
            else:
                seg_dt = seg_dt.astimezone(datetime.timezone.utc)
        except Exception as exc:
            raise LeaseRejectionException(
                status_code=409,
                error_code=LeaseErrorCode.INVALID_TIME,
                message=f"Malformed started_at timestamp '{started_at_str}': {exc}",
            ) from exc

        drift_future = (seg_dt - server_now).total_seconds()
        if drift_future > 30.0:
            raise LeaseRejectionException(
                status_code=409,
                error_code=LeaseErrorCode.INVALID_TIME,
                message=f"Clock drift: timestamp is {drift_future:.1f}s in the future (>30s)",
            )

        stale_past = (server_now - seg_dt).total_seconds()
        if stale_past > 7 * 86400.0:
            raise LeaseRejectionException(
                status_code=409,
                error_code=LeaseErrorCode.INVALID_TIME,
                message=f"Timestamp is stale by {stale_past / 86400.0:.1f} days (> 7 days)",
            )

        return seg_dt

    @staticmethod
    def validate_primary_device_lease(
        conn: sqlite3.Connection,
        tenant_id: str,
        vault_id: str,
        uploading_dp_id: str | None,
        user_id: str | None = None,
    ) -> str:
        """
        Enforces single primary recording device requirement:
        - No primary device in vault -> HTTP 403 recording.upload_rejected_no_primary
        - Uploading device != active primary -> HTTP 409 recording.upload_rejected_not_primary
        """
        if user_id:
            query = """SELECT dp_instance_id FROM dp_instances
                       WHERE tenant_id = ? AND vault_id = ? AND user_id = ? AND is_primary = 1"""
            params = (tenant_id, vault_id, user_id)
        else:
            query = """SELECT dp_instance_id FROM dp_instances
                       WHERE tenant_id = ? AND vault_id = ? AND is_primary = 1"""
            params = (tenant_id, vault_id)

        rows = conn.execute(query, params).fetchall()
        if not rows:
            raise LeaseRejectionException(
                status_code=403,
                error_code=LeaseErrorCode.NO_PRIMARY,
                message="No active primary device configured for this vault.",
            )

        active_primary_id = rows[0]["dp_instance_id"]
        if not uploading_dp_id or uploading_dp_id != active_primary_id:
            raise LeaseRejectionException(
                status_code=409,
                error_code=LeaseErrorCode.NOT_PRIMARY,
                message=f"Device {uploading_dp_id} is not the primary recording device",
                extra={"primary_dp_instance_id": active_primary_id},
            )

        return active_primary_id

    @staticmethod
    def validate_listening_schedule_window(
        conn: sqlite3.Connection,
        tenant_id: str,
        vault_id: str,
        dp_instance_id: str | None,
        seg_dt: datetime.datetime,
        capture_timezone_str: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """
        Verifies segment timestamp falls inside the active listening schedule weekly window:
        - If outside window -> HTTP 403 recording.upload_rejected_out_of_primary_window
        - If disabled or no windows -> open 24/7 recording permitted.
        """
        row = None
        if dp_instance_id:
            row = conn.execute(
                """SELECT schedule_id, weekly_windows, is_enabled, revision
                   FROM listening_schedules
                   WHERE dp_instance_id = ? AND tenant_id = ? AND vault_id = ?""",
                (dp_instance_id, tenant_id, vault_id),
            ).fetchone()

        if not row and user_id:
            row = conn.execute(
                """SELECT schedule_id, weekly_windows, is_enabled, revision
                   FROM listening_schedules
                   WHERE user_id = ? AND tenant_id = ? AND vault_id = ?
                   LIMIT 1""",
                (user_id, tenant_id, vault_id),
            ).fetchone()

        if not row:
            return  # No schedule configured -> 24/7 permitted

        row_dict = dict(row)
        if not row_dict.get("is_enabled", True):
            return  # Schedule disabled -> 24/7 permitted

        raw_windows = row_dict.get("weekly_windows", "[]")
        if isinstance(raw_windows, str):
            try:
                windows = json.loads(raw_windows)
            except Exception:
                windows = []
        elif isinstance(raw_windows, list):
            windows = raw_windows
        else:
            windows = []

        if not windows:
            return  # Empty windows -> 24/7 permitted

        # Resolve local timezone
        tz = datetime.timezone.utc
        if capture_timezone_str:
            try:
                tz = ZoneInfo(capture_timezone_str)
            except Exception:
                tz = datetime.timezone.utc

        local_dt = seg_dt.astimezone(tz)
        iso_weekday = local_dt.isoweekday()  # 1 = Monday ... 7 = Sunday
        local_minutes = local_dt.hour * 60 + local_dt.minute

        matched = False
        for win in windows:
            if win.get("weekday") != iso_weekday:
                continue
            start_m = win.get("start_minutes")
            end_m = win.get("end_minutes")
            if start_m is None and "window_start" in win:
                h, m = win["window_start"].split(":")
                start_m = int(h) * 60 + int(m)
            if end_m is None and "window_end" in win:
                h, m = win["window_end"].split(":")
                end_m = int(h) * 60 + int(m)

            if start_m is not None and end_m is not None:
                if start_m <= local_minutes <= end_m:
                    matched = True
                    break

        if not matched:
            raise LeaseRejectionException(
                status_code=403,
                error_code=LeaseErrorCode.OUT_OF_PRIMARY_WINDOW,
                message=f"Upload rejected: timestamp '{seg_dt.isoformat()}' is outside the active listening schedule.",
            )
