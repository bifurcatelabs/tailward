from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test's ~/.modmcp/ and Claude projects root.

    Defaults to ``warden_mode = "passive"`` — the product's supported
    surface. Tests that exercise the opt-in active loop (preamble injection,
    drift corrective delivery) must depend on the ``active_mode`` fixture
    below so the config is rewritten before ``create_app`` boots the
    daemon.
    """
    home = tmp_path / "modmcp_home"
    claude = tmp_path / "claude_projects"
    home.mkdir()
    claude.mkdir()
    monkeypatch.setenv("MODMCP_HOME", str(home))
    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(claude))
    from tailward import config as cfg_mod

    cfg_mod._cached = None
    (home / "config.toml").write_text(
        'warden_mode = "passive"\n', encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def active_mode(_isolated_home: Path) -> Path:
    """Opt a test into ``warden_mode = "active"``.

    List this fixture *before* any ``TestClient`` / ``create_app`` fixture
    in the test signature so the config flip lands before the daemon
    lifespan reads it. The shared ``_isolated_home`` autouse fixture always
    runs first and seeds the passive default, so this overwrite is safe.
    """
    home = _isolated_home / "modmcp_home"
    (home / "config.toml").write_text(
        'warden_mode = "active"\n', encoding="utf-8"
    )
    from tailward import config as cfg_mod

    cfg_mod._cached = None
    return _isolated_home
