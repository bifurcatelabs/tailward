"""LLM-call instrumentation: every Qwen call lands a row in
``llm_call_metrics`` capturing call_kind, configured budget,
finish_reason, and the usage breakdown — so we can answer "is the
rubric truncating mid-think" with data, not feel.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from tailward.daemon.qwen import QwenClient, _populate_usage
from tailward.storage.ledger import Ledger


def _fake_response(
    *,
    content: str,
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    reasoning_tokens: int | None = None,
    total_tokens: int | None = None,
):
    """Mimic an OpenAI ChatCompletion response shape."""
    usage_dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens
            if total_tokens is not None
            else prompt_tokens + completion_tokens,
    }
    if reasoning_tokens is not None:
        usage_dict["completion_tokens_details"] = {
            "reasoning_tokens": reasoning_tokens
        }
    usage = SimpleNamespace(
        model_dump=lambda: dict(usage_dict),
        **usage_dict,
    )
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_populate_usage_extracts_reasoning_tokens() -> None:
    metric = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "usage_json": None,
    }
    resp = _fake_response(
        content="hi",
        prompt_tokens=42,
        completion_tokens=88,
        reasoning_tokens=1500,
    )
    _populate_usage(metric, resp.usage)
    assert metric["prompt_tokens"] == 42
    assert metric["completion_tokens"] == 88
    assert metric["reasoning_tokens"] == 1500
    assert metric["total_tokens"] == 42 + 88
    assert "completion_tokens_details" in (metric["usage_json"] or "")


def test_populate_usage_handles_servers_without_reasoning_split() -> None:
    """Older llama.cpp builds and plain OpenAI don't report
    ``completion_tokens_details``. The metric row must still land
    with reasoning_tokens=None rather than failing the call."""
    metric = {k: None for k in (
        "prompt_tokens", "completion_tokens", "reasoning_tokens",
        "total_tokens", "usage_json",
    )}
    resp = _fake_response(content="hi", prompt_tokens=10, completion_tokens=20)
    _populate_usage(metric, resp.usage)
    assert metric["completion_tokens"] == 20
    assert metric["reasoning_tokens"] is None


@pytest.mark.asyncio
async def test_complete_records_metric_on_success(tmp_path) -> None:
    """End-to-end: a successful call writes a row tagged with the
    call_kind, configured max_tokens, and the finish_reason."""
    db = tmp_path / "ledger.db"
    ledger = Ledger(db_path=db)
    await ledger.connect()

    client = QwenClient()
    client.attach_recorder(ledger, asyncio.get_running_loop())
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: _fake_response(
                    content='{"ok":true}',
                    finish_reason="stop",
                    prompt_tokens=120,
                    completion_tokens=200,
                    reasoning_tokens=900,
                ),
            ),
        ),
    )

    out = await client.complete("sys", "user", kind="rubric")
    assert out == '{"ok":true}'

    # The recorder is fire-and-forget; give the bridged coroutine a
    # tick to land before we query.
    await asyncio.sleep(0.05)
    rows = await ledger.llm_call_metrics_recent(call_kind="rubric")
    assert len(rows) == 1
    row = rows[0]
    assert row["call_kind"] == "rubric"
    assert row["finish_reason"] == "stop"
    assert row["completion_tokens"] == 200
    assert row["reasoning_tokens"] == 900
    assert row["enable_thinking"] in (0, 1)
    assert row["max_tokens"] is not None
    assert row["error"] is None
    await ledger.close()


@pytest.mark.asyncio
async def test_complete_records_metric_on_length_truncation(tmp_path) -> None:
    """When the call returns empty content with finish_reason=length
    (the smoking-gun shape for thinking-mode budget exhaustion), the
    client raises *and* the metric row lands with the error captured."""
    db = tmp_path / "ledger.db"
    ledger = Ledger(db_path=db)
    await ledger.connect()

    client = QwenClient()
    client.attach_recorder(ledger, asyncio.get_running_loop())
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: _fake_response(
                    content="",
                    finish_reason="length",
                    prompt_tokens=300,
                    completion_tokens=2500,
                    reasoning_tokens=2500,
                ),
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="finish_reason=length"):
        await client.complete("sys", "user", kind="rubric")

    await asyncio.sleep(0.05)
    rows = await ledger.llm_call_metrics_recent(call_kind="rubric")
    assert len(rows) == 1
    row = rows[0]
    assert row["finish_reason"] == "length"
    assert row["error"] and "length" in row["error"]
    assert row["reasoning_tokens"] == 2500
    await ledger.close()


@pytest.mark.asyncio
async def test_per_kind_temperature_and_presence_flow_to_api(tmp_path) -> None:
    """Per-call-kind sampler overrides reach the underlying chat-completion
    call. Regression for the Qwen3 profile split: rubric uses the
    precise-coding profile (temp=0.6, presence=0.0); other thinking-on
    kinds use the general profile (temp=1.0, presence=1.5)."""
    db = tmp_path / "ledger.db"
    ledger = Ledger(db_path=db)
    await ledger.connect()

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _fake_response(content='{"ok":true}')

    client = QwenClient()
    client.attach_recorder(ledger, asyncio.get_running_loop())
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=_capture),
        ),
    )

    await client.complete("sys", "user", kind="rubric")
    assert captured["temperature"] == pytest.approx(0.6)
    assert captured["presence_penalty"] == pytest.approx(0.0)

    captured.clear()
    await client.complete("sys", "user", kind="synth")
    assert captured["temperature"] == pytest.approx(1.0)
    assert captured["presence_penalty"] == pytest.approx(1.5)

    captured.clear()
    await client.complete("sys", "user", kind="drift")
    assert captured["temperature"] == pytest.approx(1.0)
    assert captured["presence_penalty"] == pytest.approx(1.5)

    await ledger.close()


@pytest.mark.asyncio
async def test_complete_records_metric_on_http_failure(tmp_path) -> None:
    """If the underlying HTTP call raises, the error is captured in
    the metric row (with duration), then re-raised. Without this we'd
    silently lose the timing data on the most interesting calls."""
    db = tmp_path / "ledger.db"
    ledger = Ledger(db_path=db)
    await ledger.connect()

    client = QwenClient()
    client.attach_recorder(ledger, asyncio.get_running_loop())

    def _boom(**_):
        time.sleep(0.01)
        raise RuntimeError("connection refused")

    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=_boom),
        ),
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        await client.complete("sys", "user", kind="drift")

    await asyncio.sleep(0.05)
    rows = await ledger.llm_call_metrics_recent(call_kind="drift")
    assert len(rows) == 1
    row = rows[0]
    assert row["finish_reason"] is None
    assert row["error"] and "connection refused" in row["error"]
    assert row["duration_ms"] is not None and row["duration_ms"] >= 0
    await ledger.close()
