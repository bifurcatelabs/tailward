"""Claim-verification pass.

Regex-detects strong-claim phrases in assistant output ("I removed", "all",
"fully", etc.), extracts candidate targets, and cross-checks against the
project's recent tool calls and on-disk files. Writes verified /
contradicted / unverifiable rows to the verification_ledger.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_config
from ..schema.events import TranscriptEvent

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)


CLAIM_PATTERNS = [
    re.compile(r"\bI\s+(removed|deleted|refactored|rewrote|fixed|added|renamed)\b[^.\n]{0,200}", re.I),
    re.compile(r"\b(all|every|fully|completely|entirely)\s+[a-z][^.\n]{0,120}", re.I),
    re.compile(r"\bno\s+more\s+[a-z][^.\n]{0,120}", re.I),
]

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass
class Claim:
    text: str
    candidates: list[str]


def extract_claims(text: str) -> list[Claim]:
    out: list[Claim] = []
    seen: set[str] = set()
    for pat in CLAIM_PATTERNS:
        for m in pat.finditer(text or ""):
            snippet = m.group(0).strip()
            if snippet in seen:
                continue
            seen.add(snippet)
            idents = [
                w for w in IDENT_RE.findall(snippet)
                if w.lower() not in _STOP and len(w) > 3
            ]
            out.append(Claim(text=snippet, candidates=idents[:6]))
    return out


_STOP = {
    "removed", "deleted", "refactored", "rewrote", "fixed", "added", "renamed",
    "all", "every", "fully", "completely", "entirely", "this", "that",
    "these", "those", "from", "into", "with", "over", "across", "file",
    "files", "code", "tests", "test", "more", "none", "them", "they", "have",
    "been", "done", "just", "there", "where", "which", "what", "when", "your",
    "like", "also", "only", "make", "made", "after", "before", "should",
    "would", "could",
}


class AuditWorker:
    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._q: asyncio.Queue[tuple[TranscriptEvent, object]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def enqueue(self, ev: TranscriptEvent, fs) -> None:
        await self._q.put((ev, fs))

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="audit-worker")

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
                log.exception("audit processing failed")

    async def _process(self, ev: TranscriptEvent, fs) -> None:
        if not ev.text or not fs.project_path or not fs.session_id:
            return
        claims = extract_claims(ev.text)
        if not claims:
            return

        project_root = Path(fs.project_path)
        cfg = get_config()
        budget = cfg.claim_grep_budget

        for claim in claims:
            status, evidence = await asyncio.to_thread(
                _verify_claim, claim, project_root, budget
            )
            await self._daemon.ledger.record_claim(
                fs.session_id, fs.project_hash, claim.text, status, evidence
            )
            if status == "contradicted" and self._daemon.surface is not None:
                try:
                    await self._daemon.surface.surface(
                        fs.session_id,
                        fs.project_hash,
                        kind="claim",
                        severity="high",
                        text=f"Contradicted claim: {claim.text}\n{evidence or ''}",
                    )
                except Exception:
                    log.exception("surfacing failed")


def _verify_claim(claim: Claim, project_root: Path, budget: int) -> tuple[str, str | None]:
    """Grep candidate identifiers against the repo.

    Simple heuristic rules:
    - "removed/deleted X" claim + X still appears in files -> contradicted
    - "added/renamed to X" + X not found -> contradicted
    - no candidates extractable -> unverifiable
    - otherwise -> verified (absence of evidence we can grep against)
    """
    if not claim.candidates:
        return "unverifiable", "no greppable identifiers"

    if not project_root.exists():
        return "unverifiable", f"project root missing: {project_root}"

    claim_lower = claim.text.lower()
    removal = any(w in claim_lower for w in ("removed", "deleted", "no more"))
    addition = any(w in claim_lower for w in ("added", "renamed"))

    hits = _grep_identifiers(project_root, claim.candidates, budget)
    found = {k for k, v in hits.items() if v}

    if removal and found:
        evidence = "; ".join(
            f"{k} still present in {path}:{line}" for k, (path, line) in _first_hits(hits).items()
        )
        return "contradicted", evidence
    if addition and not found:
        evidence = f"none of {claim.candidates} found in repo"
        return "contradicted", evidence
    return "verified", f"candidates found: {sorted(found) or 'n/a'}"


def _first_hits(hits: dict[str, list[tuple[str, int]]]) -> dict[str, tuple[str, int]]:
    out: dict[str, tuple[str, int]] = {}
    for k, v in hits.items():
        if v:
            out[k] = v[0]
    return out


_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".modmcp", "dist", "build"}


def _grep_identifiers(
    root: Path, idents: list[str], budget: int
) -> dict[str, list[tuple[str, int]]]:
    results: dict[str, list[tuple[str, int]]] = {i: [] for i in idents}
    scanned = 0
    patterns = {i: re.compile(r"\b" + re.escape(i) + r"\b") for i in idents}
    for path in root.rglob("*"):
        if scanned >= budget:
            break
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".bin"}:
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, start=1):
                    for ident, pat in patterns.items():
                        if pat.search(line):
                            results[ident].append((str(path), lineno))
        except OSError:
            continue
        scanned += 1
    return results
