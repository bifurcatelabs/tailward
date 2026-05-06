"""Bounded-shutdown integration tests.

Spawns the daemon as a subprocess (regular Python, not the PyInstaller
bundle), POSTs /shutdown, and measures the actual time until the
process exits. This catches the failure mode the v3 Tauri shell hits:
even after cooperative /shutdown returns 200 and uvicorn logs
"Application shutdown complete," the OS process can linger for tens of
seconds because workers' in-flight LLM calls (asyncio.to_thread + sync
OpenAI client) keep underlying executor threads alive past lifespan
teardown.

The baseline test exercises a clean daemon (no transcripts in the test
home, no in-flight LLM calls) — should pass quickly. The slow-worker
test injects a stubbed worker whose stop() sleeps 30s, simulating the
hung-LLM-call case; verifies the bounded teardown still lets the
process exit within the budget.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


SHUTDOWN_BUDGET_SECS = 10.0


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(port: int, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=0.5
            ) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError):
            time.sleep(0.2)
    raise TimeoutError(
        f"daemon did not become healthy on port {port} within {timeout_s}s"
    )


def _post_shutdown(port: int) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/shutdown", method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.URLError:
        # EOF mid-response is acceptable — uvicorn may close the socket
        # before flushing on shutdown. The signal still landed.
        pass


def _measure_exit(proc: subprocess.Popen, budget_s: float) -> float:
    """Return seconds until process exits, or raise if it exceeds budget."""
    started = time.monotonic()
    while time.monotonic() - started < budget_s:
        if proc.poll() is not None:
            return time.monotonic() - started
        time.sleep(0.1)
    elapsed = time.monotonic() - started
    raise AssertionError(
        f"daemon did not exit within {budget_s}s of /shutdown "
        f"(still alive after {elapsed:.1f}s)"
    )


def _spawn_daemon(port: int, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "tailward.daemon", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def test_clean_daemon_shuts_down_within_budget():
    """Baseline: daemon with no transcripts to backfill, no in-flight LLM
    calls. Should exit promptly after /shutdown."""
    port = _find_free_port()
    proc = _spawn_daemon(port)
    try:
        _wait_for_health(port)
        _post_shutdown(port)
        elapsed = _measure_exit(proc, SHUTDOWN_BUDGET_SECS)
        # Sanity: should be well under the budget on a clean test env.
        assert elapsed < SHUTDOWN_BUDGET_SECS, (
            f"baseline shutdown took {elapsed:.1f}s — within budget but "
            f"slower than expected for a clean daemon"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_daemon_with_slow_worker_still_shuts_down_within_budget():
    """Slow but cancellable worker (asyncio.sleep). The wait_for
    cancellation should kill it cleanly. This validates the easy case
    of bounded teardown — coroutines that respond to cancellation."""
    port = _find_free_port()
    proc = _spawn_daemon(
        port,
        extra_env={"TAILWARD_TEST_SLOW_WORKER_SECS": "30"},
    )
    try:
        _wait_for_health(port)
        _post_shutdown(port)
        elapsed = _measure_exit(proc, SHUTDOWN_BUDGET_SECS)
        assert elapsed < SHUTDOWN_BUDGET_SECS, (
            f"shutdown with cancellable slow worker took {elapsed:.1f}s — "
            f"bounded teardown failed to cap a slow worker"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_daemon_with_hung_thread_worker_still_shuts_down_within_budget():
    """Worker whose stop() awaits asyncio.to_thread(time.sleep, N) —
    the cancellation pattern that DOESN'T work, because cancelling the
    awaiting coroutine doesn't interrupt the sync call in the executor
    thread. Same failure mode as a blocking sync HTTP call to a slow
    LLM endpoint via the OpenAI sync client.

    This test reproduces the real production failure (process linger
    of ~60s after window close in the v3 manual test). It will FAIL
    until we either:
      - Make LLM calls natively cancellable (httpx.AsyncClient
        instead of to_thread + sync OpenAI client), or
      - Force-exit via os._exit in __main__.py before asyncio's loop
        teardown gets a chance to await executor.shutdown.
    """
    port = _find_free_port()
    proc = _spawn_daemon(
        port,
        extra_env={"TAILWARD_TEST_HUNG_WORKER_SECS": "30"},
    )
    try:
        _wait_for_health(port)
        _post_shutdown(port)
        elapsed = _measure_exit(proc, SHUTDOWN_BUDGET_SECS)
        assert elapsed < SHUTDOWN_BUDGET_SECS, (
            f"shutdown with hung-thread worker took {elapsed:.1f}s — "
            f"bounded teardown does not cap uncancellable thread work"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
