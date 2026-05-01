"""``warden`` CLI: daemon lifecycle, handoff, hook, link.

Internal package name remains ``modmcp`` per the v2.0.0 light-path
rebrand. The legacy ``modmcp`` console script is kept as a backward-
compat alias; both invoke the same entry point.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from . import __version__
from .config import get_config
from .daemon import lifecycle
from .paths import (
    archive_dir,
    canonicalize_project_path,
    claude_projects_root,
    ensure_layout,
    intent_path,
    project_dir,
    project_toml_path,
)
from .schema.intent import empty_intent, load_intent, save_intent

app = typer.Typer(
    add_completion=False,
    help="warden — local-first audit underlay for Claude Code.",
    no_args_is_help=True,
)

daemon_app = typer.Typer(help="Daemon lifecycle.")
app.add_typer(daemon_app, name="daemon")

hook_app = typer.Typer(help="Hook bridges invoked by Claude Code.")
app.add_typer(hook_app, name="hook")


# ---------------- top-level ----------------


@app.callback()
def _root() -> None:
    ensure_layout()


@app.command()
def version() -> None:
    """Print warden version."""
    typer.echo(__version__)


# ---------------- daemon ----------------


@daemon_app.command("start")
def daemon_start() -> None:
    """Start the daemon in the background."""
    try:
        pid = lifecycle.start()
    except Exception as e:
        typer.echo(f"failed to start daemon: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(
        f"warden daemon started "
        f"(pid={pid}, mode={get_config().warden_mode}, "
        f"url={lifecycle.health_url()})"
    )


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop the daemon."""
    ok = lifecycle.stop()
    typer.echo("stopped" if ok else "not running")


@daemon_app.command("status")
def daemon_status() -> None:
    """Print daemon status."""
    typer.echo(json.dumps(lifecycle.status(), indent=2))


@daemon_app.command("logs")
def daemon_logs(n: int = typer.Option(100, help="Lines to tail.")) -> None:
    """Tail the daemon log."""
    typer.echo(lifecycle.tail_log(n))


@daemon_app.command("run")
def daemon_run(
    host: str = typer.Option(None), port: int = typer.Option(None)
) -> None:
    """Run the daemon in the foreground (blocks). Useful for debugging."""
    import uvicorn

    from .daemon.app import create_app

    cfg = get_config()
    uvicorn.run(
        create_app(),
        host=host or cfg.http_host,
        port=port or cfg.http_port,
        log_level="info",
    )


# ---------------- handoff ----------------


@app.command()
def handoff(
    session: str = typer.Option(
        None, "--session", help="Claude session ID (defaults to most recent in cwd project)."
    ),
    no_edit: bool = typer.Option(False, "--no-edit", help="Skip $EDITOR review."),
    auto: bool = typer.Option(
        False, "--auto/--manual", help="Auto-synthesize via Qwen (M5). Default: manual template."
    ),
    project: Path = typer.Option(
        None, "--project", help="Project path (defaults to cwd)."
    ),
) -> None:
    """Capture intent from a Claude Code session into ``intent.md``."""
    project_path = str((project or Path.cwd()).resolve())

    transcript = _resolve_transcript(project_path, session)
    if transcript is None:
        typer.echo(
            f"no transcript found under {claude_projects_root()} for project {project_path}",
            err=True,
        )
        raise typer.Exit(code=2)

    target = intent_path(project_path)
    _ensure_project_toml(project_path)

    if target.exists():
        _snapshot_before_overwrite(target)
        intent = load_intent(target)
    else:
        intent = empty_intent(project_path, Path(project_path).name)

    if auto:
        try:
            from .daemon.qwen import QwenClient
            from .phase1 import synthesize

            qwen = QwenClient()
            synthesize(qwen, transcript, intent)
        except Exception as e:
            typer.echo(
                f"auto-synthesis failed ({e}); writing partial intent with incomplete=true",
                err=True,
            )
            intent.front.incomplete = True

    save_intent(intent, target)
    typer.echo(f"wrote {target}")

    if not no_edit:
        _open_in_editor(target)
        intent = load_intent(target)
        save_intent(intent, target)  # normalize formatting

    typer.echo("handoff complete")


@app.command()
def link(
    project: Path = typer.Option(None, "--project", help="Project path (defaults to cwd)."),
) -> None:
    """Symlink the project's ``intent.md`` into ``<repo>/.tailward/intent.md``."""
    project_path = (project or Path.cwd()).resolve()
    target = intent_path(str(project_path))
    if not target.exists():
        typer.echo(f"no intent.md at {target}; run `tailward handoff` first", err=True)
        raise typer.Exit(code=2)

    link_dir = project_path / ".tailward"
    link_dir.mkdir(exist_ok=True)
    link_file = link_dir / "intent.md"

    if link_file.exists() or link_file.is_symlink():
        link_file.unlink()

    try:
        os.symlink(target, link_file)
        typer.echo(f"symlinked {link_file} -> {target}")
    except OSError as e:
        typer.echo(f"symlink not available ({e}); copying instead", err=True)
        shutil.copy2(target, link_file)
        typer.echo(f"copied {target} -> {link_file}")


# ---------------- hook ----------------


@hook_app.command("userpromptsubmit")
def hook_userpromptsubmit() -> None:
    """Bridge for Claude Code's UserPromptSubmit hook.

    Reads JSON on stdin, POSTs to the daemon, writes daemon's JSON response on
    stdout. On any failure it exits cleanly with empty output so the user's
    prompt is never blocked.
    """
    import httpx

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        sys.exit(0)

    # Normalize common fields across hook schema variants.
    normalized = {
        "session_id": payload.get("session_id") or payload.get("sessionId"),
        "transcript_path": payload.get("transcript_path") or payload.get("transcriptPath"),
        "prompt": payload.get("prompt") or payload.get("user_prompt") or "",
        "cwd": payload.get("cwd") or payload.get("project_root") or os.getcwd(),
    }

    cfg = get_config()
    url = f"http://{cfg.http_host}:{cfg.http_port}/hook/userpromptsubmit"
    timeout = cfg.hook_timeout_ms / 1000.0

    try:
        r = httpx.post(url, json=normalized, timeout=timeout)
        if r.status_code == 200:
            sys.stdout.write(r.text)
            return
    except Exception:
        pass
    # Silent pass-through on any failure.
    sys.exit(0)


# ---------------- helpers ----------------


def _resolve_transcript(project_path: str, session: str | None) -> Path | None:
    root = claude_projects_root()
    if not root.exists():
        return None

    # Claude Code sanitizes the project path; try a few encodings.
    canonical = canonicalize_project_path(project_path)
    candidates = [
        canonical.replace(":", "").replace("/", "-"),
        canonical.replace(":", "-").replace("/", "-"),
        canonical.replace("/", "-"),
    ]

    search_dirs: list[Path] = []
    for c in candidates:
        d = root / c
        if d.exists():
            search_dirs.append(d)
    if not search_dirs:
        # Fallback: search the whole root and match by cwd inside files.
        search_dirs = [p for p in root.iterdir() if p.is_dir()]

    best: Path | None = None
    best_mtime = -1.0
    for d in search_dirs:
        for jsonl in d.glob("*.jsonl"):
            if session and jsonl.stem != session:
                continue
            m = jsonl.stat().st_mtime
            if m > best_mtime:
                best = jsonl
                best_mtime = m
    return best


def _ensure_project_toml(project_path: str) -> None:
    pdir = project_dir(project_path)
    pdir.mkdir(parents=True, exist_ok=True)
    toml = project_toml_path(project_path)
    if toml.exists():
        return
    name = Path(project_path).name
    toml.write_text(
        f'project_path = "{project_path}"\n'
        f'project_name = "{name}"\n'
        f'created = "{datetime.now(UTC).isoformat()}"\n',
        encoding="utf-8",
    )


def _snapshot_before_overwrite(intent_file: Path) -> None:
    if not intent_file.exists():
        return
    archive = archive_dir(
        load_intent(intent_file).front.project_path
        if intent_file.exists()
        else str(intent_file.parent)
    )
    archive.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    (archive / f"intent-{ts}.md").write_bytes(intent_file.read_bytes())


def _open_in_editor(path: Path) -> None:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        editor = "notepad" if sys.platform == "win32" else "vi"
    try:
        subprocess.run([editor, str(path)], check=False)
    except FileNotFoundError:
        typer.echo(f"editor {editor!r} not found; edit {path} manually", err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
