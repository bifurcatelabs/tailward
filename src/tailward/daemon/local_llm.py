"""Local OpenAI-compatible LLM client, serialized through a queue.

Non-standard sampler params (``top_k``, ``min_p``, ``repetition_penalty``)
are passed via ``extra_body`` so they route through proxies like LiteLLM to
the underlying llama.cpp / vLLM backend.

Every call emits a ``llm_call_metrics`` ledger row (call kind, configured
budget, finish_reason, prompt / completion / reasoning token counts,
wall-clock duration) so silent truncation is visible without forcing
operators to rely on feel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Literal

from openai import OpenAI

from ..config import get_config

if TYPE_CHECKING:
    from ..storage.ledger import Ledger

log = logging.getLogger(__name__)

CallKind = Literal["synth", "drift", "query", "rubric", "consolidator"]


class LocalLLMClient:
    """Single queue in front of a local OpenAI-compatible endpoint."""

    def __init__(self) -> None:
        cfg = get_config()
        self._cfg = cfg
        self._client = OpenAI(
            base_url=cfg.local_llm_endpoint, api_key=cfg.local_llm_api_key
        )
        self._sem = asyncio.Semaphore(1)
        self._ledger: Ledger | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_recorder(
        self, ledger: Ledger, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Wire the metric recorder. Called from the daemon lifespan
        once both the ledger is connected and the event loop is the
        one ``_complete_sync`` will bridge metrics back into."""
        self._ledger = ledger
        self._loop = loop

    def _max_tokens_for(self, kind: CallKind) -> int:
        cfg = self._cfg
        return {
            "synth": cfg.local_llm_max_tokens_synth,
            "drift": cfg.local_llm_max_tokens_drift,
            "query": cfg.local_llm_max_tokens_query,
            "rubric": cfg.local_llm_max_tokens_rubric,
            "consolidator": cfg.local_llm_max_tokens_consolidator,
        }[kind]

    def _thinking_for(self, kind: CallKind) -> bool:
        cfg = self._cfg
        return {
            "synth": cfg.local_llm_enable_thinking_synth,
            "drift": cfg.local_llm_enable_thinking_drift,
            "query": cfg.local_llm_enable_thinking_query,
            "rubric": cfg.local_llm_enable_thinking_rubric,
            "consolidator": cfg.local_llm_enable_thinking_consolidator,
        }[kind]

    def _model_for(self, kind: CallKind) -> str:
        cfg = self._cfg
        override = {
            "synth": cfg.local_llm_model_synth,
            "drift": cfg.local_llm_model_drift,
            "query": cfg.local_llm_model_query,
            "rubric": cfg.local_llm_model_rubric,
            "consolidator": cfg.local_llm_model_consolidator,
        }[kind]
        return override or cfg.local_llm_model

    def _temperature_for(self, kind: CallKind) -> float:
        cfg = self._cfg
        override = {
            "synth": cfg.local_llm_temperature_synth,
            "drift": cfg.local_llm_temperature_drift,
            "query": cfg.local_llm_temperature_query,
            "rubric": cfg.local_llm_temperature_rubric,
            "consolidator": cfg.local_llm_temperature_consolidator,
        }[kind]
        return cfg.local_llm_temperature if override is None else override

    def _presence_penalty_for(self, kind: CallKind) -> float:
        cfg = self._cfg
        override = {
            "synth": cfg.local_llm_presence_penalty_synth,
            "drift": cfg.local_llm_presence_penalty_drift,
            "query": cfg.local_llm_presence_penalty_query,
            "rubric": cfg.local_llm_presence_penalty_rubric,
            "consolidator": cfg.local_llm_presence_penalty_consolidator,
        }[kind]
        return cfg.local_llm_presence_penalty if override is None else override

    def resolve_model(self, kind: CallKind) -> str:
        return self._model_for(kind)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        kind: CallKind = "query",
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        async with self._sem:
            return await asyncio.to_thread(
                self._complete_sync,
                system,
                user,
                temperature,
                max_tokens,
                json_mode,
                kind,
            )

    def _complete_sync(
        self,
        system: str,
        user: str,
        temperature: float | None,
        max_tokens: int | None,
        json_mode: bool,
        kind: CallKind,
    ) -> str:
        cfg = self._cfg
        # Caller-passed temperature is the strongest override; otherwise
        # the per-kind setting (which itself falls back to the global
        # ``local_llm_temperature`` if the kind override is ``None``).
        temp = self._temperature_for(kind) if temperature is None else temperature
        mt = self._max_tokens_for(kind) if max_tokens is None else max_tokens
        thinking = self._thinking_for(kind)
        model = self._model_for(kind)
        presence_penalty = self._presence_penalty_for(kind)

        extra_body: dict[str, Any] = {
            "top_k": cfg.local_llm_top_k,
            "min_p": cfg.local_llm_min_p,
            "repetition_penalty": cfg.local_llm_repetition_penalty,
            "chat_template_kwargs": {
                "enable_thinking": thinking,
            },
        }

        kwargs: dict[str, Any] = dict(
            model=model,
            temperature=temp,
            top_p=cfg.local_llm_top_p,
            presence_penalty=presence_penalty,
            max_tokens=mt,
            extra_body=extra_body,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        # Metric scaffold — every code path below funnels back through
        # ``_record_metric`` so the row lands whether the call succeeds,
        # raises, or hits the length-truncation branch.
        metric: dict[str, Any] = {
            "call_kind": kind,
            "model": model,
            "max_tokens": mt,
            "enable_thinking": thinking,
            "prompt_tokens": None,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "finish_reason": None,
            "duration_ms": None,
            "usage_json": None,
            "error": None,
        }
        started = time.monotonic()

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            metric["duration_ms"] = int((time.monotonic() - started) * 1000)
            metric["error"] = f"{type(e).__name__}: {e}"[:500]
            self._record_metric(metric)
            raise

        metric["duration_ms"] = int((time.monotonic() - started) * 1000)
        choice = resp.choices[0]
        metric["finish_reason"] = choice.finish_reason
        _populate_usage(metric, getattr(resp, "usage", None))

        content = choice.message.content or ""

        # Thinking models may run out of budget inside the reasoning
        # preamble and return empty content with finish_reason="length". Make
        # that failure mode loud and actionable. Metric is recorded *before*
        # we raise so the truncation event is captured for the dashboard.
        if not content and choice.finish_reason == "length":
            metric["error"] = "empty content with finish_reason=length"
            self._record_metric(metric)
            raise RuntimeError(
                "Local LLM returned empty content with finish_reason=length; "
                f"the {'thinking preamble' if thinking else 'output'} consumed the whole budget. "
                f"Raise local_llm_max_tokens_{kind} (currently {mt})"
                + (f" or set local_llm_enable_thinking_{kind}=false." if thinking else ".")
            )

        self._record_metric(metric)
        return content

    def _record_metric(self, metric: dict[str, Any]) -> None:
        """Bridge the synchronous metric capture (running in a worker
        thread via ``asyncio.to_thread``) back into the daemon's event
        loop for the actual aiosqlite write. Fire-and-forget; we don't
        want metric persistence to block or fail the LLM call."""
        if self._ledger is None or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._ledger.record_llm_call_metric(**metric),
                self._loop,
            )
        except Exception:
            log.exception("llm_call_metrics record failed for kind=%s", metric.get("call_kind"))

    async def complete_json(self, system: str, user: str, **kw) -> dict:
        raw = await self.complete(system, user, json_mode=True, **kw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        cleaned = raw.strip()
        for fence in ("```json", "```"):
            if cleaned.startswith(fence):
                cleaned = cleaned[len(fence):].lstrip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()
        if not cleaned.startswith("{"):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                cleaned = cleaned[start : end + 1]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            # Truncated JSON (output budget hit). Recover whatever complete
            # top-level keys we can, so callers can still partially fill the
            # schema. Log the tail so operators can see the truncation.
            log.warning(
                "complete_json: salvaging truncated JSON (err=%s); tail=%r",
                e,
                cleaned[-200:],
            )
            salvaged = _salvage_json_object(cleaned)
            if salvaged is not None:
                return salvaged
            raise


def _populate_usage(metric: dict[str, Any], usage: Any) -> None:
    """Extract token counts from an OpenAI-compatible ``usage`` block.

    Handles three shapes seen in the wild:
    * Plain OpenAI: ``{prompt_tokens, completion_tokens, total_tokens}``.
    * vLLM / Qwen3 thinking: adds
      ``completion_tokens_details.reasoning_tokens``.
    * llama.cpp early builds: returns the standard fields only, no
      reasoning split — ``reasoning_tokens`` ends up null and the
      summary endpoint reports it that way honestly.

    The full raw blob is preserved in ``usage_json`` so any
    server-specific extension fields stay forensically inspectable.
    """
    if usage is None:
        return
    try:
        if hasattr(usage, "model_dump"):
            usage_dict = usage.model_dump()
        elif isinstance(usage, dict):
            usage_dict = dict(usage)
        else:
            usage_dict = json.loads(json.dumps(usage, default=str))
    except Exception:
        return
    if not isinstance(usage_dict, dict):
        return
    metric["usage_json"] = json.dumps(usage_dict, default=str)
    metric["prompt_tokens"] = _maybe_int(usage_dict.get("prompt_tokens"))
    metric["completion_tokens"] = _maybe_int(usage_dict.get("completion_tokens"))
    metric["total_tokens"] = _maybe_int(usage_dict.get("total_tokens"))
    details = usage_dict.get("completion_tokens_details") or {}
    if isinstance(details, dict):
        metric["reasoning_tokens"] = _maybe_int(details.get("reasoning_tokens"))


def _maybe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _salvage_json_object(text: str) -> dict | None:
    """Greedy, tolerant top-level-object parser for truncated JSON.

    Walks ``text`` one character at a time tracking brace/quote depth. When we
    hit the end of the (possibly truncated) input, we close every unterminated
    string and object in order, then parse. Returns ``None`` if even that
    can't produce valid JSON.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    end = -1
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if in_str:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end >= 0:
        candidate = text[start : end + 1]
    else:
        # Close anything still open.
        tail = ""
        if in_str:
            tail += '"'
        tail += "}" * max(depth, 0)
        candidate = text[start:] + tail

    # Drop trailing comma if we truncated mid-key.
    candidate = candidate.replace(",}", "}").replace(",\n}", "\n}")

    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        # Drop the last partial key:value pair and try again.
        last_comma = candidate.rfind(",", 0, candidate.rfind("}") if "}" in candidate else len(candidate))
        if last_comma > 0:
            retry = candidate[:last_comma] + "}"
            try:
                obj = json.loads(retry)
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
        return None
