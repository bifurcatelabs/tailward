"""Storage paths, project hashing, and atomic file writes."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path


def home_dir() -> Path:
    """Root modmcp state directory. Override via ``MODMCP_HOME`` env var."""
    override = os.environ.get("MODMCP_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".modmcp"


def logs_dir() -> Path:
    return home_dir() / "logs"


def projects_dir() -> Path:
    return home_dir() / "projects"


def config_path() -> Path:
    return home_dir() / "config.toml"


def pid_path() -> Path:
    return home_dir() / "daemon.pid"


def ledger_path() -> Path:
    return home_dir() / "ledger.db"


def daemon_log_path() -> Path:
    return logs_dir() / "daemon.log"


def claude_projects_root() -> Path:
    """Claude Code's per-project transcript storage."""
    override = os.environ.get("CLAUDE_PROJECTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".claude" / "projects"


def canonicalize_project_path(p: str | os.PathLike[str]) -> str:
    """Return a canonical string form of a project path suitable for hashing.

    On Windows: lowercase drive letter, forward slashes, resolved symlinks/junctions.
    On POSIX: fully resolved path.
    """
    resolved = Path(p).expanduser().resolve()
    s = str(resolved)
    if sys.platform == "win32":
        s = s.replace("\\", "/")
        if len(s) >= 2 and s[1] == ":":
            s = s[0].lower() + s[1:]
    return s


def project_hash(project_path: str | os.PathLike[str]) -> str:
    """12-char SHA-256 hex digest of the canonical project path."""
    canonical = canonicalize_project_path(project_path)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def project_dir(project_path: str | os.PathLike[str]) -> Path:
    return projects_dir() / project_hash(project_path)


def intent_path(project_path: str | os.PathLike[str]) -> Path:
    return project_dir(project_path) / "intent.md"


def archive_dir(project_path: str | os.PathLike[str]) -> Path:
    return project_dir(project_path) / "archive"


def project_toml_path(project_path: str | os.PathLike[str]) -> Path:
    return project_dir(project_path) / "project.toml"


def ensure_layout() -> None:
    """Create base storage directories if missing. Safe to call repeatedly."""
    for d in (home_dir(), logs_dir(), projects_dir()):
        d.mkdir(parents=True, exist_ok=True)


def atomic_write_text(target: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``target`` atomically (write-tmp-then-rename)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_bytes(target: Path, content: bytes) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
