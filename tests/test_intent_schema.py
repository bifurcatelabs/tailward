from __future__ import annotations

from modmcp.schema.intent import SECTIONS, dump_markdown, empty_intent, parse_markdown


def test_empty_intent_has_all_sections() -> None:
    i = empty_intent("/tmp/proj", "proj")
    for s in SECTIONS:
        assert s in i.sections


def test_round_trip_markdown() -> None:
    i = empty_intent("/tmp/proj", "proj")
    i.set("Active Goal", "Ship M0.")
    i.append_list_item("Open Threads", "wire up /health endpoint [high]")
    i.append_list_item("Active Rules", "minimal change; no scope creep")
    text = dump_markdown(i)
    assert "# Active Goal" in text
    assert "Ship M0." in text
    assert "- wire up /health endpoint [high]" in text

    back = parse_markdown(text)
    assert back.front.project_path == "/tmp/proj"
    assert "Ship M0." in back.sections["Active Goal"]
    assert "wire up /health endpoint" in back.sections["Open Threads"]
    assert "minimal change" in back.sections["Active Rules"]
