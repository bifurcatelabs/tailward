"""Local LLM endpoint probe worker (v0.2 platform).

Periodically pings the configured ``local_llm_endpoint`` with a cheap
``GET /v1/models`` request, records latency + status to the
``probe_results`` table, and surfaces the result on the LiveBus-less
poll path the Platform view consumes via ``GET /probes/recent``.

We deliberately do not probe ``api.anthropic.com`` /
``status.anthropic.com``: synthetic probes against those targets
mostly measure your ISP, transit, and the CDN edge, not Anthropic's
service health. The actionable inference-path signal comes from the
in-band per-turn metrics derived in ``app.py`` — the probe worker's
job is bounded to "is the model server I configured reachable, and how
fast does it answer a no-op request right now."
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import httpx

from ..config import get_config

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)


class ProbeWorker:
    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        cfg = get_config()
        if not cfg.probe_enabled:
            log.info("probe worker disabled by config")
            return
        self._stop.clear()
        self._client = httpx.AsyncClient(timeout=cfg.probe_timeout_seconds)
        self._task = asyncio.create_task(self._run(), name="probe-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            cfg = get_config()
            try:
                await self._probe_local_llm(cfg)
            except Exception:
                log.exception("probe loop iteration raised")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=cfg.probe_interval_seconds,
                )
                # If wait returned without timing out, the stop event
                # was set — exit the loop.
                return
            except TimeoutError:
                continue

    async def _probe_local_llm(self, cfg) -> None:
        """One probe iteration: GET <local_llm_endpoint>/models.

        OpenAI-compatible servers (llama.cpp, Ollama, vLLM, LM Studio)
        all answer this. The response payload is small; we record the
        model count when available so the Platform view can show
        which model is loaded.
        """
        if self._client is None:
            return
        # Probe profile 1's endpoint — the default that most workers
        # route through. Profile 2 is opt-in; probing it would double
        # probe load without a clear use case until per-profile health
        # tracking lands as its own surface.
        profile = cfg.profile(1)
        endpoint = (profile.endpoint or "").rstrip("/")
        if not endpoint:
            return
        url = f"{endpoint}/models"
        headers = {}
        if profile.api_key:
            headers["Authorization"] = f"Bearer {profile.api_key}"

        start = time.monotonic()
        status = "error"
        latency_ms: int | None = None
        detail: str | None = None
        error: str | None = None
        try:
            r = await self._client.get(url, headers=headers)
            latency_ms = int((time.monotonic() - start) * 1000)
            if r.status_code == 200:
                status = "ok"
                try:
                    data = r.json()
                    models = data.get("data") or data.get("models") or []
                    if isinstance(models, list):
                        names = []
                        for m in models[:5]:
                            if isinstance(m, dict):
                                name = m.get("id") or m.get("name")
                                if name:
                                    names.append(str(name))
                            elif isinstance(m, str):
                                names.append(m)
                        detail = ", ".join(names) if names else None
                except Exception:
                    detail = None
            else:
                error = f"HTTP {r.status_code}"
        except (TimeoutError, httpx.TimeoutException):
            status = "timeout"
            error = f"timeout after {cfg.probe_timeout_seconds}s"
        except httpx.HTTPError as e:
            error = f"{type(e).__name__}: {e}"[:200]
        except Exception as e:
            error = f"{type(e).__name__}: {e}"[:200]

        try:
            await self._daemon.ledger.record_probe_result(
                "local_llm",
                url,
                status=status,
                latency_ms=latency_ms,
                detail=detail,
                error=error,
            )
        except Exception:
            log.exception("probe-result persist failed (target=local_llm)")
