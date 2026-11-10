-- ============================================================================
-- HEMORY CHAT, PROCESSING CHAINS & MUTATIONS SCHEMA (Milestone M4)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. CHAT THREADS & TURNS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_threads (
    thread_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    title VARCHAR(255) NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_threads_vault ON chat_threads(vault_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_user ON chat_threads(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_tenant ON chat_threads(tenant_id);

CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id VARCHAR(64) PRIMARY KEY,
    thread_id VARCHAR(64) NOT NULL REFERENCES chat_threads(thread_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL DEFAULT 1,
    prompt TEXT NOT NULL,
    response_text TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    tokens_used INTEGER NOT NULL DEFAULT 0,
    client_turn_id VARCHAR(64) NULL,
    client_context TEXT NOT NULL DEFAULT '{}',
    error_code VARCHAR(64) NULL,
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT uq_thread_turn_index UNIQUE (thread_id, turn_index),
    CONSTRAINT chk_turns_status CHECK (status IN ('pending', 'recalling', 'executing', 'summarizing', 'completed', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_chat_turns_thread ON chat_turns(thread_id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_vault ON chat_turns(vault_id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_status ON chat_turns(status);

-- ----------------------------------------------------------------------------
-- 2. PROCESSING CHAINS (Feature F4.4, P1 Timeline Clipping)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processing_chains (
    chain_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    recording_id VARCHAR(64) NOT NULL,
    live_session_id VARCHAR(64) NULL REFERENCES recording_sessions(live_session_id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    current_step VARCHAR(32) NOT NULL DEFAULT 'slicing',
    progress_pct REAL NOT NULL DEFAULT 0.0,
    error_code VARCHAR(64) NULL,
    error_message TEXT NULL,
    analyzing_start_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    analyzing_end_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_chain_status CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    CONSTRAINT chk_chain_step CHECK (current_step IN ('slicing', 'vad', 'transcribing', 'diarizing', 'summarizing')),
    CONSTRAINT chk_chain_progress CHECK (progress_pct >= 0.0 AND progress_pct <= 100.0)
);

CREATE INDEX IF NOT EXISTS idx_chains_vault_status ON processing_chains(vault_id, status);
CREATE INDEX IF NOT EXISTS idx_chains_vault_updated ON processing_chains(vault_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_chains_recording ON processing_chains(recording_id);
CREATE INDEX IF NOT EXISTS idx_chains_session ON processing_chains(live_session_id);

-- ----------------------------------------------------------------------------
-- 3. RECORDING MUTATIONS (Feature F4.4, Causal Cursor Ordering)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recording_mutations (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(64) NOT NULL,
    mutation_type VARCHAR(64) NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mutations_vault_cursor ON recording_mutations(vault_id, cursor);
CREATE INDEX IF NOT EXISTS idx_mutations_vault_type ON recording_mutations(vault_id, mutation_type);
CREATE INDEX IF NOT EXISTS idx_mutations_entity ON recording_mutations(entity_id);

-- ----------------------------------------------------------------------------
-- 4. EPISODIC MEMORY EVENTS (Feature F4.3, P2 Activity)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_events (
    event_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
    session_id VARCHAR(64) NULL REFERENCES recording_sessions(live_session_id) ON DELETE SET NULL,
    title VARCHAR(255) NOT NULL,
    summary TEXT NOT NULL,
    category VARCHAR(64) NOT NULL DEFAULT 'general',
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    speaker_names TEXT NOT NULL DEFAULT '[]',
    keywords TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_events_vault ON memory_events(vault_id, start_time);
CREATE INDEX IF NOT EXISTS idx_memory_events_session ON memory_events(session_id);

-- ----------------------------------------------------------------------------
-- 5. SQLITE FTS5 FULL-TEXT SEARCH INDEXES & TRIGGERS
-- ----------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS section_messages_fts USING fts5(
    message_id UNINDEXED,
    content,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS trg_section_messages_fts_insert AFTER INSERT ON section_messages BEGIN
    INSERT INTO section_messages_fts(message_id, content) VALUES (new.message_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_section_messages_fts_delete AFTER DELETE ON section_messages BEGIN
    DELETE FROM section_messages_fts WHERE message_id = old.message_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_section_messages_fts_update AFTER UPDATE OF content ON section_messages BEGIN
    DELETE FROM section_messages_fts WHERE message_id = old.message_id;
    INSERT INTO section_messages_fts(message_id, content) VALUES (new.message_id, new.content);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS memory_events_fts USING fts5(
    event_id UNINDEXED,
    title,
    summary,
    keywords,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS trg_memory_events_fts_insert AFTER INSERT ON memory_events BEGIN
    INSERT INTO memory_events_fts(event_id, title, summary, keywords) VALUES (new.event_id, new.title, new.summary, new.keywords);
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_events_fts_delete AFTER DELETE ON memory_events BEGIN
    DELETE FROM memory_events_fts WHERE event_id = old.event_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_events_fts_update AFTER UPDATE ON memory_events BEGIN
    DELETE FROM memory_events_fts WHERE event_id = old.event_id;
    INSERT INTO memory_events_fts(event_id, title, summary, keywords) VALUES (new.event_id, new.title, new.summary, new.keywords);
END;
