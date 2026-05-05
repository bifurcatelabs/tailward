"""Active Rules parsing + policy matching + worker end-to-end."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tailward.daemon.app import create_app
from tailward.paths import intent_path, project_hash
from tailward.schema.constraints import (
    default_policy,
    is_memory_edit_path,
    parse_active_rules,
)
from tailward.schema.events import TranscriptEvent
from tailward.schema.intent import empty_intent, save_intent


def test_parse_active_rules_immutable_paths() -> None:
    body = "- do not edit pyproject.toml\n- no force push\n"
    policy = parse_active_rules(body)
    assert any("pyproject.toml" in p for p in policy.immutable.paths)


def test_parse_active_rules_allow_only() -> None:
    body = "- only edit files under src/\n"
    policy = parse_active_rules(body)
    assert any("src" in pat for pat in policy.path.allow)


def test_parse_active_rules_forbidden_bash() -> None:
    body = "- no force push\n- skip tests is forbidden\n"
    policy = parse_active_rules(body)
    assert policy.bash.patterns, "expected bash patterns from force-push hint"


def test_default_policy_blocks_force_push() -> None:
    policy = default_policy()
    assert policy.bash.violation_for("git push --force origin main") is not None


# ---------- memory-edit path detection ----------


def test_memory_edit_path_matches_windows() -> None:
    p = r"C:\Users\user\.claude\projects\C--warden\memory\foo.md"
    assert is_memory_edit_path(p)


def test_memory_edit_path_matches_unix() -> None:
    p = "/home/glenn/.claude/projects/-home-glenn-warden/memory/notes.md"
    assert is_memory_edit_path(p)


def test_memory_edit_path_matches_nested() -> None:
    p = "/home/u/.claude/projects/proj/memory/sub/dir/file.md"
    assert is_memory_edit_path(p)


def test_memory_edit_path_rejects_non_memory() -> None:
    assert not is_memory_edit_path(
        r"C:\Users\user\.claude\projects\C--warden\todos\foo.json"
    )


def test_memory_edit_path_rejects_project_root_edit() -> None:
    assert not is_memory_edit_path(r"C:\warden\src\tailward\app.py")


def test_memory_edit_path_rejects_empty() -> None:
    assert not is_memory_edit_path("")


def test_default_policy_blocks_rm_rf_root() -> None:
    policy = default_policy()
    assert policy.bash.violation_for("rm -rf /") is not None


# ---- mute-the-alarm baselines (failure mode 5) --------------------------


def test_default_policy_blocks_no_verify() -> None:
    policy = default_policy()
    assert policy.bash.violation_for("git commit --no-verify -m wip") is not None
    assert policy.bash.violation_for("git push --no-verify origin main") is not None


def test_default_policy_blocks_test_command_or_true_silence() -> None:
    policy = default_policy()
    assert policy.bash.violation_for("pytest -q || true") is not None
    assert policy.bash.violation_for("npm test || true") is not None
    assert policy.bash.violation_for("cargo test || true") is not None


def test_default_policy_allows_unrelated_or_true() -> None:
    """A bare ``cmd || true`` on a non-test command must not trip the baseline.

    Regression guard: the mute-the-alarm pattern is anchored to named
    test/lint tools so that legitimate fallbacks (``mkdir -p foo || true``)
    don't generate noise.
    """
    policy = default_policy()
    assert policy.bash.violation_for("mkdir -p tmp || true") is None
    assert policy.bash.violation_for("grep foo bar.txt || true") is None


def test_default_policy_blocks_pytest_deselect_and_negated_k() -> None:
    policy = default_policy()
    assert policy.bash.violation_for("pytest --deselect tests/test_slow.py") is not None
    assert policy.bash.violation_for("pytest -k 'not integration'") is not None


# ---- target-gaming baselines (failure mode 10) --------------------------


def test_default_policy_blocks_override_ini() -> None:
    policy = default_policy()
    assert policy.bash.violation_for("pytest --override-ini addopts=''") is not None


def test_default_policy_blocks_cov_fail_under_zero() -> None:
    policy = default_policy()
    assert policy.bash.violation_for("pytest --cov-fail-under=0") is not None
    assert policy.bash.violation_for("pytest --cov-fail-under = 0") is not None


def test_default_policy_allows_cov_fail_under_nonzero() -> None:
    """Raising the bar (``--cov-fail-under=80``) is legitimate; only = 0 is gaming."""
    policy = default_policy()
    assert policy.bash.violation_for("pytest --cov-fail-under=80") is None
    assert policy.bash.violation_for("pytest --cov-fail-under=50") is None


def test_default_policy_blocks_coverage_omit_at_runtime() -> None:
    policy = default_policy()
    assert policy.bash.violation_for("coverage run --omit='src/risky/*' -m pytest") is not None


# ---- immutable measurement artifacts (failure mode 10) -----------------


def test_default_policy_marks_ci_workflows_immutable() -> None:
    policy = default_policy()
    assert policy.immutable.violation_for(".github/workflows/ci.yml") is not None
    assert policy.immutable.violation_for(".github/actions/setup/action.yml") is not None


def test_default_policy_marks_coverage_configs_immutable() -> None:
    policy = default_policy()
    assert policy.immutable.violation_for(".coveragerc") is not None
    assert policy.immutable.violation_for("codecov.yml") is not None
    assert policy.immutable.violation_for("tox.ini") is not None
    assert policy.immutable.violation_for(".pre-commit-config.yaml") is not None


def test_default_policy_leaves_regular_source_alone() -> None:
    policy = default_policy()
    assert policy.immutable.violation_for("src/foo/bar.py") is None
    assert policy.immutable.violation_for("tests/test_x.py") is None


# ---- rule_text rendering ------------------------------------------------


def test_default_policy_rule_texts_are_human_readable() -> None:
    """Violations should render a human sentence, not a raw regex."""
    policy = default_policy()
    # Keyed by the exact pattern; the worker's _lookup_rule falls back here.
    force_push_pat = next(
        p for p in policy.bash.patterns if "force" in p
    )
    assert "force-push" in policy.rule_texts[force_push_pat]
    override_ini_pat = next(
        p for p in policy.bash.patterns if "override-ini" in p
    )
    assert "measurement" in policy.rule_texts[override_ini_pat].lower()


def test_path_policy_allow_only_denies_outside() -> None:
    from tailward.schema.constraints import PathPolicy
    p = PathPolicy(allow=["src/**"])
    assert p.violation_for("tests/foo.py") is not None
    assert p.violation_for("src/a/b.py") is None


def test_immutable_glob_match() -> None:
    from tailward.schema.constraints import ImmutableFiles
    i = ImmutableFiles(paths=["pyproject.toml", ".github/workflows/**"])
    assert i.violation_for("pyproject.toml") == "pyproject.toml"
    assert i.violation_for(".github/workflows/ci.yml") is not None
    assert i.violation_for("src/foo.py") is None


def _seed_intent(project_path: str, rules_body: str) -> None:
    intent = empty_intent(project_path, Path(project_path).name)
    intent.set("Active Rules", rules_body)
    save_intent(intent, intent_path(project_path))


@pytest.mark.asyncio
async def test_constraints_worker_fires_on_force_push(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_intent(str(proj), "- no force push\n")

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        # Drive a tool_use event synthetically.
        ev = TranscriptEvent(
            raw={"id": "t1"}, kind="tool_use", session_id="s-constraint",
            timestamp=None, text="",
            tool_name="Bash", tool_input={"command": "git push --force origin main"},
        )
        class _FS:
            session_id = "s-constraint"
            project_path = str(proj)
            project_hash = project_hash(str(proj))
        fs = _FS()
        await daemon.ledger.upsert_session(fs.session_id, fs.project_hash, fs.project_path)

        assert daemon.constraints is not None
        await daemon.constraints.enqueue(ev, fs)

        async def _check():
            for _ in range(40):
                rows = await daemon.ledger.recent_violations(fs.project_hash)
                if rows:
                    return rows
                await asyncio.sleep(0.1)
            return []

        rows = await _check()
        assert rows, "constraints worker did not record a violation"
        assert any("forbidden-bash" in r["rule_id"] for r in rows)


@pytest.mark.asyncio
async def test_constraints_worker_flags_immutable_write(tmp_path: Path) -> None:
    proj = tmp_path / "proj_imm"
    proj.mkdir()
    _seed_intent(str(proj), "- do not edit pyproject.toml\n")

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        ev = TranscriptEvent(
            raw={"id": "t2"}, kind="tool_use", session_id="s-imm",
            timestamp=None, text="",
            tool_name="Edit",
            tool_input={"file_path": "pyproject.toml", "old_string": "a", "new_string": "b"},
        )
        class _FS:
            session_id = "s-imm"
            project_path = str(proj)
            project_hash = project_hash(str(proj))
        fs = _FS()
        await daemon.ledger.upsert_session(fs.session_id, fs.project_hash, fs.project_path)
        await daemon.constraints.enqueue(ev, fs)

        for _ in range(40):
            rows = await daemon.ledger.recent_violations(fs.project_hash)
            if rows:
                break
            await asyncio.sleep(0.1)
        assert rows, "immutable-file violation not recorded"
        assert any(r["rule_id"].startswith("immutable:") for r in rows)
