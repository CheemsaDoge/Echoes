"""Background Processing Chains & Recording Mutations Synchronization Services.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 5.4.3 & Feature F4.4.
"""

from __future__ import annotations
import datetime
import secrets
from typing import Any

from .db import Database
from .models import (
    ProcessingChainDto,
    ProcessingChainsResponse,
    RecordingMutationDto,
    RecordingMutationsResponse,
    ClientMutationAction,
    ClientRecordingMutationsResponse,
)


def get_processing_chains_response(
    db: Database,
    vault_id: str,
    status: str | None = None,
    since: str | None = None,
    session_id: str | None = None,
    recording_id: str | None = None,
    limit: int = 50,
) -> ProcessingChainsResponse:
    """Fetch processing chains for vault, formatting into timeline card DTOs."""
    rows = db.list_processing_chains(
        vault_id=vault_id,
        status=status,
        since=since,
        session_id=session_id,
        recording_id=recording_id,
        limit=limit,
    )
    chains = []
    for r in rows:
        chains.append(
            ProcessingChainDto(
                chain_id=r["chain_id"],
                recording_id=r["recording_id"],
                live_session_id=r.get("live_session_id"),
                status=r["status"],
                current_step=r["current_step"],
                progress_pct=float(r.get("progress_pct") or 0.0),
                error_code=r.get("error_code"),
                error_message=r.get("error_message"),
                analyzing_start_at=r["analyzing_start_at"],
                analyzing_end_at=r.get("analyzing_end_at"),
                started_at=r.get("analyzing_start_at"),
                updated_at=r["updated_at"],
            )
        )
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return ProcessingChainsResponse(
        chains=chains,
        total=len(chains),
        server_time=now_iso,
    )


def get_recording_mutations_response(
    db: Database,
    vault_id: str,
    cursor: int = 0,
    limit: int = 50,
    mutation_type: str | None = None,
) -> RecordingMutationsResponse:
    """Fetch incremental mutations using causal integer cursor ordering."""
    mutations_raw, next_cursor, has_more = db.list_recording_mutations(
        vault_id=vault_id,
        cursor=cursor,
        limit=limit,
        mutation_type=mutation_type,
    )
    mutations = []
    for m in mutations_raw:
        mutations.append(
            RecordingMutationDto(
                cursor=m["cursor"],
                id=m["cursor"],
                tenant_id=m["tenant_id"],
                vault_id=m["vault_id"],
                entity_type=m["entity_type"],
                entity_id=m["entity_id"],
                mutation_type=m["mutation_type"],
                payload=m.get("payload") or {},
                payload_json=m.get("payload_json"),
                created_at=m["created_at"],
            )
        )
    return RecordingMutationsResponse(
        mutations=mutations,
        next_cursor=next_cursor,
        has_more=has_more,
    )


def record_client_mutations(
    db: Database,
    tenant_id: str,
    vault_id: str,
    mutations: list[dict[str, Any]] | list[ClientMutationAction],
) -> ClientRecordingMutationsResponse:
    """Append client-submitted recording mutations (e.g. redact_audio) to mutation journal."""
    count = 0
    for item in mutations:
        m_dict = item.model_dump() if isinstance(item, ClientMutationAction) else dict(item)
        action = m_dict.get("action") or "redact_audio"
        entity_id = m_dict.get("live_session_id") or m_dict.get("mutation_id") or f"mut_{secrets.token_hex(4)}"
        db.append_recording_mutation(
            tenant_id=tenant_id,
            vault_id=vault_id,
            entity_type="session" if "live_session_id" in m_dict else "recording",
            entity_id=entity_id,
            mutation_type=action,
            payload_json=m_dict,
        )
        count += 1
    return ClientRecordingMutationsResponse(status="synced", processed_count=count)
