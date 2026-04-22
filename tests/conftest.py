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
    return tmp_path
