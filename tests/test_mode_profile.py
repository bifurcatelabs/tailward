"""Mode profile registry + worker-side honoring."""

from __future__ import annotations

from modmcp.daemon.mode_profile import (
    ALL_RUBRIC_DIMENSIONS,
    DEFAULT_PROFILE,
    known_profile_names,
    profile_for,
)


def test_known_labels_resolve_to_built_in_profiles() -> None:
    build = profile_for("build")
    assert build.name == "build"
    assert build.scope_creep_floor == 12
    assert set(build.rubric_dimensions) == set(ALL_RUBRIC_DIMENSIONS)
    assert build.scope_event_label == "creep"

    expl = profile_for("exploration")
    assert expl.name == "exploration"
    assert expl.scope_creep_floor is None  # disabled
    assert "maintainability" not in expl.rubric_dimensions
    assert expl.scope_event_label == "spread"

    meta = profile_for("meta")
    assert meta.name == "meta"
    assert meta.scope_creep_floor == 30


def test_unknown_labels_fall_through_to_permissive_default() -> None:
    """yolo / shower thoughts / typos all land on DEFAULT_PROFILE.
    The act of self-labeling is captured (the label is persisted on
    every row) but the audit doesn't enforce SWE thresholds against
    a label it doesn't recognize."""
    assert profile_for("yolo") is DEFAULT_PROFILE
    assert profile_for("shower thoughts") is DEFAULT_PROFILE
    assert profile_for("BUILDX_TYPO") is DEFAULT_PROFILE
    assert profile_for(None) is DEFAULT_PROFILE
    assert profile_for("") is DEFAULT_PROFILE


def test_default_profile_is_permissive() -> None:
    """Permissive == no scope_creep events fire, minimal rubric set,
    spread (not creep) framing in event labels. This is what an
    unlabeled / yolo session should land on."""
    assert DEFAULT_PROFILE.scope_creep_floor is None
    assert len(DEFAULT_PROFILE.rubric_dimensions) <= 2
    assert DEFAULT_PROFILE.scope_event_label == "spread"


def test_label_lookup_is_case_insensitive_and_trims_whitespace() -> None:
    assert profile_for("BUILD").name == "build"
    assert profile_for("  exploration  ").name == "exploration"


def test_known_profile_names_is_just_the_built_ins() -> None:
    """default / fallback profile is *not* in the known list — that's
    the point: the registry has opinions about a small set, everything
    else falls through."""
    names = set(known_profile_names())
    assert names == {"build", "exploration", "meta"}
    assert "default" not in names
    assert "yolo" not in names
