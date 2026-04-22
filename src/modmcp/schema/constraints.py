"""Structured constraint policy parsed from the ``Active Rules`` section.

We extract three kinds of rules:

* :class:`PathPolicy` — allow/deny globs matched against
  :func:`modmcp.schema.events.target_paths`.
* :class:`ImmutableFiles` — paths the agent must not edit (a convenience
  alias around PathPolicy ``deny``).
* :class:`ForbiddenBashPatterns` — regex patterns matched against
  :func:`modmcp.schema.events.bash_command`.

Parsing is heuristic: we look for hints in the rule text and fall back to
treating the whole line as a tag-free note. Ambiguous rules do not raise;
they just don't produce enforceable policy.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field


@dataclass
class PathPolicy:
    """Glob rules against :func:`target_paths` of each tool call."""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

    def violation_for(self, path: str) -> str | None:
        """Return the deny pattern that matches ``path``, or None."""
        norm = path.replace("\\", "/")
        for pat in self.deny:
            if _match_glob(pat, norm):
                return pat
        if self.allow:
            allowed = any(_match_glob(pat, norm) for pat in self.allow)
            if not allowed:
                return "(not in allow-list)"
        return None


@dataclass
class ImmutableFiles:
    """Paths the agent must not modify."""

    paths: list[str] = field(default_factory=list)

    def violation_for(self, path: str) -> str | None:
        norm = path.replace("\\", "/")
        for p in self.paths:
            if _match_glob(p, norm):
                return p
        return None


@dataclass
class ForbiddenBashPatterns:
    """Regex patterns applied to Bash tool commands."""

    patterns: list[str] = field(default_factory=list)

    _compiled: list[re.Pattern[str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = []
        for p in self.patterns:
            try:
                self._compiled.append(re.compile(p, re.IGNORECASE))
            except re.error:
                continue

    def violation_for(self, command: str) -> str | None:
        for raw, pat in zip(self.patterns, self._compiled):
            if pat.search(command or ""):
                return raw
        return None


@dataclass
class CompiledPolicy:
    path: PathPolicy = field(default_factory=PathPolicy)
    immutable: ImmutableFiles = field(default_factory=ImmutableFiles)
    bash: ForbiddenBashPatterns = field(default_factory=ForbiddenBashPatterns)
    rule_texts: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (
            self.path.allow
            or self.path.deny
            or self.immutable.paths
            or self.bash.patterns
        )


def _match_glob(pattern: str, path: str) -> bool:
    """Fnmatch with a small extension: ``**`` as any-depth wildcard."""
    if "**" in pattern:
        regex = fnmatch.translate(pattern.replace("**", "__DSTAR__"))
        regex = regex.replace("__DSTAR__", ".*")
        return re.match(regex, path) is not None
    return fnmatch.fnmatch(path, pattern)


# ---------------------------------------------------------------------------
# Heuristic parser (used when no LLM is available / as a fallback).
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$", re.MULTILINE)
_PATH_TOKEN = re.compile(
    r"[A-Za-z0-9_./\-*]+/[A-Za-z0-9_./\-*]*"  # src/, src/foo, src/foo/bar
    r"|[A-Za-z0-9_.\-*]+\.\w{1,6}"            # pyproject.toml, ci.yml
)
_BASH_HINT_RE = re.compile(
    r"(?:never|do\s*not|don'?t|forbidden|no\b)[^\n]*?(?:run|execute|invoke)\s+"
    r"(?P<cmd>[a-z0-9_./-]+[^\n]*?)(?:[,;]|$)",
    re.IGNORECASE,
)
_IMMUTABLE_HINT_RE = re.compile(
    r"(?:do\s*not|don'?t|never|avoid|forbidden\s+to)\s+(?:edit|modify|touch|change|write\s+to|overwrite)\s+"
    r"(?P<rest>[^\n,;]+)",
    re.IGNORECASE,
)
_ALLOW_HINT_RE = re.compile(
    r"(?:only|strictly)\s+(?:edit|modify|change|write)\s+(?:files?\s+)?(?:in|under|within)\s+(?P<rest>[^\n,;]+)",
    re.IGNORECASE,
)
_DENY_HINT_RE = re.compile(
    r"(?:no|never|do\s*not|don'?t)\s+(?:edit|modify|write|touch)\s+(?:files?\s+)?(?:in|under|within)\s+(?P<rest>[^\n,;]+)",
    re.IGNORECASE,
)


def parse_active_rules(body: str) -> CompiledPolicy:
    """Best-effort parse of the ``Active Rules`` section body into a policy.

    Each bullet becomes a rule; its text is stored verbatim under a
    generated ``rule_id`` so violations can cite the original wording.
    """
    policy = CompiledPolicy()
    bash_patterns: list[str] = []

    if not body:
        return policy

    bullets = [m.group("body").strip() for m in _BULLET_RE.finditer(body)]
    if not bullets:
        bullets = [ln.strip() for ln in body.splitlines() if ln.strip()]

    for idx, line in enumerate(bullets):
        rule_id = f"rule-{idx + 1:02d}"
        policy.rule_texts[rule_id] = line
        _apply_heuristics(line, policy, bash_patterns)

    if bash_patterns:
        policy.bash = ForbiddenBashPatterns(bash_patterns)

    return policy


def _apply_heuristics(
    line: str, policy: CompiledPolicy, bash_patterns: list[str]
) -> None:
    low = line.lower()

    m = _IMMUTABLE_HINT_RE.search(line)
    if m:
        for tok in _extract_path_tokens(m.group("rest")):
            policy.immutable.paths.append(tok)

    m = _ALLOW_HINT_RE.search(line)
    if m:
        for tok in _extract_path_tokens(m.group("rest")):
            pat = tok if "*" in tok else tok.rstrip("/") + "/**"
            policy.path.allow.append(pat)

    m = _DENY_HINT_RE.search(line)
    if m:
        for tok in _extract_path_tokens(m.group("rest")):
            pat = tok if "*" in tok else tok.rstrip("/") + "/**"
            policy.path.deny.append(pat)

    for m in _BASH_HINT_RE.finditer(line):
        cmd = m.group("cmd").strip()
        if cmd:
            bash_patterns.append(re.escape(cmd.split(" ", 1)[0]))

    if "no in-repo writes" in low or "read-only" in low:
        policy.path.deny.append("**")

    if "skip tests" in low or "disable tests" in low or "no-cov" in low:
        bash_patterns.append(r"pytest.*--no-cov")
        bash_patterns.append(r"pytest.*-k\s+['\"]?not\b")

    if "force push" in low or "git push --force" in low:
        bash_patterns.append(r"git\s+push\s+(?:--force|-f\b)")

    if "rm -rf" in low:
        bash_patterns.append(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\b")


def _extract_path_tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in _PATH_TOKEN.finditer(text):
        tok = m.group(0).strip().strip(",;:")
        if tok and tok.lower() not in {"and", "or", "the"}:
            out.append(tok)
    return out


def default_policy() -> CompiledPolicy:
    """Baseline guardrails that apply even with an empty Active Rules section."""
    policy = CompiledPolicy()
    policy.bash = ForbiddenBashPatterns([
        r"git\s+push\s+(?:--force|-f\b)",
        r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/",
        r":\(\)\{\s*:\|:&",
    ])
    policy.rule_texts["baseline-01"] = "Baseline: no force-push or rm -rf /"
    return policy


def merge(base: CompiledPolicy, overlay: CompiledPolicy) -> CompiledPolicy:
    merged = CompiledPolicy()
    merged.path = PathPolicy(
        allow=list(dict.fromkeys(base.path.allow + overlay.path.allow)),
        deny=list(dict.fromkeys(base.path.deny + overlay.path.deny)),
    )
    merged.immutable = ImmutableFiles(
        paths=list(dict.fromkeys(base.immutable.paths + overlay.immutable.paths))
    )
    merged.bash = ForbiddenBashPatterns(
        list(dict.fromkeys(base.bash.patterns + overlay.bash.patterns))
    )
    merged.rule_texts = {**base.rule_texts, **overlay.rule_texts}
    return merged
