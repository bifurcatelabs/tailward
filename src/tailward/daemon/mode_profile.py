"""Per-session-mode behavior profile.

The ``session_mode`` field in ``intent.md`` is a free-form string the
user owns ("build", "exploration", "meta", "yolo", "shower thoughts",
whatever). This module ships *opinionated profiles* for a few well-
known labels and a permissive **default** for everything else.

The original failure-modes taxonomy is software-engineering-coded:
it assumes defined tasks, verifiable outcomes, minimal-change-as-
virtue. Applying that lens to an exploration session would treat
"touched 30 files chasing an idea" as scope creep — which is a
measurement category error, not the user's fault. ModeProfile is the
hook that lets the audit honor user intent without prescribing a
fixed set of labels.

Two non-negotiables baked into the design:

1. **Unknown labels are not punished.** "yolo" / "shower thoughts" /
   anything custom resolves to the permissive ``DEFAULT_PROFILE``.
   The act of self-labeling is itself the trust signal — tailward
   capturing it on every persisted row is the value.
2. **Default is permissive, not strict.** When ``session_mode`` is
   unset, the audit lands on the lightest-touch profile. To opt
   *into* stricter surfacing the user labels their session "build".

See ``project_emerging_failure_modes.md`` and
``project_session_arc_visualization.md`` in memory for the framing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..paths import intent_path
from ..schema.intent import load_intent

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# Canonical rubric dimension names — kept in lockstep with the
# ``DIMENSIONS`` list in ``rubric_worker.py``. Centralized here so
# profile authors can pick from a known set without importing the
# worker.
ALL_RUBRIC_DIMENSIONS: tuple[str, ...] = (
    "invariants_awareness",
    "uncertainty_honesty",
    "maintainability",
    "provenance",
)


@dataclass(frozen=True)
class ModeProfile:
    """Per-mode worker tuning. Frozen so workers can cache by name.

    ``scope_creep_floor=None`` means scope_creep events are disabled
    entirely for this mode — the snapshot still records files
    touched and diff bytes, but no "creep" event ever fires. This is
    the right shape for exploration where scope expansion is the
    work, not the failure.

    ``rubric_dimensions=()`` means the rubric worker doesn't even
    issue an LLM call for this mode. Saves tokens on sessions where
    the existing dimensions are category errors.

    ``scope_event_label`` is cosmetic (UI copy), keeping prescriptive
    SWE language out of non-build modes.
    """

    name: str
    description: str
    scope_creep_floor: int | None
    scope_creep_factor: float
    rubric_dimensions: tuple[str, ...]
    scope_event_label: str = "spread"


# Built-in profiles. The default here is *permissive* on purpose:
# tailward non-punishing by default, opt-in to stricter surfacing via
# explicit labeling. See the module docstring.

_BUILD = ModeProfile(
    name="build",
    description=(
        "SWE-shaped task: defined scope, verifiable outcomes, "
        "minimal-change-as-virtue. All audit signals fully active."
    ),
    scope_creep_floor=12,
    scope_creep_factor=2.0,
    rubric_dimensions=ALL_RUBRIC_DIMENSIONS,
    scope_event_label="creep",
)

_EXPLORATION = ModeProfile(
    name="exploration",
    description=(
        "Open-ended exploration. Scope expansion is the work, not "
        "the failure — scope_creep events disabled. Rubric drops "
        "the dimensions whose framing assumes a defined target."
    ),
    scope_creep_floor=None,  # disabled
    scope_creep_factor=0.0,
    rubric_dimensions=("invariants_awareness", "uncertainty_honesty"),
    scope_event_label="spread",
)

_META = ModeProfile(
    name="meta",
    description=(
        "Brainstorming, planning, design discussion. Long prose, "
        "few file edits. Rubric keeps the reasoning-trail dimensions "
        "but drops the ones that assume code is being written."
    ),
    scope_creep_floor=30,
    scope_creep_factor=3.0,
    rubric_dimensions=("uncertainty_honesty", "provenance"),
    scope_event_label="creep",
)

# Permissive fallback for unknown labels (yolo, shower thoughts, etc).
# The act of self-labeling is captured (the label is persisted on every
# row); the audit just doesn't enforce SWE thresholds against it.
DEFAULT_PROFILE = ModeProfile(
    name="default",
    description=(
        "Permissive fallback for unknown / unset session_mode. "
        "Captures the user's self-label without grading against it."
    ),
    scope_creep_floor=None,
    scope_creep_factor=0.0,
    rubric_dimensions=("uncertainty_honesty",),
    scope_event_label="spread",
)


_REGISTRY: dict[str, ModeProfile] = {
    "build": _BUILD,
    "exploration": _EXPLORATION,
    "meta": _META,
}


def profile_for(label: str | None) -> ModeProfile:
    """Resolve a free-form ``session_mode`` label to its profile.

    Unknown labels — including everything from ``"yolo"`` through
    ``"shower thoughts"`` to typos — fall through to
    ``DEFAULT_PROFILE``. Empty / None likewise. Lookup is
    case-insensitive on the registry keys but the returned profile
    keeps its canonical name.
    """
    if not label:
        return DEFAULT_PROFILE
    return _REGISTRY.get(label.strip().lower(), DEFAULT_PROFILE)


def known_profile_names() -> list[str]:
    """For UI consumption: the set of labels the registry has opinions
    about. Anything else resolves to ``DEFAULT_PROFILE``."""
    return list(_REGISTRY.keys())


def session_mode_for_project(
    project_path: str | None, *, box: str = ""
) -> str | None:
    """Read the ``session_mode`` string from ``intent.md`` for a
    project. Returns ``None`` if the file is missing or the field is
    unset — callers should treat that as "default profile applies"
    rather than an error.

    ``box`` is remote provenance: pass the session's box so a remote
    project's intent.md is read from its own box-aware directory rather
    than colliding with a local project that shares the path."""
    if not project_path:
        return None
    try:
        intent = load_intent(intent_path(project_path, box=box))
    except Exception:
        return None
    label = intent.front.session_mode
    if not isinstance(label, str):
        return None
    return label.strip() or None


def active_profile_for_project(
    project_path: str | None, *, box: str = ""
) -> ModeProfile:
    """Convenience: read intent.md, look up the matching profile.

    Workers call this each time they process an event. The intent.md
    read is cheap (~kB) and re-reading on every event means a user
    edit lands without daemon restart.
    """
    return profile_for(session_mode_for_project(project_path, box=box))
