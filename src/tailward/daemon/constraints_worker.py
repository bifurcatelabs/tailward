"""Rule-based constraint audit.

Tails ``tool_use`` events, resolves the current session's
:class:`~tailward.schema.constraints.CompiledPolicy` (recompiled whenever
``Active Rules`` changes), and writes each violation to the ledger
``constraint_violations`` table. Every violation is also pushed onto the
:class:`~tailward.daemon.livebus.LiveBus` so the web UI sees it within a
watcher tick.

Covers failure modes 1 (constraint-respecting), 5 (fails-loudly via
forbidden bash), and 10 (doesn't-game-targets via immutable files like CI
configs).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..paths import intent_path
from ..schema.constraints import (
    CompiledPolicy,
    default_policy,
    is_memory_edit_path,
    merge,
    parse_active_rules,
)
from ..schema.events import TranscriptEvent, bash_command, target_paths
from ..schema.intent import load_intent

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)


@dataclass
class _PolicyCache:
    policy: CompiledPolicy
    rules_digest: str


class ConstraintsWorker:
    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._q: asyncio.Queue[tuple[TranscriptEvent, object, bool]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._policies: dict[str, _PolicyCache] = {}
        self._seen_violations: dict[str, set[tuple[str, str]]] = {}

    async def enqueue(self, ev: TranscriptEvent, fs, *, is_backlog: bool = False) -> None:
        # ``is_backlog`` rides through the queue so the publish step
        # can suppress live broadcast for backlog-derived violations
        # (the violation still lands in the ledger; it just doesn't
        # fan out to the live feed as if it just happened).
        await self._q.put((ev, fs, is_backlog))

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="constraints-worker")

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
                ev, fs, is_backlog = await asyncio.wait_for(self._q.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                await self._process(ev, fs, is_backlog=is_backlog)
            except Exception:
                log.exception("constraints processing failed")

    def _compute_policy(self, project_path: str) -> CompiledPolicy:
        try:
            intent = load_intent(intent_path(project_path))
        except Exception:
            return default_policy()
        rules_body = intent.sections.get("Active Rules", "") or ""
        digest = hashlib.sha1(rules_body.encode("utf-8")).hexdigest()
        cached = self._policies.get(project_path)
        if cached and cached.rules_digest == digest:
            return cached.policy
        policy = merge(default_policy(), parse_active_rules(rules_body))
        self._policies[project_path] = _PolicyCache(policy, digest)
        return policy

    async def _process(self, ev: TranscriptEvent, fs, *, is_backlog: bool = False) -> None:
        if not fs.project_path or not fs.session_id:
            return
        # Persist findings with the original event timestamp; suppress
        # broadcast for backlog so historical replay doesn't appear in
        # the live feed.
        _ts_epoch: float | None = (
            ev.timestamp.timestamp() if ev.timestamp else None
        )
        _pub_kwargs: dict[str, object] = {
            "broadcast": not is_backlog,
            "ts": _ts_epoch,
        }
        # Accept bare ``tool_use`` events and assistant messages that wrap
        # a tool_use content block. Real Claude Code transcripts only
        # ever emit the embedded shape; the bare shape exists in tests
        # and is kept working for defense-in-depth.
        if not ev.tool_name:
            return
        policy = self._compute_policy(fs.project_path)
        if policy.is_empty():
            return

        violations: list[tuple[str, str, str, str]] = []
        memory_edits: list[str] = []

        for path in target_paths(ev):
            pat = policy.immutable.violation_for(path)
            if pat:
                violations.append((
                    "immutable",
                    pat,
                    f"{ev.tool_name} touched immutable path {path}",
                    self._lookup_rule(policy, "immutable", pat),
                ))
            pat = policy.path.violation_for(path)
            if pat:
                # Memory-file edits land outside the watched project
                # root by design (they live under ~/.claude/projects/
                # <ph>/memory/**). Surface as a memory_edit signal
                # instead of a constraint_violation so the dashboard
                # reflects the activity without conflating it with
                # policy events.
                if is_memory_edit_path(path):
                    memory_edits.append(path)
                    continue
                violations.append((
                    "path-policy",
                    pat,
                    f"{ev.tool_name} targeted disallowed path {path} (matched {pat})",
                    self._lookup_rule(policy, "path", pat),
                ))

        cmd = bash_command(ev)
        if cmd:
            pat = policy.bash.violation_for(cmd)
            if pat:
                violations.append((
                    "forbidden-bash",
                    pat,
                    f"Bash command matched forbidden pattern: {cmd[:200]}",
                    self._lookup_rule(policy, "bash", pat),
                ))

        if memory_edits and getattr(self._daemon, "live", None) is not None:
            for path in memory_edits:
                try:
                    await self._daemon.live.publish(
                        fs.session_id,
                        fs.project_hash,
                        "memory_edit",
                        {
                            "tool": ev.tool_name,
                            "path": path,
                        },
                        **_pub_kwargs,
                    )
                except Exception:
                    log.exception("live publish failed (memory_edit)")

        if not violations:
            return

        seen = self._seen_violations.setdefault(fs.session_id, set())

        for kind, pat, evidence, rule_text in violations:
            key = (kind, pat)
            if key in seen:
                continue
            seen.add(key)
            rule_id = f"{kind}:{_short_hash(pat)}"
            severity = "high" if kind in ("immutable", "forbidden-bash") else "med"
            vid = await self._daemon.ledger.record_constraint_violation(
                fs.session_id,
                fs.project_hash,
                tool_call_id=str(ev.raw.get("id") or ""),
                rule_id=rule_id,
                rule_text=rule_text,
                evidence=evidence,
                severity=severity,
            )
            log.info(
                "constraint violation session=%s rule=%s path_or_cmd=%s",
                fs.session_id,
                rule_id,
                pat,
            )
            if getattr(self._daemon, "live", None) is not None:
                try:
                    await self._daemon.live.publish(
                        fs.session_id,
                        fs.project_hash,
                        "constraint_violation",
                        {
                            "id": vid,
                            "rule_id": rule_id,
                            "rule_text": rule_text,
                            "evidence": evidence,
                            "severity": severity,
                            "tool": ev.tool_name,
                        },
                        **_pub_kwargs,
                    )
                except Exception:
                    log.exception("live publish failed (constraint)")
            if self._daemon.surface is not None:
                try:
                    await self._daemon.surface.surface(
                        fs.session_id,
                        fs.project_hash,
                        kind="constraint",
                        severity=severity,
                        text=f"{rule_text}\n{evidence}",
                    )
                except Exception:
                    log.exception("constraint surfacing failed")

    def _lookup_rule(self, policy: CompiledPolicy, kind: str, pat: str) -> str:
        # 1. Exact-key lookup: default_policy() stores rule_texts keyed by the
        #    literal pattern/path, so baseline violations render their own
        #    human-readable description instead of a raw regex.
        direct = policy.rule_texts.get(pat)
        if direct:
            return direct
        # 2. Substring fallback: parse_active_rules() keeps each bullet under a
        #    "rule-NN" id; we pick the first rule whose wording mentions the
        #    triggering token.
        for _rid, text in policy.rule_texts.items():
            if pat and pat.lower() in text.lower():
                return text
        if kind == "immutable":
            return f"Immutable path: {pat}"
        if kind == "path":
            return f"Path policy: {pat}"
        if kind == "bash":
            return f"Forbidden bash pattern: {pat}"
        return pat


def _short_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]
