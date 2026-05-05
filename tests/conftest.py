from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test's ~/.tailward/ and Claude projects root."""
    home = tmp_path / "tailward_home"
    claude = tmp_path / "claude_projects"
    home.mkdir()
    claude.mkdir()
    monkeypatch.setenv("TAILWARD_HOME", str(home))
    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(claude))
    from tailward import config as cfg_mod

    cfg_mod._cached = None
    (home / "config.toml").write_text("", encoding="utf-8")
    return tmp_path
