"""SQLite schema for the modmcp ledger.

Idempotent. Called on daemon start-up (and by the test harness).
"""

from __future__ import annotations

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
        last_seen_at TEXT NOT NULL
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
]
