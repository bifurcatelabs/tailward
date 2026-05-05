"""Captured-intent schema and markdown (de)serialization.

The on-disk format is a markdown file with YAML frontmatter. The body is a
sequence of ``# Section`` headers whose contents are plain markdown that humans
can edit freely. We parse section bodies as either list-of-lines or free prose.

Source of truth: the markdown file. This module round-trips it safely.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

import frontmatter
from pydantic import BaseModel, Field

SessionMode = Literal["build", "meta", "exploration"]

SECTIONS = [
    "Receiving Posture",
    "Active Goal",
    "Open Threads",
    "Active Rules",
    "Known User Drift Patterns",
    "Known Agent Drift Patterns",
    "Commitments (pending)",
    "Recent Claims",
    "Notes",
]


class Frontmatter(BaseModel):
    project_path: str
    project_name: str
    created: datetime
    updated: datetime
    session_mode: SessionMode = "build"
    incomplete: bool = False


class Intent(BaseModel):
    """In-memory view of a captured-intent document."""

    front: Frontmatter
    sections: dict[str, str] = Field(default_factory=dict)

    def get(self, section: str) -> str:
        return self.sections.get(section, "")

    def set(self, section: str, body: str) -> None:
        if section not in SECTIONS:
            raise ValueError(f"Unknown section: {section!r}")
        self.sections[section] = body.rstrip() + "\n"
        self.front.updated = datetime.now(UTC)

    def append_list_item(self, section: str, item: str) -> None:
        body = self.sections.get(section, "").rstrip()
        new_line = f"- {item.strip()}"
        self.sections[section] = (body + "\n" + new_line if body else new_line) + "\n"
        self.front.updated = datetime.now(UTC)


def empty_intent(project_path: str, project_name: str) -> Intent:
    now = datetime.now(UTC)
    front = Frontmatter(
        project_path=project_path,
        project_name=project_name,
        created=now,
        updated=now,
    )
    body_defaults = {s: "" for s in SECTIONS}
    body_defaults["Receiving Posture"] = (
        "[1-3 sentences setting how the next agent should receive the user regardless of opener]\n"
    )
    body_defaults["Active Goal"] = (
        "[What this session is trying to accomplish, concrete and bounded]\n"
    )
    return Intent(front=front, sections=body_defaults)


_HEADER_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)


def parse_markdown(text: str) -> Intent:
    """Parse a captured-intent markdown document."""
    post = frontmatter.loads(text)
    front = Frontmatter.model_validate(post.metadata)
    body = post.content

    sections: dict[str, str] = {}
    matches = list(_HEADER_RE.finditer(body))
    for i, m in enumerate(matches):
        title = m.group("title").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_body = body[start:end].lstrip("\n")
        sections[title] = section_body
    return Intent(front=front, sections=sections)


def dump_markdown(intent: Intent) -> str:
    """Serialize back to the canonical markdown form."""
    post = frontmatter.Post(content="")
    metadata: dict[str, object] = {
        "project_path": intent.front.project_path,
        "project_name": intent.front.project_name,
        "created": intent.front.created.isoformat(),
        "updated": intent.front.updated.isoformat(),
        "session_mode": intent.front.session_mode,
    }
    if intent.front.incomplete:
        metadata["incomplete"] = True
    post.metadata = metadata

    chunks: list[str] = []
    for name in SECTIONS:
        body = intent.sections.get(name, "").rstrip()
        chunks.append(f"# {name}\n{body}\n" if body else f"# {name}\n\n")
    post.content = "\n".join(chunks).rstrip() + "\n"

    return frontmatter.dumps(post) + "\n"


def load_intent(path) -> Intent:
    from pathlib import Path

    return parse_markdown(Path(path).read_text(encoding="utf-8"))


def save_intent(intent: Intent, path) -> None:
    from pathlib import Path

    from ..paths import atomic_write_text

    atomic_write_text(Path(path), dump_markdown(intent))
