"""Daemon start/stop/status: detached process + PID file.

Uses HTTP-loopback IPC for everything (hook + web UI), so lifecycle here is
just "spawn a background uvicorn server process, track its PID, stop via
signal". This is the minimum that works on Windows + POSIX without needing
launchd/systemd/Task Scheduler — those are optional wrappers shipped in M10.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from ..config import get_config
from ..paths import daemon_log_path, ensure_layout, pid_path


def _read_pid() -> int | None:
    p = pid_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except (ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"], text=True, stderr=subprocess.DEVNULL
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], text=True
        )
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def health_url() -> str:
    cfg = get_config()
    return f"http://{cfg.http_host}:{cfg.http_port}/health"


def ping(timeout: float = 0.5) -> bool:
    try:
        r = httpx.get(health_url(), timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def start() -> int:
    """Spawn the daemon as a detached background process and return its PID."""
    ensure_layout()
    existing = _read_pid()
    if existing and _pid_alive(existing) and ping():
        return existing

    cfg = get_config()
    log = open(daemon_log_path(), "a", buffering=1, encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "modmcp.daemon",
        "--host",
        cfg.http_host,
        "--port",
        str(cfg.http_port),
    ]

    kwargs: dict[str, object] = dict(stdout=log, stderr=log, stdin=subprocess.DEVNULL)

    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["close_fds"] = False
    else:
        kwargs["start_new_session"] = True
        kwargs["close_fds"] = True

    proc = subprocess.Popen(cmd, **kwargs)  # type: ignore[arg-type]
    pid_path().write_text(str(proc.pid))

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if ping(timeout=0.3):
            return proc.pid
        if proc.poll() is not None:
            raise RuntimeError(
                f"daemon exited early with code {proc.returncode}; see {daemon_log_path()}"
            )
        time.sleep(0.2)
    raise TimeoutError("daemon did not come up within 10s")


def stop(timeout: float = 5.0) -> bool:
    pid = _read_pid()
    if not pid:
        return False
    if not _pid_alive(pid):
        pid_path().unlink(missing_ok=True)
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    pid_path().unlink(missing_ok=True)
    return True


def status() -> dict:
    pid = _read_pid()
    alive = bool(pid and _pid_alive(pid))
    healthy = ping() if alive else False
    return {
        "pid": pid,
        "alive": alive,
        "healthy": healthy,
        "log": str(daemon_log_path()),
        "url": f"http://{get_config().http_host}:{get_config().http_port}",
    }


def tail_log(n: int = 100) -> str:
    p: Path = daemon_log_path()
    if not p.exists():
        return ""
    with open(p, "rb") as f:
        try:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, 64 * 1024)
            f.seek(size - chunk)
            data = f.read()
        except OSError:
            data = f.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-n:])
