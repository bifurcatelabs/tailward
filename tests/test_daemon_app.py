"""End-to-end integration tests using FastAPI TestClient.

Exercises hook IPC, web UI routes, and project-save round-trips.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modmcp.daemon.app import create_app
from modmcp.paths import intent_path, project_dir, project_hash
from modmcp.schema.intent import empty_intent, load_intent, save_intent


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


def test_hook_returns_preamble_on_first_call(
    active_mode: Path, tmp_path: Path
) -> None:
    """Preamble injection is an active-mode affordance. Passive mode returns
    an empty response body regardless of intent state — covered in
    ``test_hook_returns_empty_in_passive_mode``."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_project(str(proj), goal="refactor the parser")

    with TestClient(create_app()) as client:
        r = client.post(
            "/hook/userpromptsubmit",
            json={"session_id": "s1", "cwd": str(proj), "prompt": "hello"},
        )
    assert r.status_code == 200
    body = r.json()
    assert "Warden preamble" in body["additionalContext"]
    assert "refactor the parser" in body["additionalContext"]


def test_hook_returns_empty_in_passive_mode(
    tmp_path: Path, client: TestClient
) -> None:
    """Passive mode (the default) suppresses all hook injection even when an
    ``intent.md`` exists for the project."""
    proj = tmp_path / "proj_passive"
    proj.mkdir()
    _seed_project(str(proj), goal="refactor the parser")

    r = client.post(
        "/hook/userpromptsubmit",
        json={"session_id": "s_passive", "cwd": str(proj), "prompt": "hello"},
    )
    assert r.status_code == 200
    assert r.json() == {}


def test_hook_returns_empty_when_no_intent(tmp_path: Path, client: TestClient) -> None:
    proj = tmp_path / "noproj"
    proj.mkdir()
    r = client.post(
        "/hook/userpromptsubmit",
        json={"session_id": "s99", "cwd": str(proj), "prompt": "hi"},
    )
    assert r.status_code == 200
    assert r.json() == {}


def test_web_project_list_and_intent_save(tmp_path: Path, client: TestClient) -> None:
    proj = tmp_path / "proj2"
    proj.mkdir()
    ph = _seed_project(str(proj), goal="initial goal")

    r = client.get("/")
    assert r.status_code == 200
    assert "example" in r.text

    r = client.get(f"/p/{ph}")
    assert r.status_code == 200
    assert "initial goal" in r.text

    r = client.post(
        f"/p/{ph}/save",
        data={
            "session_mode": "build",
            "phase2_turns_remaining": "4",
            "section[Active Goal]": "updated goal",
        },
    )
    assert r.status_code == 200
    assert "saved" in r.text

    updated = load_intent(intent_path(str(proj)))
    assert "updated goal" in updated.sections["Active Goal"]
    assert updated.front.phase2_turns_remaining == 4


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
