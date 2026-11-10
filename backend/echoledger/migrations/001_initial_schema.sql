-- ============================================================================
-- HEMORY SERVER RELATIONAL SCHEMA
-- Version: 2.4.0-production
-- Tables: 14 Relational Entities with Indexes and Cascading Constraints
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. TENANTS: Top-level organizational and billing domain
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(128) NOT NULL,
    plan_tier VARCHAR(32) NOT NULL DEFAULT 'free',       -- 'free', 'pro', 'enterprise'
    status VARCHAR(32) NOT NULL DEFAULT 'active',         -- 'active', 'suspended', 'disconnected'
    settings JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_tenants_slug UNIQUE (slug),
    CONSTRAINT chk_tenants_plan CHECK (plan_tier IN ('free', 'pro', 'enterprise')),
    CONSTRAINT chk_tenants_status CHECK (status IN ('active', 'suspended', 'disconnected'))
);

CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
CREATE INDEX IF NOT EXISTS idx_tenants_plan ON tenants(plan_tier);

-- ----------------------------------------------------------------------------
-- 2. VAULTS: Encrypted memory container & cryptographic partition
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vaults (
    vault_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    encryption_key_id VARCHAR(128) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ready',          -- 'initializing', 'ready', 'locked'
    storage_quota_bytes BIGINT NOT NULL DEFAULT 10737418240, -- 10 GB default
    storage_used_bytes BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_vaults_status CHECK (status IN ('initializing', 'ready', 'locked'))
);

CREATE INDEX IF NOT EXISTS idx_vaults_tenant ON vaults(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vaults_default ON vaults(tenant_id, is_default) WHERE is_default = TRUE;

-- ----------------------------------------------------------------------------
-- 3. USERS: Natural person identity and profile
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    full_name VARCHAR(255) NULL,
    password_hash VARCHAR(255) NULL,
    avatar_url TEXT NULL,
    system_timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
    system_locale VARCHAR(32) NOT NULL DEFAULT 'en_US',
    subscription_status VARCHAR(32) NOT NULL DEFAULT 'active', -- 'active', 'past_due', 'cancelled'
    deletion_requested_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email),
    CONSTRAINT chk_users_subscription CHECK (subscription_status IN ('active', 'past_due', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_deletion ON users(deletion_requested_at) WHERE deletion_requested_at IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 4. DP_INSTANCES: Device Profile execution nodes / MCP servers
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dp_instances (
    dp_instance_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    dp_instance_name VARCHAR(255) NOT NULL,
    device_kind VARCHAR(32) NOT NULL,                    -- 'phone', 'watch', 'official_cloud', 'agent'
    dp_unit_url TEXT NOT NULL DEFAULT '',                -- Node RPC root
    dp_base_url TEXT NOT NULL DEFAULT '',                -- Node MCP derivation base URL
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_official BOOLEAN NOT NULL DEFAULT FALSE,          -- Hemory official cloud instance
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_dp_kind CHECK (device_kind IN ('phone', 'watch', 'official_cloud', 'agent'))
);

CREATE INDEX IF NOT EXISTS idx_dp_instances_user ON dp_instances(user_id);
CREATE INDEX IF NOT EXISTS idx_dp_instances_vault ON dp_instances(vault_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dp_instances_primary_unique ON dp_instances(tenant_id, vault_id, user_id) WHERE is_primary = TRUE;

-- ----------------------------------------------------------------------------
-- 5. DEVICE_BINDINGS: Hardware identity linking and push tokens
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_bindings (
    binding_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    dp_instance_id VARCHAR(64) NOT NULL REFERENCES dp_instances(dp_instance_id) ON DELETE CASCADE,
    device_unique_identifier VARCHAR(255) NOT NULL,
    device_model VARCHAR(128) NULL,
    os_name VARCHAR(32) NOT NULL,                        -- 'android', 'ios', 'watchos', 'linux'
    os_version VARCHAR(64) NULL,
    push_token TEXT NULL,                                -- FCM / APNS token (last_fid)
    status VARCHAR(32) NOT NULL DEFAULT 'bound',          -- 'bound', 'revoked', 'disconnected'
    bound_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_binding_device_user UNIQUE (user_id, device_unique_identifier),
    CONSTRAINT chk_binding_status CHECK (status IN ('bound', 'revoked', 'disconnected'))
);

CREATE INDEX IF NOT EXISTS idx_bindings_user ON device_bindings(user_id);
CREATE INDEX IF NOT EXISTS idx_bindings_dp ON device_bindings(dp_instance_id);
CREATE INDEX IF NOT EXISTS idx_bindings_unique_id ON device_bindings(device_unique_identifier);

-- ----------------------------------------------------------------------------
-- 6. RECORDING_SESSIONS: Continuous daily live listening sessions
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recording_sessions (
    live_session_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    dp_instance_id VARCHAR(64) NOT NULL REFERENCES dp_instances(dp_instance_id) ON DELETE CASCADE,
    local_day_key VARCHAR(10) NOT NULL,                  -- 'YYYY-MM-DD'
    title VARCHAR(255) NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NULL,
    duration_ms BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'recording',      -- 'recording', 'stopped', 'analyzed', 'archived'
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_sessions_status CHECK (status IN ('recording', 'stopped', 'analyzed', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_owner_day ON recording_sessions (tenant_id, vault_id, user_id, local_day_key);
CREATE INDEX IF NOT EXISTS idx_sessions_dp ON recording_sessions (dp_instance_id);
CREATE INDEX IF NOT EXISTS idx_sessions_time ON recording_sessions (started_at, ended_at);

-- ----------------------------------------------------------------------------
-- 7. RECORDING_SEGMENTS: Uploaded audio chunks & ingestion receipts
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recording_segments (
    segment_id VARCHAR(64) PRIMARY KEY,
    live_session_id VARCHAR(64) NOT NULL REFERENCES recording_sessions(live_session_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    dp_instance_id VARCHAR(64) NOT NULL REFERENCES dp_instances(dp_instance_id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    local_file_key VARCHAR(255) NULL,
    storage_key VARCHAR(512) NULL,                       -- S3/MinIO/Local key
    audio_format VARCHAR(16) NOT NULL DEFAULT 'aac',     -- 'aac', 'wav', 'pcm'
    sample_rate INTEGER NOT NULL DEFAULT 16000,
    bitrate_kbps INTEGER NOT NULL DEFAULT 48,
    duration_ms BIGINT NOT NULL DEFAULT 0,
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL,
    finalized_at TIMESTAMPTZ NULL,
    split_reason VARCHAR(64) NULL,                       -- 'window', 'silence', 'size_limit', 'user_stop', 'silence_window', 'hard_limit'
    upload_status VARCHAR(32) NOT NULL DEFAULT 'pending', -- 'pending', 'uploading', 'uploaded', 'failed'
    client_upload_id VARCHAR(64) NULL,
    upload_id VARCHAR(64) NULL,
    chunks_total INTEGER NOT NULL DEFAULT 1,
    chunks_received INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_segment_index UNIQUE (live_session_id, segment_index),
    CONSTRAINT chk_segments_status CHECK (upload_status IN ('pending', 'uploading', 'uploaded', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_segments_session ON recording_segments (live_session_id);
CREATE INDEX IF NOT EXISTS idx_segments_upload_status ON recording_segments (upload_status);
CREATE INDEX IF NOT EXISTS idx_segments_vault_time ON recording_segments (vault_id, started_at);

-- ----------------------------------------------------------------------------
-- 8. SECTIONS: Coherent conversational/topic blocks
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sections (
    section_id VARCHAR(64) PRIMARY KEY,
    live_session_id VARCHAR(64) NOT NULL REFERENCES recording_sessions(live_session_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    section_index INTEGER NOT NULL,
    title VARCHAR(255) NULL,
    start_ms BIGINT NOT NULL,
    end_ms BIGINT NOT NULL,
    transcript TEXT NULL,
    speaker_count INTEGER NOT NULL DEFAULT 0,
    quality_score REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_section_index UNIQUE (live_session_id, section_index)
);

CREATE INDEX IF NOT EXISTS idx_sections_session ON sections(live_session_id);
CREATE INDEX IF NOT EXISTS idx_sections_vault_time ON sections(vault_id, start_ms);

-- ----------------------------------------------------------------------------
-- 9. SECTION_MESSAGES: Utterances with speaker attribution & timestamps
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS section_messages (
    message_id VARCHAR(64) PRIMARY KEY,
    section_id VARCHAR(64) NOT NULL REFERENCES sections(section_id) ON DELETE CASCADE,
    speaker_id VARCHAR(64) NULL,                         -- Nullable if speaker unassigned
    speaker_name VARCHAR(128) NOT NULL DEFAULT 'Unknown',
    content TEXT NOT NULL,
    start_ms BIGINT NOT NULL,
    end_ms BIGINT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_section_messages_section ON section_messages(section_id);
CREATE INDEX IF NOT EXISTS idx_section_messages_speaker ON section_messages(speaker_id);
CREATE INDEX IF NOT EXISTS idx_section_messages_time ON section_messages(section_id, start_ms);

-- ----------------------------------------------------------------------------
-- 10. SUMMARIES: Periodic & topic-based memory condensations
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS summaries (
    summary_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    target_type VARCHAR(32) NOT NULL,                    -- 'session', 'section', 'day_digest', 'topic'
    target_id VARCHAR(64) NOT NULL,
    summary_markdown TEXT NOT NULL,
    topics JSONB NOT NULL DEFAULT '[]',
    action_items JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_summaries_type CHECK (target_type IN ('session', 'section', 'day_digest', 'topic'))
);

CREATE INDEX IF NOT EXISTS idx_summaries_target ON summaries(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_summaries_vault ON summaries(vault_id);

-- ----------------------------------------------------------------------------
-- 11. SPEAKER_PROFILES: Enrolled voice identities
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS speaker_profiles (
    speaker_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    suspected_name VARCHAR(255) NULL,
    role_label VARCHAR(128) NULL,
    color_palette VARCHAR(64) NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_speaker_profiles_vault ON speaker_profiles(vault_id);
CREATE INDEX IF NOT EXISTS idx_speaker_profiles_user ON speaker_profiles(user_id);

-- ----------------------------------------------------------------------------
-- 12. VOICEPRINT_CLUSTERS: Candidate acoustic clusters for review
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS voiceprint_clusters (
    cluster_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    speaker_id VARCHAR(64) NULL REFERENCES speaker_profiles(speaker_id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',        -- 'pending', 'identified', 'dismissed'
    embedding_blob BYTEA NULL,                            -- 512-dim voiceprint vector
    audio_sample_url TEXT NULL,
    confidence_score REAL NOT NULL DEFAULT 0.0,
    occurrences_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_clusters_status CHECK (status IN ('pending', 'identified', 'dismissed'))
);

CREATE INDEX IF NOT EXISTS idx_voiceprint_clusters_status ON voiceprint_clusters(vault_id, status);
CREATE INDEX IF NOT EXISTS idx_voiceprint_clusters_speaker ON voiceprint_clusters(speaker_id);

-- ----------------------------------------------------------------------------
-- 13. LISTENING_SCHEDULES: Weekday auto-listening windows with revision guard
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS listening_schedules (
    schedule_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    dp_instance_id VARCHAR(64) NOT NULL REFERENCES dp_instances(dp_instance_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL DEFAULT 1,
    weekly_windows JSONB NOT NULL DEFAULT '[]',
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_schedule_dp UNIQUE (dp_instance_id)
);

CREATE INDEX IF NOT EXISTS idx_schedules_dp ON listening_schedules(dp_instance_id);
CREATE INDEX IF NOT EXISTS idx_schedules_user ON listening_schedules(user_id);

-- ----------------------------------------------------------------------------
-- 14. MCP_API_KEYS: External LLM agent credentials & scopes
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mcp_api_keys (
    key_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    dp_instance_id VARCHAR(64) NOT NULL REFERENCES dp_instances(dp_instance_id) ON DELETE CASCADE,
    key_name VARCHAR(128) NOT NULL,
    key_hash VARCHAR(128) NOT NULL,                      -- SHA-256 hash of API key
    key_prefix VARCHAR(16) NOT NULL,                     -- e.g. 'hm_live_'
    scopes JSONB NOT NULL DEFAULT '["search_memory","search_sections"]',
    last_used_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_mcp_keys_hash UNIQUE (key_hash)
);

CREATE INDEX IF NOT EXISTS idx_mcp_api_keys_hash ON mcp_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_mcp_api_keys_dp ON mcp_api_keys(dp_instance_id);
CREATE INDEX IF NOT EXISTS idx_mcp_api_keys_user ON mcp_api_keys(user_id);
