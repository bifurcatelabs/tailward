"""Drift detection.

Pattern-first, LLM-escalated. Heuristic scores keyword overlap between the
last assistant turn and the captured-intent active_goal + open_threads. If
the score falls below a threshold we ask the local LLM to classify severity and
produce a verdict; high-severity verdicts surface to the user via the
live UI and OS toast (when enabled).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_config
from ..paths import intent_path
from ..schema.events import TranscriptEvent
from ..schema.intent import Intent, load_intent

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


# Single source of truth for the drift verdict prompt; surfaced
# verbatim in /llm-profiles. The system text is static; runtime data
# lands via PROMPT_USER_TEMPLATE.format(...).
PROMPT_SYSTEM: str = (
    "You score whether ONE assistant turn is on-track for the active goal. "
    "DEFAULT answer is \"low\" (on-track). Most turns are low. "
    "Escalate ONLY with clear evidence.\n"
    "\n"
    "low  — turn advances the goal OR an open thread; asks a clarifying "
    "       question about the goal; verifies a claim; explains a design "
    "       decision relevant to the goal; refuses an off-goal request; "
    "       runs a tool that supports the goal. Cautious / slow / "
    "       analytical turns are LOW, not higher.\n"
    "med  — turn does useful work but on something clearly outside all "
    "       open threads (tangential refactor, unrelated topic).\n"
    "high — turn makes a false completion claim, contradicts an Active "
    "       Rule, or pivots entirely to an unrelated goal without "
    "       explicit scope-change from the user.\n"
    "\n"
    "Return JSON only: "
    "{\"severity\":\"low|med|high\",\"detail\":\"one short sentence naming "
    "the specific evidence\",\"corrective\":\"<=25 words or empty\"}. "
    "If severity is low, corrective MUST be empty."
)
PROMPT_USER_TEMPLATE: str = (
    "Active Goal:\n{goal}\n\n"
    "Open Threads:\n{threads}\n\n"
    "Active Rules:\n{active_rules}\n\n"
    "Session mode: {session_mode}\n\n"
    "Assistant turn:\n{assistant_text}"
)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text or "")}


_MODE_HINTS = {
    "meta": ("meta", "talk through", "think about", "brainstorm", "design"),
    "build": ("implement", "fix", "write", "refactor", "add", "remove", "ship"),
    "exploration": ("explore", "investigate", "map out", "understand"),
}


def _mode_mismatch(intent_mode: str, assistant_text: str) -> bool:
    text = assistant_text.lower()
    hints = _MODE_HINTS.get(intent_mode, ())
    if not hints:
        return False
    hit = any(h in text for h in hints)
    # If the text strongly signals a different mode, consider it mismatch.
    other_hit = False
    for m, words in _MODE_HINTS.items():
        if m == intent_mode:
            continue
        if any(w in text for w in words):
            other_hit = True
            break
    return (not hit) and other_hit


@dataclass
class DriftVerdict:
    severity: str  # low | med | high
    score: float
    detail: str
    corrective: str | None


class DriftWorker:
    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._q: asyncio.Queue[tuple[TranscriptEvent, object]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def enqueue(self, ev: TranscriptEvent, fs) -> None:
        await self._q.put((ev, fs))

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="drift-worker")

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
                log.exception("drift processing failed")

    async def _process(self, ev: TranscriptEvent, fs) -> None:
        cfg = get_config()
        if not fs.project_path or not fs.session_id:
            return
        ip: Path = intent_path(fs.project_path, box=getattr(fs, "box", ""))
        if not ip.exists():
            return
        try:
            intent = load_intent(ip)
        except Exception:
            return

        # Skip turns with no meaningful prose (pure tool-call events, empty
        # text, or trivially short responses). Claude often emits several
        # assistant events per response (one per tool invocation + one for
        # prose); analyzing each would amplify noise.
        text = (ev.text or "").strip()
        if len(text) < 40:
            return

        verdict = await asyncio.wait_for(
            self._analyze(ev, intent), timeout=cfg.per_turn_hard_cap_seconds
        )

        action_taken = None
        if verdict.severity == "high":
            if self._daemon.surface is not None:
                try:
                    await self._daemon.surface.surface(
                        fs.session_id,
                        fs.project_hash,
                        kind="drift",
                        severity="high",
                        text=verdict.detail,
                    )
                except Exception:
                    log.exception("surfacing failed")
            action_taken = "surfaced"

        await self._daemon.ledger.record_drift(
            fs.session_id, fs.project_hash, "drift", verdict.severity, action_taken, verdict.detail
        )

        if getattr(self._daemon, "live", None) is not None:
            try:
                await self._daemon.live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "drift",
                    {
                        "severity": verdict.severity,
                        "detail": verdict.detail,
                        "corrective": verdict.corrective,
                        "pattern_score": round(verdict.score, 3),
                        "action_taken": action_taken,
                    },
                )
            except Exception:
                log.exception("live publish failed (drift)")


    async def _analyze(self, ev: TranscriptEvent, intent: Intent) -> DriftVerdict:
        goal = intent.sections.get("Active Goal", "")
        threads = intent.sections.get("Open Threads", "")
        goal_tokens = _tokens(goal) | _tokens(threads)
        turn_tokens = _tokens(ev.text or "")

        if not goal_tokens:
            return DriftVerdict("low", 1.0, "no active goal set", None)

        overlap = len(goal_tokens & turn_tokens) / max(1, len(goal_tokens))
        mismatch = _mode_mismatch(intent.front.session_mode, ev.text or "")

        cfg = get_config()
        pattern_score = overlap - (0.3 if mismatch else 0.0)

        if pattern_score >= cfg.drift_threshold and not mismatch:
            return DriftVerdict("low", pattern_score, "on-goal per heuristic", None)

        # Escalate to LLM.
        if self._daemon.local_llm is None:
            severity = "med" if pattern_score < 0.1 or mismatch else "low"
            corrective = (
                "Recent turn looked off-goal by heuristic; restate the Active Goal and stay in the named scope."
                if severity == "med"
                else None
            )
            return DriftVerdict(severity, pattern_score, "heuristic-only", corrective)

        user = PROMPT_USER_TEMPLATE.format(
            goal=goal,
            threads=threads,
            active_rules=intent.sections.get("Active Rules", ""),
            session_mode=intent.front.session_mode,
            assistant_text=ev.text[:4000],
        )
        try:
            payload = await self._daemon.local_llm.complete_json(
                PROMPT_SYSTEM, user, kind="drift"
            )
            severity = payload.get("severity", "low")
            if severity not in ("low", "med", "high"):
                severity = "low"
            detail = payload.get("detail", "")
            corrective = payload.get("corrective") or None
            return DriftVerdict(severity, pattern_score, detail, corrective)
        except Exception as e:
            log.warning("drift LLM call failed: %s", e)
            return DriftVerdict("low", pattern_score, f"llm-fail: {e}", None)
