"""SQLite ledger for verification, drift events, commitments, and offsets."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from ..paths import ledger_path
from .migrations import ADDITIVE_COLUMNS, SCHEMA_STATEMENTS


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
        # Idempotent additive migrations for columns we've introduced
        # since the schema first shipped. SQLite raises a duplicate-column
        # OperationalError on re-runs; treat that as success so re-opens
        # of an already-up-to-date DB are no-ops.
        for table, col, definition in ADDITIVE_COLUMNS:
            try:
                await self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col} {definition}"
                )
            except aiosqlite.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
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

    # ------- constraint_violations (v1.1) -------

    async def record_constraint_violation(
        self,
        session_id: str,
        project_hash: str,
        *,
        tool_call_id: str | None,
        rule_id: str,
        rule_text: str,
        evidence: str | None,
        severity: str,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO constraint_violations(
                 session_id, project_hash, tool_call_id, rule_id, rule_text,
                 evidence, severity, status, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (
                session_id, project_hash, tool_call_id, rule_id, rule_text,
                evidence, severity, _now_iso(),
            ),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def recent_violations(
        self, project_hash: str, limit: int = 100
    ) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM constraint_violations WHERE project_hash=?
               ORDER BY id DESC LIMIT ?""",
            (project_hash, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def violations_for_session(self, session_id: str) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM constraint_violations WHERE session_id=?
               ORDER BY id""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def set_violation_status(self, violation_id: int, status: str) -> bool:
        if status not in ("new", "acknowledged", "dismissed"):
            raise ValueError(f"invalid status: {status!r}")
        cur = await self.conn.execute(
            "UPDATE constraint_violations SET status=? WHERE id=?",
            (status, violation_id),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    # ------- scope_snapshots (v1.1) -------

    async def record_scope_snapshot(
        self,
        session_id: str,
        project_hash: str,
        *,
        turn_idx: int,
        files_touched_count: int,
        diff_bytes: int,
        tool_kinds_json: str,
        is_creep: bool,
        baseline: int,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO scope_snapshots(
                 session_id, project_hash, turn_idx, files_touched_count,
                 diff_bytes, tool_kinds_json, is_creep, baseline, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, project_hash, turn_idx, files_touched_count,
                diff_bytes, tool_kinds_json, 1 if is_creep else 0,
                baseline, _now_iso(),
            ),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def scope_snapshots_for_session(self, session_id: str) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM scope_snapshots WHERE session_id=? ORDER BY id""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def baseline_files_touched(
        self, project_hash: str, window: int
    ) -> int:
        """Rolling median of files_touched peak across prior sessions."""
        async with self.conn.execute(
            """SELECT MAX(files_touched_count) AS peak FROM scope_snapshots
               WHERE project_hash=?
               GROUP BY session_id
               ORDER BY MAX(id) DESC LIMIT ?""",
            (project_hash, window),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return 0
        peaks = sorted(int(r["peak"]) for r in rows if r["peak"] is not None)
        if not peaks:
            return 0
        return peaks[len(peaks) // 2]

    # ------- rubric_scores (v1.1) -------

    async def record_rubric_score(
        self,
        session_id: str,
        project_hash: str,
        *,
        turn_idx: int,
        dim_name: str,
        score: float,
        evidence: str | None,
        suggestion: str | None,
        model_used: str | None,
        trigger: str | None,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO rubric_scores(
                 session_id, project_hash, turn_idx, dim_name, score,
                 evidence, suggestion, model_used, trigger, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, project_hash, turn_idx, dim_name, score,
                evidence, suggestion, model_used, trigger, _now_iso(),
            ),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def rubric_scores_for_session(self, session_id: str) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM rubric_scores WHERE session_id=? ORDER BY id""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def record_rubric_feedback(
        self, rubric_score_id: int, verdict: str, note: str | None
    ) -> None:
        if verdict not in ("agree", "disagree"):
            raise ValueError(f"invalid verdict: {verdict!r}")
        await self.conn.execute(
            """INSERT INTO rubric_feedback(
                 rubric_score_id, verdict, note, created_at
               ) VALUES(?, ?, ?, ?)""",
            (rubric_score_id, verdict, note, _now_iso()),
        )
        await self.conn.commit()

    # ------- session_reports (v1.1) -------

    async def upsert_session_report(
        self,
        session_id: str,
        project_hash: str,
        *,
        mode_id: int,
        mode_name: str,
        score: float,
        evidence_json: str | None,
        suggestion: str | None,
        model_used: str | None,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO session_reports(
                 session_id, project_hash, mode_id, mode_name, score,
                 evidence_json, suggestion, model_used, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id, mode_id) DO UPDATE SET
                 mode_name=excluded.mode_name,
                 score=excluded.score,
                 evidence_json=excluded.evidence_json,
                 suggestion=excluded.suggestion,
                 model_used=excluded.model_used,
                 created_at=excluded.created_at""",
            (
                session_id, project_hash, mode_id, mode_name, score,
                evidence_json, suggestion, model_used, _now_iso(),
            ),
        )
        await self.conn.commit()

    async def session_report(self, session_id: str) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM session_reports WHERE session_id=? ORDER BY mode_id""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def recent_session_reports(
        self, project_hash: str, limit: int = 20
    ) -> list[dict]:
        """Return reports grouped by session, newest session first."""
        async with self.conn.execute(
            """SELECT * FROM session_reports WHERE project_hash=?
               ORDER BY id DESC LIMIT ?""",
            (project_hash, limit * 8),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------- session_close (v1.1) -------

    async def mark_session_closed(
        self, session_id: str, project_hash: str, status: str = "pending"
    ) -> None:
        await self.conn.execute(
            """INSERT INTO session_close(
                 session_id, project_hash, closed_at, consolidation_status
               ) VALUES(?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 consolidation_status=excluded.consolidation_status,
                 closed_at=excluded.closed_at""",
            (session_id, project_hash, _now_iso(), status),
        )
        await self.conn.commit()

    async def update_close_status(
        self, session_id: str, status: str, error: str | None = None
    ) -> None:
        await self.conn.execute(
            """UPDATE session_close SET consolidation_status=?, error=?
               WHERE session_id=?""",
            (status, error, session_id),
        )
        await self.conn.commit()

    async def session_close_status(self, session_id: str) -> str | None:
        async with self.conn.execute(
            "SELECT consolidation_status FROM session_close WHERE session_id=?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        return str(row["consolidation_status"]) if row else None

    async def session_close_row(self, session_id: str) -> dict | None:
        """Full session_close row (closed_at, status, error). Used by the
        live view to detect a session that has resumed past the close."""
        async with self.conn.execute(
            "SELECT * FROM session_close WHERE session_id=?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def sessions_needing_consolidation(self) -> list[dict]:
        async with self.conn.execute(
            """SELECT sc.session_id, sc.project_hash, ss.project_path
                 FROM session_close sc
                 JOIN session_state ss ON ss.session_id = sc.session_id
                WHERE sc.consolidation_status IN ('pending', 'error')"""
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def all_session_state(self) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM session_state ORDER BY last_seen_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_session_progress(
        self,
        session_id: str,
        *,
        turns_seen: int,
        last_message_id: str | None,
        total_input_tokens: int,
        total_output_tokens: int,
        total_cache_read_tokens: int,
        total_cache_creation_tokens: int,
        last_model: str | None,
    ) -> None:
        """Persist the cumulative-progress columns for a session.

        Called by the watcher on every logical-turn boundary so the
        in-memory ``SessionState`` can be hydrated from the DB after a
        daemon restart instead of starting fresh.
        """
        await self.conn.execute(
            """UPDATE session_state SET
                 turns_seen=?,
                 last_message_id=?,
                 total_input_tokens=?,
                 total_output_tokens=?,
                 total_cache_read_tokens=?,
                 total_cache_creation_tokens=?,
                 last_model=COALESCE(?, last_model),
                 last_seen_at=?
               WHERE session_id=?""",
            (
                turns_seen,
                last_message_id,
                total_input_tokens,
                total_output_tokens,
                total_cache_read_tokens,
                total_cache_creation_tokens,
                last_model,
                _now_iso(),
                session_id,
            ),
        )
        await self.conn.commit()

    # ------- live_events (v1.1) -------

    async def record_live_event(
        self,
        session_id: str,
        project_hash: str,
        event_type: str,
        payload: str,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO live_events(
                 session_id, project_hash, event_type, payload, created_at
               ) VALUES(?, ?, ?, ?, ?)""",
            (session_id, project_hash, event_type, payload, _now_iso()),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def live_events_for_session(
        self, session_id: str, *, since_id: int = 0, limit: int = 500
    ) -> list[dict]:
        """Forward catch-up: events with ``id > since_id``, oldest first.

        Used by SSE backfill and the polling fallback to walk forward
        from the client's last seen id. For initial page-load replay
        callers should use :meth:`live_events_recent` instead — this
        method returns the *prefix*, not the tail.
        """
        async with self.conn.execute(
            """SELECT * FROM live_events
               WHERE session_id=? AND id > ?
               ORDER BY id LIMIT ?""",
            (session_id, since_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def live_events_recent(
        self, session_id: str, *, limit: int = 100
    ) -> list[dict]:
        """Latest ``limit`` events for the session, returned chronologically.

        The page-load replay wants recency — what just happened — not the
        first N events recorded. ``ORDER BY id DESC LIMIT N`` selects the
        tail, then we reverse so callers can render in arrival order.
        """
        async with self.conn.execute(
            """SELECT * FROM live_events
               WHERE session_id=?
               ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return list(reversed([dict(r) for r in rows]))

    # ------- turn_metrics (v0.2 inference path) -------

    async def record_turn_metric(
        self,
        session_id: str,
        project_hash: str,
        *,
        message_id: str | None,
        turn_idx: int | None,
        model: str | None,
        stop_reason: str | None,
        prompt_to_response_ms: int | None,
        response_duration_ms: int | None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        output_tps: float | None,
        cache_hit_ratio: float | None,
        first_block_at: str | None,
        last_block_at: str | None,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO turn_metrics(
                 session_id, project_hash, message_id, turn_idx, model,
                 stop_reason, prompt_to_response_ms, response_duration_ms,
                 input_tokens, output_tokens, cache_read_tokens,
                 cache_creation_tokens, output_tps, cache_hit_ratio,
                 first_block_at, last_block_at, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, project_hash, message_id, turn_idx, model,
                stop_reason, prompt_to_response_ms, response_duration_ms,
                input_tokens, output_tokens, cache_read_tokens,
                cache_creation_tokens, output_tps, cache_hit_ratio,
                first_block_at, last_block_at, _now_iso(),
            ),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def turn_metrics_for_session(
        self, session_id: str, *, limit: int = 200
    ) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM turn_metrics
               WHERE session_id=?
               ORDER BY id LIMIT ?""",
            (session_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def turn_metrics_for_project(
        self, project_hash: str, *, model: str | None = None, limit: int = 500
    ) -> list[dict]:
        if model is not None:
            sql = """SELECT * FROM turn_metrics
                     WHERE project_hash=? AND model=?
                     ORDER BY id DESC LIMIT ?"""
            params: tuple = (project_hash, model, limit)
        else:
            sql = """SELECT * FROM turn_metrics
                     WHERE project_hash=?
                     ORDER BY id DESC LIMIT ?"""
            params = (project_hash, limit)
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return list(reversed([dict(r) for r in rows]))

    # ------- probe_results (v0.2 platform) -------

    async def record_probe_result(
        self,
        target: str,
        url: str,
        *,
        status: str,
        latency_ms: int | None,
        detail: str | None,
        error: str | None,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO probe_results(
                 target, url, status, latency_ms, detail, error, ts
               ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (target, url, status, latency_ms, detail, error, _now_iso()),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def recent_probe_results(
        self, *, target: str | None = None, limit: int = 200
    ) -> list[dict]:
        if target is not None:
            sql = """SELECT * FROM probe_results
                     WHERE target=? ORDER BY id DESC LIMIT ?"""
            params: tuple = (target, limit)
        else:
            sql = """SELECT * FROM probe_results
                     ORDER BY id DESC LIMIT ?"""
            params = (limit,)
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return list(reversed([dict(r) for r in rows]))

    async def latest_session_for_project(self, project_hash: str) -> str | None:
        async with self.conn.execute(
            """SELECT session_id FROM session_state WHERE project_hash=?
               ORDER BY last_seen_at DESC LIMIT 1""",
            (project_hash,),
        ) as cur:
            row = await cur.fetchone()
        return str(row["session_id"]) if row else None

    async def sessions_for_project(
        self, project_hash: str, limit: int = 20
    ) -> list[dict]:
        async with self.conn.execute(
            """SELECT * FROM session_state WHERE project_hash=?
               ORDER BY last_seen_at DESC LIMIT ?""",
            (project_hash, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
