"""Storage paths, project hashing, and atomic file writes."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path


def home_dir() -> Path:
    """Root tailward state directory.

    Lookup order: ``TAILWARD_HOME`` env var (canonical, v2.7+), then
    ``MODMCP_HOME`` env var (deprecation alias for users carrying
    v2.x configs forward — slated for removal in v4.0.0), then default
    ``~/.tailward/``. ``migrate_v2x_state_if_needed`` handles the on-
    disk side of the rename: state under ``~/.modmcp/`` is copied
    forward on first ``ensure_layout`` call.
    """
    override = os.environ.get("TAILWARD_HOME")
    if override:
        return Path(override).expanduser().resolve()
    legacy = os.environ.get("MODMCP_HOME")
    if legacy:
        return Path(legacy).expanduser().resolve()
    return Path.home() / ".tailward"


def _legacy_home_dir() -> Path:
    """Pre-v2.7 state directory. Used by the migration helper to find
    state worth carrying forward when a user upgrades from a v2.x
    install. Never the active state path on a v2.7+ install."""
    return Path.home() / ".modmcp"


def migrate_v2x_state_if_needed() -> bool:
    """Copy v2.x state from ``~/.modmcp/`` to ``~/.tailward/`` on first
    v2.7+ run. Idempotent — safe to call repeatedly.

    Migration fires only when:
      * the new ``~/.tailward/`` doesn't exist yet, AND
      * a populated ``~/.modmcp/`` exists from a prior install

    Strategy is copy + breadcrumb (not move): the legacy directory
    stays in place as a backup. A ``MIGRATED_TO_TAILWARD.txt`` file
    is dropped in the legacy dir so a user navigating there sees
    where state went. ``TAILWARD_HOME`` / ``MODMCP_HOME`` overrides
    bypass migration — they're explicit user intent.

    Returns ``True`` if migration ran, ``False`` otherwise.
    """
    # Explicit env-var override means user is in control; don't touch.
    if os.environ.get("TAILWARD_HOME") or os.environ.get("MODMCP_HOME"):
        return False
    new_home = Path.home() / ".tailward"
    if new_home.exists():
        return False
    legacy = _legacy_home_dir()
    if not legacy.exists() or not legacy.is_dir():
        return False
    # Confirm legacy looks like a real tailward state dir (avoids
    # copying an empty ``~/.modmcp`` somebody created by accident).
    looks_like_state = any(
        (legacy / probe).exists()
        for probe in ("ledger.db", "config.toml", "projects")
    )
    if not looks_like_state:
        return False
    shutil.copytree(legacy, new_home)
    breadcrumb = legacy / "MIGRATED_TO_TAILWARD.txt"
    try:
        breadcrumb.write_text(
            "State directory was migrated from ~/.modmcp/ to ~/.tailward/ "
            "during a v2.7+ first-run.\n\n"
            "This directory is preserved as a backup. The active state "
            "is now in ~/.tailward/. Once you've confirmed the new "
            "install works, this directory can be safely deleted.\n",
            encoding="utf-8",
        )
    except OSError:
        # Breadcrumb is a nice-to-have, not load-bearing.
        pass
    return True


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


def claude_dir_name(project_path: str) -> str:
    """Encode a project path to its Claude Code per-project directory name.

    Claude Code stores transcripts in
    ``~/.claude/projects/<encoded>/`` where ``<encoded>`` is the
    project's filesystem path with separators (``:``, ``/``, ``\\``)
    replaced by ``-``. The encoding is lossy: ``C:/foo/bar`` and
    ``C:\\foo-bar`` both encode to ``C--foo-bar``. Used for dedup
    when matching ledger rows against discovered directories.
    """
    s = project_path.replace(":", "-").replace("/", "-").replace("\\", "-")
    return s


def discover_claude_projects(
    skip_dirs: set[str] | None = None,
) -> dict[str, str]:
    """Map Claude Code project directories under ``claude_projects_root()``
    to ``{project_hash: project_path}``.

    Scans JSONL events for the canonical ``cwd`` field — the same
    authoritative signal the watcher's ``_is_seeded`` uses, since
    Claude Code's sanitized folder names don't always round-trip
    cleanly to the original path. Directories where no event in any
    JSONL carries a ``cwd`` are omitted.

    ``skip_dirs`` is a set of Claude Code per-project directory names
    (typically derived from ledger rows via ``claude_dir_name``).
    Matching directories are skipped — the ledger already represents
    them under whichever cwd encoding it persisted, and a single
    Claude Code directory can hold sessions written with different
    encodings (Claude Code's cwd serialization has shifted across
    versions). Surfacing a ghost row for the alternate encoding from
    the same directory would be noise.
    """
    skip_dirs = skip_dirs or set()
    root = claude_projects_root()
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in skip_dirs:
            continue
        for jsonl in entry.glob("*.jsonl"):
            cwd = _first_cwd_in_jsonl(jsonl)
            if cwd:
                out.setdefault(project_hash(cwd), cwd)
                break
    return out


def _first_cwd_in_jsonl(path: Path, max_lines: int = 200) -> str | None:
    """Return the first ``cwd`` value found in a JSONL file.

    Claude Code prefixes transcripts with metadata events
    (``queue-operation``, ``permission-mode``) that lack ``cwd``;
    the field appears starting with the first ``user`` / ``assistant``
    event. Reads up to ``max_lines`` lines so a malformed file can't
    stream us through gigabytes before yielding.
    """
    import json

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    cwd = data.get("cwd")
                    if cwd:
                        return cwd
    except OSError:
        return None
    return None


def intent_path(project_path: str | os.PathLike[str]) -> Path:
    return project_dir(project_path) / "intent.md"


def archive_dir(project_path: str | os.PathLike[str]) -> Path:
    return project_dir(project_path) / "archive"


def project_toml_path(project_path: str | os.PathLike[str]) -> Path:
    return project_dir(project_path) / "project.toml"


def ensure_layout() -> None:
    """Create base storage directories if missing. Safe to call repeatedly.

    Runs the v2.x → v2.7+ state migration before anything else so a
    user upgrading from a prior install lands on their existing
    ledger / intent.md files / configs, not an empty new home.
    """
    migrate_v2x_state_if_needed()
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
