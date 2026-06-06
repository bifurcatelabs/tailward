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
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .paths import _first_cwd_in_jsonl, atomic_write_text, home_dir, project_hash


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


# --------------- followed-box list (persistent) ---------------
#
# The set of remote boxes tailward follows, persisted in its own file
# (NOT config.toml — Config stays a flat scalar/list schema; this is an
# array-of-tables). Consumed by the CLI (`remote add/remove/pull`) and,
# later, by the interval auto-pull worker.


@dataclass
class RemoteBox:
    name: str
    host: str
    user: str
    enabled: bool = True
    port: int = 22
    remote_path: str = ".claude/projects/"
    key: str = ""  # path to an SSH key; "" = the user's default credentials
    interval_seconds: int = 60  # auto-pull cadence (used by the worker)


def follow_list_path() -> Path:
    return home_dir() / "remote_follow.toml"


def load_follow_list() -> list[RemoteBox]:
    """Followed boxes from disk, or [] if none configured."""
    path = follow_list_path()
    if not path.exists():
        return []
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    out: list[RemoteBox] = []
    for entry in raw.get("remote_follow", []) or []:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        out.append(
            RemoteBox(
                name=name,
                host=entry.get("host", ""),
                user=entry.get("user", ""),
                enabled=bool(entry.get("enabled", True)),
                port=int(entry.get("port", 22)),
                remote_path=entry.get("remote_path", ".claude/projects/"),
                key=entry.get("key", ""),
                interval_seconds=int(entry.get("interval_seconds", 60)),
            )
        )
    return out


def _toml_str(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_follow_list(boxes: list[RemoteBox]) -> None:
    lines = [
        "# tailward followed remote boxes.",
        "# Managed by `tailward remote add` / `remote remove`; editable by hand.",
        "",
    ]
    for b in boxes:
        lines.append("[[remote_follow]]")
        lines.append(f"name = {_toml_str(b.name)}")
        lines.append(f"host = {_toml_str(b.host)}")
        lines.append(f"user = {_toml_str(b.user)}")
        lines.append(f"enabled = {'true' if b.enabled else 'false'}")
        lines.append(f"port = {b.port}")
        lines.append(f"remote_path = {_toml_str(b.remote_path)}")
        lines.append(f"key = {_toml_str(b.key)}")
        lines.append(f"interval_seconds = {b.interval_seconds}")
        lines.append("")
    atomic_write_text(follow_list_path(), "\n".join(lines))


def get_followed_box(name: str) -> RemoteBox | None:
    return next((b for b in load_follow_list() if b.name == name), None)


def upsert_followed_box(box: RemoteBox) -> None:
    """Add ``box`` to the follow list, replacing any entry with the same name."""
    boxes = [b for b in load_follow_list() if b.name != box.name]
    boxes.append(box)
    boxes.sort(key=lambda b: b.name)
    save_follow_list(boxes)


def remove_followed_box(name: str) -> bool:
    """Drop the box named ``name``. Returns True if it was present."""
    boxes = load_follow_list()
    kept = [b for b in boxes if b.name != name]
    if len(kept) == len(boxes):
        return False
    save_follow_list(kept)
    return True


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


def pull_followed(cfg: Config, box: RemoteBox) -> subprocess.CompletedProcess[str]:
    """Pull a followed box using its stored connection details."""
    return pull_box(
        cfg,
        name=box.name,
        host=box.host,
        user=box.user,
        key=Path(box.key) if box.key else None,
        port=box.port,
        remote_path=box.remote_path,
    )
