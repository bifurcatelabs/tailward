"""User-side rubric — local LLM self-rubric for the user's own turns.

The assistant-side :class:`~tailward.daemon.rubric_worker.RubricWorker`
scores the agent's behavior on coding-trustworthiness dimensions.
This worker is its mirror for the user: how well is the human side
of the collaboration showing up?

Triggers (first cut, conservative — fewer LLM calls than the assistant
rubric):
  * Every ``user_rubric_turn_interval`` typed user turns. Synthesized
    turns (Claude Code ``/compact``, ``isCompactSummary=true``) are
    excluded — they aren't the user.

Each run asks the local LLM for a structured JSON score across four user-side
dimensions (intent_clarity, context_coverage, verification_engagement,
mode_coherence). Per-dimension rows write to ``rubric_scores`` with
``subject='user'`` and individually publish a ``rubric_sample`` live
event with ``subject='user'`` so the Reflection view's self-rubric
panel and the Session feed both surface them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import get_config
from ..schema.events import TranscriptEvent

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)


DIMENSIONS_USER: list[tuple[str, str]] = [
    (
        "intent_clarity",
        "Does the prompt state goal, scope, and constraints clearly enough "
        "for the agent to act without guessing? Vague asks score low; "
        "specific objectives + acceptance criteria score high.",
    ),
    (
        "context_coverage",
        "Does the prompt provide the file paths, prior decisions, and "
        "constraints the agent needs? Asks that force the agent to "
        "rediscover context score low; well-scoped briefings score high.",
    ),
    (
        "verification_engagement",
        "Does the user's behavior across recent turns show checking the "
        "agent's claims, asking for evidence, or pushing back on "
        "unverified assertions? Passive acceptance scores low; active "
        "verification scores high.",
    ),
    (
        "mode_coherence",
        "Does the user's behavior match their stated session_mode? "
        "Saying 'exploration' but expecting prod-ready code is incoherent; "
        "matching expectations to mode is coherent.",
    ),
]


def _build_system_prompt(active_dims: tuple[str, ...]) -> str:
    """Build the user-rubric system prompt for a specific dim subset.

    Mirrors the assistant rubric's mode-aware path so future profiles
    can score only the user-side dimensions that apply to this mode.
    """
    return (
        "You are a judge evaluating the USER (the human collaborator) "
        f"across {len(active_dims)} dimension(s) of how well they're "
        "engaging with an AI coding agent. You are NOT scoring the "
        "agent. Return JSON ONLY:\n"
        "{\n"
        + ",\n".join(
            f'  "{name}": {{"score": <0-5 integer>, "evidence": "<short quote or fact from a USER turn>", "suggestion": "<<=25 words, addressed to the user>"}}'
            for name in active_dims
        )
        + "\n}\n"
        "Scoring:\n"
        "  0 — absent or actively counterproductive\n"
        "  3 — average; present but unremarkable\n"
        "  5 — explicit, evidenced, and consistent\n"
        "Default to 3 when uncertain. Do not reward verbosity for its "
        "own sake — a one-line prompt that nails goal + scope can "
        "score 5 on intent_clarity. Evidence must come from a USER "
        "turn in the window, not the agent's response.\n"
    )


PROMPT_SYSTEM_USER: str = _build_system_prompt(
    tuple(name for name, _ in DIMENSIONS_USER)
)

PROMPT_USER_TEMPLATE_USER: str = (
    "Dimensions:\n{dim_hints}\n\n"
    "Recent user turns (oldest first; the most recent is what "
    "triggered this evaluation):\n{user_text}\n\n"
    "Stated session_mode: {session_mode}\n"
)


@dataclass
class _SessionUserRubric:
    last_run_count: int = 0
    in_flight: bool = False


class UserRubricWorker:
    """Mirror of RubricWorker, scoring user_message events instead.

    Architecture parallels :class:`RubricWorker` deliberately —
    separate queue, separate lock, separate per-session state — so
    the two rubrics never accidentally cross-contaminate triggers
    or LLM call accounting.
    """

    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._q: asyncio.Queue[tuple[TranscriptEvent, object]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._by_session: dict[str, _SessionUserRubric] = {}
        self._user_text_by_session: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, ev: TranscriptEvent, fs) -> None:
        await self._q.put((ev, fs))

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="user-rubric-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ev, fs = await asyncio.wait_for(self._q.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                await self._process(ev, fs)
            except Exception:
                log.exception("user-rubric processing failed")

    async def _process(self, ev: TranscriptEvent, fs) -> None:
        # Only score typed user turns. Synthesized /compact turns
        # aren't user behavior, even though the JSONL shape matches.
        if ev.kind != "user_message" or ev.synthesized:
            return
        if not fs.session_id or not fs.project_hash:
            return
        cfg = get_config()
        text = (ev.text or "").strip()
        if len(text) < cfg.user_rubric_min_text_chars:
            return

        self._user_text_by_session.setdefault(fs.session_id, []).append(text)
        # Keep last 6 turns as context — wider window than assistant
        # rubric because user-side scoring depends on multi-turn
        # patterns (verification engagement especially).
        window = self._user_text_by_session[fs.session_id][-6:]

        state = self._by_session.setdefault(fs.session_id, _SessionUserRubric())
        # turn_count = number of user turns observed so far in this session.
        turn_count = len(self._user_text_by_session[fs.session_id])

        if (
            turn_count > 0
            and turn_count != state.last_run_count
            and turn_count % cfg.user_rubric_turn_interval == 0
        ):
            await self._run_user_rubric(fs, turn_count, window)

    async def _run_user_rubric(
        self, fs, user_turn_count: int, window: list[str]
    ) -> None:
        if self._daemon.local_llm is None:
            return
        async with self._lock:
            state = self._by_session.setdefault(
                fs.session_id, _SessionUserRubric()
            )
            if state.in_flight:
                return
            state.in_flight = True
            state.last_run_count = user_turn_count

        live = getattr(self._daemon, "live", None)
        if live is not None:
            try:
                await live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "rubric_in_flight",
                    {
                        "turn_idx": user_turn_count,
                        "triggers": ["user_cadence"],
                        "subject": "user",
                    },
                )
            except Exception:
                log.exception("live publish failed (user rubric_in_flight)")

        try:
            payload = await self._call_llm(window, fs.project_path, getattr(fs, "box", ""))
            await self._record(fs, user_turn_count, payload)
            if live is not None:
                await live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "rubric_done",
                    {
                        "turn_idx": user_turn_count,
                        "triggers": ["user_cadence"],
                        "subject": "user",
                    },
                )
        except Exception as e:
            log.warning("user rubric run failed: %s", e)
            if live is not None:
                try:
                    await live.publish(
                        fs.session_id,
                        fs.project_hash,
                        "rubric_done",
                        {
                            "turn_idx": user_turn_count,
                            "triggers": ["user_cadence"],
                            "subject": "user",
                            "error": str(e),
                        },
                    )
                except Exception:
                    pass
        finally:
            async with self._lock:
                state.in_flight = False

    async def _call_llm(
        self, window: list[str], project_path: str | None, box: str = ""
    ) -> dict:
        from .mode_profile import session_mode_for_project
        mode_label = session_mode_for_project(project_path, box=box) or "unspecified"
        active = tuple(name for name, _ in DIMENSIONS_USER)
        dim_hints = "\n".join(
            f"- {name}: {desc}" for name, desc in DIMENSIONS_USER
        )
        recent = "\n\n---\n\n".join(window[-6:]) if window else ""
        user = PROMPT_USER_TEMPLATE_USER.format(
            dim_hints=dim_hints,
            user_text=recent[:6000],
            session_mode=mode_label,
        )
        system = _build_system_prompt(active)
        # Reuse the rubric call kind so cost / sampler / max_tokens
        # accounting is unified — same call shape, different prompt.
        return await self._daemon.local_llm.complete_json(system, user, kind="rubric")

    async def _record(self, fs, user_turn_count: int, payload: dict) -> None:
        from .mode_profile import session_mode_for_project
        model_used = (
            self._daemon.local_llm.resolve_model("rubric")
            if self._daemon.local_llm else None
        )
        live = getattr(self._daemon, "live", None)
        mode_label = session_mode_for_project(fs.project_path, box=getattr(fs, "box", ""))

        for name, _desc in DIMENSIONS_USER:
            dim = payload.get(name) or {}
            score_raw = dim.get("score")
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                score = 3.0
            score = max(0.0, min(5.0, score))
            evidence = (dim.get("evidence") or "").strip() or None
            suggestion = (dim.get("suggestion") or "").strip() or None

            sid = await self._daemon.ledger.record_rubric_score(
                fs.session_id,
                fs.project_hash,
                turn_idx=user_turn_count,
                dim_name=name,
                score=score,
                evidence=evidence,
                suggestion=suggestion,
                model_used=model_used,
                trigger="user_cadence",
                session_mode=mode_label,
                subject="user",
            )

            if live is not None:
                try:
                    await live.publish(
                        fs.session_id,
                        fs.project_hash,
                        "rubric_sample",
                        {
                            "id": sid,
                            "turn_idx": user_turn_count,
                            "dim": name,
                            "score": score,
                            "evidence": evidence,
                            "suggestion": suggestion,
                            "trigger": "user_cadence",
                            "subject": "user",
                        },
                    )
                except Exception:
                    log.exception("live publish failed (user rubric_sample)")
