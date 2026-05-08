"""SQLite ledger for verification, drift events, commitments, and offsets."""

from __future__ import annotations

from contextlib import asynccontextmanager
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
        # Re-entrant counter for ``batch_commits``: write methods route
        # through ``_commit()`` which no-ops while ``_batch_depth > 0``,
        # so a seed pass that calls thousands of write methods commits
        # exactly once on context exit instead of once per call.
        self._batch_depth: int = 0

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

    async def _commit(self) -> None:
        """Centralised commit gate. Write methods call this instead of
        ``self.conn.commit()`` directly so a ``batch_commits()`` scope
        can suppress per-call fsyncs and commit once on exit."""
        if self._batch_depth == 0:
            await self.conn.commit()

    @asynccontextmanager
    async def batch_commits(self):
        """Suspend per-write commits inside this scope; single commit
        on exit. Re-entrant — nested batches share the outermost
        commit. Used by ``seed_project``'s prime pass to turn O(N)
        fsyncs into 1; safe for any batch operation that doesn't need
        intermediate durability.

        Crash semantics: if the process dies inside the batch, the
        outstanding writes are rolled back. For seed (which is
        re-runnable from byte-zero of the JSONL) that's the right
        trade — the user can re-trigger seed and the ledger
        reconverges."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                await self.conn.commit()

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
        await self._commit()

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
        await self._commit()

    async def bump_turns(self, session_id: str) -> int:
        await self.conn.execute(
            "UPDATE session_state SET turns_seen = turns_seen + 1, last_seen_at=? WHERE session_id=?",
            (_now_iso(), session_id),
        )
        await self._commit()
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
        await self._commit()

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
        await self._commit()

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
        await self._commit()
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
        await self._commit()
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
        await self._commit()
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
        session_mode: str | None = None,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO scope_snapshots(
                 session_id, project_hash, turn_idx, files_touched_count,
                 diff_bytes, tool_kinds_json, is_creep, baseline,
                 session_mode, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, project_hash, turn_idx, files_touched_count,
                diff_bytes, tool_kinds_json, 1 if is_creep else 0,
                baseline, session_mode, _now_iso(),
            ),
        )
        await self._commit()
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
        session_mode: str | None = None,
        subject: str = "assistant",
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO rubric_scores(
                 session_id, project_hash, turn_idx, dim_name, score,
                 evidence, suggestion, model_used, trigger,
                 session_mode, subject, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, project_hash, turn_idx, dim_name, score,
                evidence, suggestion, model_used, trigger,
                session_mode, subject, _now_iso(),
            ),
        )
        await self._commit()
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
        await self._commit()

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
        await self._commit()

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
        await self._commit()

    async def update_close_status(
        self, session_id: str, status: str, error: str | None = None
    ) -> None:
        await self.conn.execute(
            """UPDATE session_close SET consolidation_status=?, error=?
               WHERE session_id=?""",
            (status, error, session_id),
        )
        await self._commit()

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
        await self._commit()

    # ------- live_events (v1.1) -------

    async def record_live_event(
        self,
        session_id: str,
        project_hash: str,
        event_type: str,
        payload: str,
        *,
        event_ts: str | None = None,
    ) -> int:
        # ``created_at`` is always insert-time so the live feed sorts
        # monotonically by fire-time. ``event_ts`` carries the source
        # JSONL event's original timestamp for rows derived from a
        # specific event (on_event publishes, rule-based worker
        # outputs); NULL for rows without a single source (periodic
        # synthesis captures, etc.). Past-session views sort by
        # COALESCE(event_ts, created_at) to reconstruct the original
        # session timeline.
        cur = await self.conn.execute(
            """INSERT INTO live_events(
                 session_id, project_hash, event_type, payload,
                 created_at, event_ts
               ) VALUES(?, ?, ?, ?, ?, ?)""",
            (session_id, project_hash, event_type, payload,
             _now_iso(), event_ts),
        )
        await self._commit()
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

    async def session_arc_triples(self, session_id: str) -> list[dict]:
        """Lightweight per-event tuples for the SessionTimeline arc.

        Returns ``[{id, event_type, created_at, event_ts}, ...]`` for
        every event in the session, ordered chronologically. The
        frontend renders each as a single tick/cluster on the arc
        strip — payloads are not needed, so the query strips them.
        Cheap enough to fetch the full session's worth of rows on
        bootstrap (~60 bytes per row × thousands of rows = a few
        hundred KB max).

        ``event_ts`` carries the source JSONL event's timestamp on
        rows derived from a specific event; the frontend prefers it
        over ``created_at`` so the arc reflects the original session
        timeline rather than today's insert times when viewing a
        seeded historical session.

        The richer feed-replay endpoint is paginated and tail-windowed
        by design (visible-prose payloads are large); this endpoint
        intentionally side-steps that limit so the arc reflects the
        whole session, not just the loaded feed window.
        """
        async with self.conn.execute(
            """SELECT id, event_type, created_at, event_ts
               FROM live_events
               WHERE session_id=?
               ORDER BY id""",
            (session_id,),
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

    async def live_events_before(
        self, session_id: str, *, before_id: int, limit: int = 100
    ) -> list[dict]:
        """Events with ``id < before_id``, returning the most-recent
        ``limit`` of those (i.e. paginating backwards). Returned in
        chronological order so the caller can prepend without resorting.

        Used by the live-feed "load older" affordance: the client
        passes the lowest id it currently has rendered, and we hand
        back the next-older batch.
        """
        async with self.conn.execute(
            """SELECT * FROM live_events
               WHERE session_id=? AND id < ?
               ORDER BY id DESC LIMIT ?""",
            (session_id, before_id, limit),
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
        session_mode: str | None = None,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO turn_metrics(
                 session_id, project_hash, message_id, turn_idx, model,
                 stop_reason, prompt_to_response_ms, response_duration_ms,
                 input_tokens, output_tokens, cache_read_tokens,
                 cache_creation_tokens, output_tps, cache_hit_ratio,
                 first_block_at, last_block_at, session_mode, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, project_hash, message_id, turn_idx, model,
                stop_reason, prompt_to_response_ms, response_duration_ms,
                input_tokens, output_tokens, cache_read_tokens,
                cache_creation_tokens, output_tps, cache_hit_ratio,
                first_block_at, last_block_at, session_mode, _now_iso(),
            ),
        )
        await self._commit()
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

    # ------- llm_call_metrics (v0.2 platform) -------

    async def record_llm_call_metric(
        self,
        *,
        call_kind: str,
        model: str | None,
        max_tokens: int | None,
        enable_thinking: bool | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        reasoning_tokens: int | None,
        total_tokens: int | None,
        finish_reason: str | None,
        duration_ms: int | None,
        usage_json: str | None,
        error: str | None,
    ) -> int:
        cur = await self.conn.execute(
            """INSERT INTO llm_call_metrics(
                 call_kind, model, max_tokens, enable_thinking,
                 prompt_tokens, completion_tokens, reasoning_tokens,
                 total_tokens, finish_reason, duration_ms, usage_json,
                 error, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                call_kind, model, max_tokens,
                None if enable_thinking is None else (1 if enable_thinking else 0),
                prompt_tokens, completion_tokens, reasoning_tokens,
                total_tokens, finish_reason, duration_ms, usage_json,
                error, _now_iso(),
            ),
        )
        await self._commit()
        return int(cur.lastrowid or 0)

    async def llm_call_metrics_recent(
        self, *, call_kind: str | None = None, limit: int = 200
    ) -> list[dict]:
        if call_kind is not None:
            sql = """SELECT * FROM llm_call_metrics
                     WHERE call_kind=? ORDER BY id DESC LIMIT ?"""
            params: tuple = (call_kind, limit)
        else:
            sql = """SELECT * FROM llm_call_metrics
                     ORDER BY id DESC LIMIT ?"""
            params = (limit,)
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return list(reversed([dict(r) for r in rows]))

    async def llm_call_metrics_summary(self) -> list[dict]:
        """Per-call-kind aggregates for the inspection endpoint.

        SQLite has no median, so we return count + finish-reason
        histogram + averages + 95th-percentile-ish via a window of the
        max value; the route layer picks whichever shape is useful.
        For the truncation question, ``finish_reason='length'`` count
        and ``avg(completion_tokens)`` vs ``max_tokens`` are the
        load-bearing fields.
        """
        async with self.conn.execute(
            """SELECT
                 call_kind,
                 COUNT(*) AS n,
                 SUM(CASE WHEN finish_reason='length' THEN 1 ELSE 0 END) AS n_length,
                 SUM(CASE WHEN finish_reason='stop'   THEN 1 ELSE 0 END) AS n_stop,
                 SUM(CASE WHEN error IS NOT NULL      THEN 1 ELSE 0 END) AS n_error,
                 AVG(prompt_tokens)        AS avg_prompt,
                 AVG(completion_tokens)    AS avg_completion,
                 MAX(completion_tokens)    AS max_completion,
                 AVG(reasoning_tokens)     AS avg_reasoning,
                 MAX(reasoning_tokens)     AS max_reasoning,
                 AVG(duration_ms)          AS avg_duration_ms,
                 MAX(max_tokens)           AS configured_max_tokens
               FROM llm_call_metrics
               GROUP BY call_kind
               ORDER BY call_kind"""
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

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
        await self._commit()
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

    async def projects_summary(self) -> list[dict]:
        """One row per project tailward has seen, with session count and
        most-recent activity. Used by the v2.1 landing page.
        """
        async with self.conn.execute(
            """SELECT project_hash,
                      MAX(project_path) AS project_path,
                      COUNT(*) AS session_count,
                      MAX(last_seen_at) AS last_active_at
               FROM session_state
               GROUP BY project_hash
               ORDER BY MAX(last_seen_at) DESC"""
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def recent_sessions(self, limit: int = 30) -> list[dict]:
        """Recent sessions across all projects, newest first.

        Powers the v0.2 HeaderBar session picker, which lets the user
        jump between active and recent sessions across the projects
        tailward is watching. Returns the columns the picker actually
        renders — id, project, recency, model, turn count — so the
        frontend doesn't paint a heavy row.
        """
        async with self.conn.execute(
            """SELECT session_id, project_hash, project_path, started_at,
                      last_seen_at, last_model, turns_seen
               FROM session_state
               ORDER BY last_seen_at DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------- Reflection-view self-rubric panel -------

    async def user_rubric_summary(self, project_hash: str) -> dict:
        """Per-dimension average + sample count for user-side scores.

        Powers the Reflection-view self-rubric panel's "averages by
        dimension" row. Filters to ``subject='user'`` so the
        assistant-side rubric doesn't pollute the math.
        """
        async with self.conn.execute(
            """SELECT dim_name, AVG(score) AS avg_score, COUNT(*) AS n
               FROM rubric_scores
               WHERE project_hash=? AND subject='user'
               GROUP BY dim_name""",
            (project_hash,),
        ) as cur:
            rows = await cur.fetchall()
        return {
            "by_dim": [
                {
                    "dim": str(r["dim_name"]),
                    "avg_score": float(r["avg_score"]) if r["avg_score"] is not None else None,
                    "n": int(r["n"]),
                }
                for r in rows
            ],
        }

    async def user_rubric_recent(
        self, project_hash: str, limit: int = 30
    ) -> list[dict]:
        """Recent user-rubric samples for a project, newest first.

        Returns the rows with evidence + suggestion populated so the
        panel can show the latest specific feedback the LLM produced
        about the user's behavior.
        """
        async with self.conn.execute(
            """SELECT id, session_id, turn_idx, dim_name, score,
                      evidence, suggestion, created_at, session_mode
               FROM rubric_scores
               WHERE project_hash=? AND subject='user'
               ORDER BY id DESC LIMIT ?""",
            (project_hash, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------- Reflection-view past-sessions panel -------

    async def recent_sessions_with_summary(self, limit: int = 50) -> list[dict]:
        """Cross-project recent sessions enriched with rubric averages
        and a "has report card" flag.

        Powers the Reflection-view past-sessions table. Pre-calibration
        sessions (rubric truncated mid-think) will have ``avg_score``
        clustered near 3.0 — that's noise, documented in
        project_v0_2_reports_gap.md. We surface the data anyway and
        leave interpretation to the user; visual treatment of noisy
        rows is a UI concern.
        """
        async with self.conn.execute(
            """
            SELECT
                ss.session_id, ss.project_hash, ss.project_path,
                ss.started_at, ss.last_seen_at, ss.last_model,
                ss.turns_seen, ss.total_input_tokens, ss.total_output_tokens,
                rs.avg_score, rs.sample_count,
                urs.avg_score AS user_avg_score,
                urs.sample_count AS user_sample_count,
                CASE WHEN sr.session_id IS NOT NULL THEN 1 ELSE 0 END AS has_report
            FROM session_state ss
            LEFT JOIN (
                SELECT session_id, AVG(score) AS avg_score, COUNT(*) AS sample_count
                FROM rubric_scores
                WHERE subject='assistant'
                GROUP BY session_id
            ) rs ON rs.session_id = ss.session_id
            LEFT JOIN (
                SELECT session_id, AVG(score) AS avg_score, COUNT(*) AS sample_count
                FROM rubric_scores
                WHERE subject='user'
                GROUP BY session_id
            ) urs ON urs.session_id = ss.session_id
            LEFT JOIN (
                SELECT DISTINCT session_id FROM session_reports
            ) sr ON sr.session_id = ss.session_id
            ORDER BY ss.last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def session_rubric_trajectory(self, session_id: str) -> list[dict]:
        """All rubric_scores for one session, ordered by turn then dim.

        Powers the per-session deep view's trajectory plot. Returns
        every dimension/turn pair so the UI can pivot however it wants
        (line per dim, faceted, average per turn).
        """
        async with self.conn.execute(
            """SELECT id, turn_idx, dim_name, score, evidence, suggestion,
                      created_at, session_mode
               FROM rubric_scores
               WHERE session_id=?
               ORDER BY turn_idx ASC, dim_name ASC""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------- Reflection-view signals (derived; no LLM) -------

    async def user_turn_rows(
        self, project_hash: str, limit: int = 500
    ) -> list[dict]:
        """Recent ``user_turn`` (and ``compact_summary``) live events for a
        project, oldest-first, with raw payload for downstream stats.

        The Reflection view derives idle-gap and prompt-length
        distributions client-side from this stream. We deliberately
        include both event types here so the caller can choose to
        exclude synthesized turns (Claude Code /compact) when
        characterizing *user* behavior — a synthesized turn isn't
        the user typing, even though it's the same JSONL shape.
        """
        async with self.conn.execute(
            """SELECT id, session_id, event_type, payload, created_at
               FROM live_events
               WHERE project_hash=?
                 AND event_type IN ('user_turn','compact_summary')
               ORDER BY id DESC LIMIT ?""",
            (project_hash, limit),
        ) as cur:
            rows = await cur.fetchall()
        # Reverse to oldest-first so callers can compute deltas without
        # re-sorting.
        return [dict(r) for r in reversed(list(rows))]

    async def violation_status_counts(self, project_hash: str) -> dict:
        """Aggregate constraint-violation status counts for a project.

        Maps to the Reflection view's "destructive-action approval
        cadence" panel: how often did the user acknowledge versus
        dismiss versus leave new the violations tailward surfaced?
        """
        async with self.conn.execute(
            """SELECT status, count(*) AS n
               FROM constraint_violations
               WHERE project_hash=?
               GROUP BY status""",
            (project_hash,),
        ) as cur:
            rows = await cur.fetchall()
        out = {"new": 0, "acknowledged": 0, "dismissed": 0}
        for r in rows:
            out[str(r["status"])] = int(r["n"])
        return out

    async def claim_status_counts(self, project_hash: str) -> dict:
        """Aggregate claim verification verdicts for a project.

        Maps to the Reflection view's "verification behavior" panel:
        of the assistant's first-person completion claims, how many
        held up under grep-based verification?
        """
        async with self.conn.execute(
            """SELECT status, count(*) AS n
               FROM verification_ledger
               WHERE project_hash=?
               GROUP BY status""",
            (project_hash,),
        ) as cur:
            rows = await cur.fetchall()
        out = {"verified": 0, "contradicted": 0, "unverifiable": 0}
        for r in rows:
            out[str(r["status"])] = int(r["n"])
        return out

    async def search_events(
        self, project_hash: str, query: str, limit: int = 50
    ) -> list[dict]:
        """Project-scoped substring search across content-bearing events.

        Maps to the search field in the live header — surfaces a turn
        the user remembers but can't pinpoint. Searches the JSON
        payload of event types that carry user-visible content (user
        turns, assistant turns, tool calls, synthesized turns, claims,
        away-summary recaps), case-insensitively.

        Returns a list of {event_id, session_id, event_type, payload,
        created_at, snippet} dicts ordered most-recent first. Snippet
        is a ~120-char window around the first match in the payload
        for context preview. The frontend uses event_id + session_id
        for click-through; payload is included for callers that want
        the full event data.

        Substring search via LIKE — adequate for tailward's data
        volumes (live_events grows linearly with session activity,
        single-project scans stay small). FTS5 is the upgrade path
        if this becomes slow.
        """
        if not query.strip():
            return []
        like_pattern = "%" + query.replace("%", r"\%").replace("_", r"\_") + "%"
        async with self.conn.execute(
            """SELECT id, session_id, event_type, payload, created_at
               FROM live_events
               WHERE project_hash = ?
                 AND event_type IN (
                     'user_turn', 'turn', 'tool_call',
                     'compact_summary', 'claim', 'away_summary'
                 )
                 AND LOWER(payload) LIKE LOWER(?) ESCAPE '\\'
               ORDER BY id DESC
               LIMIT ?""",
            (project_hash, like_pattern, limit),
        ) as cur:
            rows = await cur.fetchall()

        results: list[dict] = []
        q_lower = query.lower()
        for r in rows:
            payload = r["payload"] or ""
            # Snippet: 80-char window centered on the first match.
            idx = payload.lower().find(q_lower)
            if idx < 0:
                snippet = payload[:120]
            else:
                start = max(0, idx - 40)
                end = min(len(payload), idx + len(query) + 80)
                snippet = payload[start:end]
                if start > 0:
                    snippet = "…" + snippet
                if end < len(payload):
                    snippet = snippet + "…"
            results.append({
                "event_id": int(r["id"]),
                "session_id": r["session_id"],
                "event_type": r["event_type"],
                "payload": r["payload"],
                "created_at": r["created_at"],
                "snippet": snippet,
            })
        return results

    async def tool_calls_by_mode(self, project_hash: str) -> dict:
        """Aggregate ``tool_call`` and ``tool_interrupted`` events
        grouped by tool name and active permission_mode at the time
        of the call.

        Maps to the Reflection view's "tool calls by permission mode"
        panel: surfaces which tools ran under which mode (default vs
        acceptEdits vs bypassPermissions vs plan) and how many were
        explicitly interrupted by the user.

        Counts are computed in Python after fetching matching rows —
        the live_events.payload JSON is opaque to SQLite without
        per-event json_extract calls, and the row volume per project
        is small enough that round-tripping in Python is cheaper than
        adding json_extract complexity. Returns separate buckets for
        emits vs interruptions so the UI can render them as
        complementary slices, not mixed.
        """
        import json as _json
        from collections import Counter

        async with self.conn.execute(
            """SELECT event_type, payload
               FROM live_events
               WHERE project_hash=?
                 AND event_type IN ('tool_call', 'tool_interrupted')""",
            (project_hash,),
        ) as cur:
            rows = await cur.fetchall()

        counts: Counter[tuple[str, str | None, str | None]] = Counter()
        for r in rows:
            raw_payload = r["payload"]
            try:
                payload = _json.loads(raw_payload) if raw_payload else {}
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            tool = payload.get("tool")
            mode = payload.get("permission_mode")
            counts[(r["event_type"], tool, mode)] += 1

        tool_calls: list[dict] = []
        interruptions: list[dict] = []
        total_calls = 0
        total_interruptions = 0
        for (event_type, tool, mode), n in sorted(
            counts.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2] or "")
        ):
            entry = {"tool": tool, "permission_mode": mode, "count": int(n)}
            if event_type == "tool_call":
                tool_calls.append(entry)
                total_calls += int(n)
            else:
                interruptions.append(entry)
                total_interruptions += int(n)

        return {
            "tool_calls": tool_calls,
            "interruptions": interruptions,
            "totals": {
                "tool_calls": total_calls,
                "interruptions": total_interruptions,
            },
        }

    async def stop_reason_counts(self, project_hash: str) -> dict:
        """Distribution of ``stop_reason`` across the project's
        assistant turn events.

        Mirrors the tool-calls-by-mode shape: pivot the JSON payload
        in Python (sqlite has no first-class JSON path indexing in
        the WAL build we ship). Skips empty placeholder rows that
        carry no ``message_id`` — those are historical artifacts
        from older tailward code that published empty turn shells; the
        current dispatcher doesn't produce them. Filtering at query
        time keeps the panel honest: every counted row represents an
        actual assistant message.

        Returns ``{"counts": [{"stop_reason": "...", "count": N}, ...],
        "total": N}`` with counts sorted descending.
        """
        import json as _json
        from collections import Counter

        async with self.conn.execute(
            """SELECT payload FROM live_events
               WHERE project_hash=? AND event_type='turn'""",
            (project_hash,),
        ) as cur:
            rows = await cur.fetchall()

        # Dedupe by message_id and pick the final stop_reason per
        # message — tailward's dispatcher publishes some messages as
        # multiple turn events (a partial emit for the visible text,
        # then a final emit once the full block stream lands). The
        # panel asks "how did messages END?", so we count once per
        # message using the final state. A message that only ever
        # emitted a partial (rare; usually means watcher tail caught
        # the JSONL mid-stream, or a user interrupt before the
        # message closed) lands as ``untagged`` — same convention
        # the tool-calls-by-mode panel uses for events missing
        # their permission_mode field.
        per_message: dict[str, str | None] = {}
        for r in rows:
            try:
                payload = _json.loads(r["payload"]) if r["payload"] else {}
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            mid = payload.get("message_id")
            if not mid:
                continue
            sr = payload.get("stop_reason")
            # Only overwrite if we don't yet have a stop_reason for
            # this message, or the new row carries one — never let a
            # later partial emit clobber a recorded final state.
            if mid not in per_message or per_message[mid] is None:
                per_message[mid] = sr or None

        counts: Counter[str] = Counter()
        for sr in per_message.values():
            counts[sr if sr else "untagged"] += 1

        ordered = [
            {"stop_reason": k, "count": int(n)}
            for k, n in counts.most_common()
        ]
        return {"counts": ordered, "total": int(sum(counts.values()))}

    async def memory_edit_count(self, project_hash: str) -> int:
        """Number of memory_edit events for the project.

        Surfaces in the Reflection view next to the violation cadence
        so the user sees calibration activity (memory file edits)
        alongside policy events without conflating the two.
        """
        async with self.conn.execute(
            """SELECT COUNT(*) AS n
               FROM live_events
               WHERE project_hash=? AND event_type='memory_edit'""",
            (project_hash,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    # ------- per-project seed flag -------

    async def is_project_seeded(self, project_hash: str) -> bool:
        """True if the user has opted this project into deep-parse on
        startup. Non-seeded projects are enumerated only (session rows
        written from filesystem inspection, content not parsed)."""
        async with self.conn.execute(
            "SELECT 1 FROM seeded_projects WHERE project_hash=?",
            (project_hash,),
        ) as cur:
            row = await cur.fetchone()
        return row is not None

    async def mark_project_seeded(self, project_hash: str) -> None:
        """Record that the user has opted this project into deep-parse.
        Idempotent — re-seeding is a no-op (preserves the original
        seeded_at timestamp)."""
        await self.conn.execute(
            """
            INSERT INTO seeded_projects(project_hash, seeded_at)
            VALUES(?, ?)
            ON CONFLICT(project_hash) DO NOTHING
            """,
            (project_hash, _now_iso()),
        )
        await self._commit()

    async def seeded_project_hashes(self) -> set[str]:
        """All currently-seeded project hashes. Returned as a set so
        the watcher's prime-pass loop can do an O(1) check per
        JSONL file without a per-file SQL roundtrip."""
        async with self.conn.execute(
            "SELECT project_hash FROM seeded_projects"
        ) as cur:
            rows = await cur.fetchall()
        return {row["project_hash"] for row in rows}
