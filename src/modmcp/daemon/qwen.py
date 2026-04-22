"""Qwen / llama.cpp OpenAI-compatible client, serialized through a queue.

Non-standard sampler params (``top_k``, ``min_p``, ``repetition_penalty``)
are passed via ``extra_body`` so they route through proxies like LiteLLM to
the underlying llama.cpp / vLLM backend.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from openai import OpenAI

from ..config import get_config

log = logging.getLogger(__name__)

CallKind = Literal["synth", "drift", "query", "rubric", "consolidator"]


class QwenClient:
    """Single queue in front of a local OpenAI-compatible endpoint."""

    def __init__(self) -> None:
        cfg = get_config()
        self._cfg = cfg
        self._client = OpenAI(base_url=cfg.qwen_endpoint, api_key=cfg.qwen_api_key)
        self._sem = asyncio.Semaphore(1)

    def _max_tokens_for(self, kind: CallKind) -> int:
        cfg = self._cfg
        return {
            "synth": cfg.qwen_max_tokens_synth,
            "drift": cfg.qwen_max_tokens_drift,
            "query": cfg.qwen_max_tokens_query,
            "rubric": cfg.qwen_max_tokens_rubric,
            "consolidator": cfg.qwen_max_tokens_consolidator,
        }[kind]

    def _thinking_for(self, kind: CallKind) -> bool:
        cfg = self._cfg
        return {
            "synth": cfg.qwen_enable_thinking_synth,
            "drift": cfg.qwen_enable_thinking_drift,
            "query": cfg.qwen_enable_thinking_query,
            "rubric": cfg.qwen_enable_thinking_rubric,
            "consolidator": cfg.qwen_enable_thinking_consolidator,
        }[kind]

    def _model_for(self, kind: CallKind) -> str:
        cfg = self._cfg
        override = {
            "synth": cfg.qwen_model_synth,
            "drift": cfg.qwen_model_drift,
            "query": cfg.qwen_model_query,
            "rubric": cfg.qwen_model_rubric,
            "consolidator": cfg.qwen_model_consolidator,
        }[kind]
        return override or cfg.qwen_model

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
        temp = cfg.qwen_temperature if temperature is None else temperature
        mt = self._max_tokens_for(kind) if max_tokens is None else max_tokens

        extra_body: dict[str, Any] = {
            "top_k": cfg.qwen_top_k,
            "min_p": cfg.qwen_min_p,
            "repetition_penalty": cfg.qwen_repetition_penalty,
            "chat_template_kwargs": {
                "enable_thinking": self._thinking_for(kind),
            },
        }

        kwargs: dict[str, Any] = dict(
            model=self._model_for(kind),
            temperature=temp,
            top_p=cfg.qwen_top_p,
            presence_penalty=cfg.qwen_presence_penalty,
            max_tokens=mt,
            extra_body=extra_body,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        content = choice.message.content or ""

        # Qwen3 thinking models may run out of budget inside the reasoning
        # preamble and return empty content with finish_reason="length". Make
        # that failure mode loud and actionable.
        if not content and choice.finish_reason == "length":
            thinking = self._thinking_for(kind)
            raise RuntimeError(
                "Qwen returned empty content with finish_reason=length; "
                f"the {'thinking preamble' if thinking else 'output'} consumed the whole budget. "
                f"Raise qwen_max_tokens_{kind} (currently {mt})"
                + (f" or set qwen_enable_thinking_{kind}=false." if thinking else ".")
            )
        return content

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
