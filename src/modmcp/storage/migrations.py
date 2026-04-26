"""SQLite schema for the modmcp ledger.

Idempotent. Called on daemon start-up (and by the test harness).

``SCHEMA_STATEMENTS`` is the source of truth for fresh installs (every
statement uses ``IF NOT EXISTS``). ``ADDITIVE_COLUMNS`` lists columns we
have added to existing tables since the schema went live. Each entry is
applied via ``ALTER TABLE ADD COLUMN`` and the duplicate-column error is
caught so re-runs are no-ops. There is no ``schema_version`` machinery
yet; once we need a destructive migration we'll introduce one.
"""

from __future__ import annotations

ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, type+default). Run on every connect; aiosqlite
    # raises OperationalError("duplicate column name: ...") which the
    # caller swallows.
    ("session_state", "last_message_id", "TEXT"),
    ("session_state", "total_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("session_state", "total_output_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("session_state", "total_cache_read_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("session_state", "total_cache_creation_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("session_state", "last_model", "TEXT"),
]


SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS verification_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        claim_text TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('verified','contradicted','unverifiable')),
        evidence TEXT,
        ran_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_verification_session ON verification_ledger(session_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS commitment_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        commitment_text TEXT NOT NULL,
        fulfilled INTEGER NOT NULL DEFAULT 0,
        fulfilled_at TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS drift_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL CHECK(severity IN ('low','med','high')),
        action_taken TEXT,
        detail TEXT,
        ran_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_drift_session ON drift_events(session_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS processed_offset (
        session_id TEXT PRIMARY KEY,
        jsonl_path TEXT NOT NULL,
        jsonl_offset INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS correction_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        text TEXT NOT NULL,
        consumed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        consumed_at TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_correction_pending
        ON correction_queue(session_id, consumed);
    """,
    """
    CREATE TABLE IF NOT EXISTS session_state (
        session_id TEXT PRIMARY KEY,
        project_hash TEXT NOT NULL,
        project_path TEXT NOT NULL,
        turns_seen INTEGER NOT NULL DEFAULT 0,
        phase2_active INTEGER NOT NULL DEFAULT 1,
        started_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        last_message_id TEXT,
        total_input_tokens INTEGER NOT NULL DEFAULT 0,
        total_output_tokens INTEGER NOT NULL DEFAULT 0,
        total_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        total_cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
        last_model TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS surfacings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        kind TEXT NOT NULL,
        severity TEXT NOT NULL,
        text TEXT NOT NULL,
        acknowledged INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
    # ---- v1.1 failure-mode audit layer ----
    """
    CREATE TABLE IF NOT EXISTS constraint_violations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        tool_call_id TEXT,
        rule_id TEXT NOT NULL,
        rule_text TEXT NOT NULL,
        evidence TEXT,
        severity TEXT NOT NULL CHECK(severity IN ('low','med','high')),
        status TEXT NOT NULL DEFAULT 'new'
            CHECK(status IN ('new','acknowledged','dismissed')),
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_violations_session
        ON constraint_violations(session_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_violations_project
        ON constraint_violations(project_hash);
    """,
    """
    CREATE TABLE IF NOT EXISTS scope_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        turn_idx INTEGER NOT NULL,
        files_touched_count INTEGER NOT NULL,
        diff_bytes INTEGER NOT NULL,
        tool_kinds_json TEXT NOT NULL,
        is_creep INTEGER NOT NULL DEFAULT 0,
        baseline INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scope_session
        ON scope_snapshots(session_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS rubric_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        turn_idx INTEGER NOT NULL,
        dim_name TEXT NOT NULL,
        score REAL NOT NULL,
        evidence TEXT,
        suggestion TEXT,
        model_used TEXT,
        trigger TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rubric_session
        ON rubric_scores(session_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS rubric_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rubric_score_id INTEGER NOT NULL,
        verdict TEXT NOT NULL CHECK(verdict IN ('agree','disagree')),
        note TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS session_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        mode_id INTEGER NOT NULL,
        mode_name TEXT NOT NULL,
        score REAL NOT NULL,
        evidence_json TEXT,
        suggestion TEXT,
        model_used TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(session_id, mode_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_reports_project
        ON session_reports(project_hash);
    """,
    """
    CREATE TABLE IF NOT EXISTS session_close (
        session_id TEXT PRIMARY KEY,
        project_hash TEXT NOT NULL,
        closed_at TEXT NOT NULL,
        consolidation_status TEXT NOT NULL
            CHECK(consolidation_status IN ('pending','running','done','error')),
        error TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS live_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_live_events_session
        ON live_events(session_id, id);
    """,
    # ---- v0.2 inference-path metrics ----
    """
    CREATE TABLE IF NOT EXISTS turn_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_hash TEXT NOT NULL,
        message_id TEXT,
        turn_idx INTEGER,
        model TEXT,
        stop_reason TEXT,
        -- Wall-clock metrics derived from JSONL timestamps
        prompt_to_response_ms INTEGER,
        response_duration_ms INTEGER,
        -- Token metrics from the assistant's usage block
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
        -- Derived
        output_tps REAL,
        cache_hit_ratio REAL,
        first_block_at TEXT,
        last_block_at TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_turn_metrics_session
        ON turn_metrics(session_id, id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_turn_metrics_project_model
        ON turn_metrics(project_hash, model, id);
    """,
]
