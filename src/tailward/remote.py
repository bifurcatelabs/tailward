"""Remote transcript aggregation (Phase 0).

The remote box runs **nothing** — it just has its existing ``sshd`` and the
JSONL transcripts Claude Code writes. The local daemon *pulls* those
transcripts into a local mirror that the watcher consumes as extra projects
roots, so remote sessions ingest through exactly the same pipeline as local
ones. See ``.scratch/remote-aggregation-design.md`` for the full design.

Mirror layout::

    <remote_mirror_root>/<box>/projects/<sanitized>/*.jsonl

Each ``<box>/projects`` directory is a drop-in parallel to the local
``~/.claude/projects/`` root, which is what lets the watcher treat it
identically (see ``TranscriptWatcher`` multi-root support). Box provenance
is folded into each project's hash (see ``project_hash(..., box=...)``), so
two boxes working in the same absolute path stay distinct.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import Config
from .paths import _first_cwd_in_jsonl, home_dir, project_hash


def remote_mirror_root(cfg: Config) -> Path | None:
    """Configured mirror root as an absolute path, or ``None`` if unset."""
    raw = (cfg.remote_mirror_root or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def box_projects_dir(cfg: Config, box: str) -> Path:
    """The ``projects`` directory the watcher consumes for one box.

    Raises ``ValueError`` if no mirror root is configured — the caller
    must check ``remote_mirror_root`` first.
    """
    root = remote_mirror_root(cfg)
    if root is None:
        raise ValueError("remote_mirror_root is not configured")
    return root / box / "projects"


def box_projects_roots(cfg: Config) -> list[Path]:
    """Enumerate every existing ``<mirror>/<box>/projects`` directory.

    Returns an empty list when no mirror root is configured or the mirror
    directory doesn't exist yet (no box has been pulled). Sorted by box
    name for deterministic ordering.
    """
    root = remote_mirror_root(cfg)
    if root is None or not root.is_dir():
        return []
    out: list[Path] = []
    for box_dir in sorted(root.iterdir()):
        if not box_dir.is_dir():
            continue
        projects = box_dir / "projects"
        if projects.is_dir():
            out.append(projects)
    return out


def discover_remote_projects(cfg: Config) -> list[dict]:
    """Projects mirrored under followed boxes, for the project index.

    The watcher only writes a ``session_state`` row once it *ingests* a
    project, and ``discover_claude_projects`` scans the local root only —
    so a freshly pulled remote project would otherwise be invisible (and
    thus un-seedable from the UI). This surfaces each mirrored project as
    a discoverable entry with its box-aware hash + provenance label.

    Returns ``[{project_hash, project_path, box}]``. ``project_path`` is
    read from the first ``cwd`` in a project's JSONL (the authoritative
    signal, same as the local discovery path); projects whose JSONL
    carries no ``cwd`` are skipped.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for projects_root in box_projects_roots(cfg):
        box = projects_root.parent.name
        for entry in sorted(projects_root.iterdir()):
            if not entry.is_dir():
                continue
            for jsonl in entry.glob("*.jsonl"):
                cwd = _first_cwd_in_jsonl(jsonl)
                if not cwd:
                    continue
                ph = project_hash(cwd, box=box)
                if ph in seen:
                    break
                seen.add(ph)
                out.append({"project_hash": ph, "project_path": cwd, "box": box})
                break
    return out


def default_pull_key() -> Path:
    """The dedicated tailward pull key location (may not exist)."""
    return home_dir() / "keys" / "tailward-pull"


def build_rsync_cmd(
    *,
    host: str,
    user: str,
    dest: Path,
    remote_path: str = ".claude/projects/",
    key: Path | None = None,
    port: int = 22,
) -> list[str]:
    """Construct the ``rsync`` argv for pulling one box's transcripts.

    Pure (no side effects) so it's unit-testable. ``dest`` should be the
    box's ``projects`` directory; the trailing slash on ``remote_path``
    makes rsync copy the *contents* of the remote projects dir into it.

    Transport is rsync-over-SSH, local-initiated pull. When ``key`` is
    given it's used with ``IdentitiesOnly`` (the dedicated restricted
    pull key path); otherwise the user's default SSH credentials are
    used — fine for a spike, the restricted-key hardening is a later
    phase. Host keys are pinned on first connect (``accept-new``).
    """
    ssh_parts = ["ssh"]
    if port != 22:
        ssh_parts += ["-p", str(port)]
    if key is not None:
        ssh_parts += ["-i", str(key), "-o", "IdentitiesOnly=yes"]
    ssh_parts += ["-o", "StrictHostKeyChecking=accept-new"]
    ssh = " ".join(ssh_parts)
    # -rtz: recursive, preserve mtimes (the watcher keys on them), compress.
    # --append-verify: only ship appended bytes for growing JSONL, but
    # re-checksum the existing prefix so a rewrite/truncation is caught.
    return [
        "rsync",
        "-rtz",
        "--append-verify",
        "-e",
        ssh,
        f"{user}@{host}:{remote_path}",
        str(dest) + "/",
    ]


class RsyncMissingError(RuntimeError):
    """Raised when the ``rsync`` executable isn't on PATH."""


def pull_box(
    cfg: Config,
    *,
    name: str,
    host: str,
    user: str,
    key: Path | None = None,
    port: int = 22,
    remote_path: str = ".claude/projects/",
) -> subprocess.CompletedProcess[str]:
    """Pull one box's transcripts into the local mirror.

    Creates ``<mirror>/<name>/projects/`` and rsyncs the remote
    transcripts into it. Returns the completed process so the caller can
    surface rsync's own output. Raises ``ValueError`` if no mirror root
    is configured, ``RsyncMissingError`` if rsync isn't installed.
    """
    if remote_mirror_root(cfg) is None:
        raise ValueError(
            "remote_mirror_root is not configured; set it in config.toml "
            "before pulling a remote box"
        )
    if shutil.which("rsync") is None:
        raise RsyncMissingError(
            "rsync not found on PATH. Phase-0 remote pull needs rsync "
            "(on Windows, use WSL or cwRsync; on macOS/Linux it ships or "
            "installs from your package manager)."
        )
    dest = box_projects_dir(cfg, name)
    dest.mkdir(parents=True, exist_ok=True)
    cmd = build_rsync_cmd(
        host=host,
        user=user,
        dest=dest,
        remote_path=remote_path,
        key=key,
        port=port,
    )
    return subprocess.run(cmd, capture_output=True, text=True, check=False)
