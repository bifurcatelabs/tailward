"""End-to-end integration tests using FastAPI TestClient.

Exercises hook IPC, web UI routes, and project-save round-trips.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tailward.daemon.app import create_app
from tailward.paths import intent_path, project_dir, project_hash
from tailward.schema.intent import empty_intent, save_intent


def _seed_project(project_path: str, goal: str = "ship M0 through M10") -> str:
    pd = project_dir(project_path)
    pd.mkdir(parents=True, exist_ok=True)
    intent = empty_intent(project_path, "example")
    intent.set("Active Goal", goal)
    save_intent(intent, intent_path(project_path))
    return project_hash(project_path)


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


def test_bind_info_loopback_default(client: TestClient) -> None:
    """The default config binds to 127.0.0.1; bind-info reports loopback
    with no warning. This is the safe case."""
    r = client.get("/api/bind-info")
    assert r.status_code == 200
    body = r.json()
    assert body["http_host"] == "127.0.0.1"
    assert body["is_loopback"] is True
    assert body["warning"] is None


def test_bind_info_warns_on_non_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the daemon is bound to 0.0.0.0 (or any non-loopback host),
    the bind-info endpoint surfaces a non-null warning so the Settings
    view can render the network-exposure banner.

    Regression guard: the 0.0.0.0 binding is the most realistic v3
    user foot-gun (per ``project_localhost_binding_security.md``). If
    this test stops asserting a warning, we've silently regressed the
    user-facing surface that calls out the unauthenticated exposure.
    """
    home = tmp_path / "tailward_home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(
        'http_host = "0.0.0.0"\n', encoding="utf-8"
    )
    monkeypatch.setenv("TAILWARD_HOME", str(home))
    from tailward import config as cfg_mod

    cfg_mod._cached = None

    with TestClient(create_app()) as c:
        r = c.get("/api/bind-info")

    assert r.status_code == 200
    body = r.json()
    assert body["http_host"] == "0.0.0.0"
    assert body["is_loopback"] is False
    assert body["warning"] is not None
    assert "0.0.0.0" in body["warning"]
    assert "no authentication" in body["warning"]


def test_web_landing_and_project_pages_serve_spa(
    tmp_path: Path, client: TestClient
) -> None:
    """v2.1 retired the Jinja project list + intent editor; both URLs
    now serve the same SPA shell. The shell-coverage tests live in
    ``test_live_ui.py``; this case just verifies the routes still
    respond 200 with a mount node."""
    proj = tmp_path / "proj2"
    proj.mkdir()
    ph = _seed_project(str(proj), goal="initial goal")

    r = client.get("/")
    assert r.status_code == 200
    assert 'id="app"' in r.text

    r = client.get(f"/p/{ph}")
    assert r.status_code == 200
    assert 'id="app"' in r.text


def test_api_ledger_and_drift_empty(tmp_path: Path, client: TestClient) -> None:
    proj = tmp_path / "proj3"
    proj.mkdir()
    ph = _seed_project(str(proj))

    r = client.get(f"/api/projects/{ph}/ledger")
    assert r.status_code == 200
    assert r.json() == []

    r = client.get(f"/api/projects/{ph}/drift")
    assert r.status_code == 200
    assert r.json() == []
