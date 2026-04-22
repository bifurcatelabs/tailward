from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test's ~/.modmcp/ and Claude projects root."""
    home = tmp_path / "modmcp_home"
    claude = tmp_path / "claude_projects"
    home.mkdir()
    claude.mkdir()
    monkeypatch.setenv("MODMCP_HOME", str(home))
    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(claude))
    # Clear cached config.
    from modmcp import config as cfg_mod

    cfg_mod._cached = None
    # Default tests to ``active`` warden mode so preamble-dependent tests
    # (and the end-to-end smoke test) exercise the full participation loop.
    # Individual tests that want passive behavior can write their own
    # config.toml before the app fixture runs.
    (home / "config.toml").write_text(
        'warden_mode = "active"\n', encoding="utf-8"
    )
    return tmp_path
