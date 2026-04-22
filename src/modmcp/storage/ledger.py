"""SQLite ledger for verification, drift events, commitments, and offsets."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from ..paths import ledger_path
from .migrations import SCHEMA_STATEMENTS


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Ledger:
    """Thin async wrapper around aiosqlite. One connection per daemon."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else ledger_path()
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        for stmt in SCHEMA_STATEMENTS:
            await self._conn.execute(stmt)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Ledger not connected")
        return self._conn

    # ------- processed_offset -------

    async def get_offset(self, session_id: str) -> int:
        async with self.conn.execute(
            "SELECT jsonl_offset FROM processed_offset WHERE session_id=?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        return int(row["jsonl_offset"]) if row else 0

    async def set_offset(self, session_id: str, jsonl_path: str, offset: int) -> None:
        await self.conn.execute(
            """
            INSERT INTO processed_offset(session_id, jsonl_path, jsonl_offset, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                jsonl_path=excluded.jsonl_path,
                jsonl_offset=excluded.jsonl_offset,
                updated_at=excluded.updated_at
            """,
            (session_id, jsonl_path, offset, _now_iso()),
        )
        await self.conn.commit()

    # ------- session_state -------

    async def upsert_session(
        self, session_id: str, project_hash: str, project_path: str
    ) -> None:
        now = _now_iso()
        await self.conn.execute(
            """
            INSERT INTO session_state(session_id, project_hash, project_path, started_at, last_seen_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_seen_at=excluded.last_seen_at
            """,
            (session_id, project_hash, project_path, now, now),
        )
        await self.conn.commit()

    async def bump_turns(self, session_id: str) -> int:
        await self.conn.execute(
            "UPDATE session_state SET turns_seen = turns_seen + 1, last_seen_at=? WHERE session_id=?",
            (_now_iso(), session_id),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT turns_seen FROM session_state WHERE session_id=?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        return int(row["turns_seen"]) if row else 0

    async def get_session(self, session_id: str) -> dict | None:
        async with self.conn.execute(
            "SELECT * FROM session_state WHERE session_id=?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    # ------- correction_queue -------

    async def enqueue_correction(self, session_id: str, project_hash: str, text: str) -> None:
        await self.conn.execute(
            """INSERT INTO correction_queue(session_id, project_hash, text, created_at)
               VALUES(?, ?, ?, ?)""",
            (session_id, project_hash, text, _now_iso()),
        )
        await self.conn.commit()

    async def drain_corrections(self, session_id: str) -> list[str]:
        async with self.conn.execute(
            """SELECT id, text FROM correction_queue
               WHERE session_id=? AND consumed=0 ORDER BY id""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        await self.conn.executemany(
            "UPDATE correction_queue SET consumed=1, consumed_at=? WHERE id=?",
            [(_now_iso(), i) for i in ids],
        )
        await self.conn.commit()
        return [r["text"] for r in rows]

    # ------- drift_events -------

    async def record_drift(
        self,
        session_id: str,
        project_hash: str,
        event_type: str,
        severity: str,
        action_taken: str | None,
        detail: str | None,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO drift_events(session_id, project_hash, event_type, severity,
               action_taken, detail, ran_at) VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (session_id, project_hash, event_type, severity, action_taken, detail, _now_iso()),
        )
        await self.conn.commit()

    async def recent_drift(self, project_hash: str, limit: int = 50) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM drift_events WHERE project_hash=?
               ORDER BY id DESC LIMIT ?""",
            (project_hash, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------- verification_ledger -------

    async def record_claim(
        self,
        session_id: str,
        project_hash: str,
        claim_text: str,
        status: str,
        evidence: str | None,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO verification_ledger(session_id, project_hash, claim_text,
               status, evidence, ran_at) VALUES(?, ?, ?, ?, ?, ?)""",
            (session_id, project_hash, claim_text, status, evidence, _now_iso()),
        )
        await self.conn.commit()

    async def recent_claims(self, project_hash: str, limit: int = 50) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM verification_ledger WHERE project_hash=?
               ORDER BY id DESC LIMIT ?""",
            (project_hash, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------- surfacings -------

    async def record_surfacing(
        self,
        session_id: str,
        project_hash: str,
        kind: str,
        severity: str,
        text: str,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO surfacings(session_id, project_hash, kind, severity, text, created_at)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (session_id, project_hash, kind, severity, text, _now_iso()),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)
