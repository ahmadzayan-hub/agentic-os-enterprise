"""Semantic-boundary chunking.

Chunks respect document structure first (headings, then paragraphs, then
sentences) and only fall back to hard splitting when a single unit exceeds the
budget. Each chunk carries its section path and character span so a citation
can be resolved back to a location a human can find.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TARGET_TOKENS = 320
DEFAULT_OVERLAP_TOKENS = 48

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$|^([A-Z][A-Za-z0-9 ,'/()-]{3,80})\n[=-]{3,}$", re.MULTILINE)
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_TOKEN = re.compile(r"\w+|[^\w\s]")


def count_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


@dataclass(slots=True)
class Chunk:
    index: int
    content: str
    token_count: int
    section_path: str = ""
    char_start: int = 0
    char_end: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "content": self.content,
            "token_count": self.token_count,
            "section_path": self.section_path,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "metadata": self.metadata,
        }


def _sections(text: str) -> list[tuple[str, str, int]]:
    """Split into (section_path, body, char_offset) using markdown headings."""
    matches = list(re.finditer(r"^(#{1,6})\s+(.+)$", text, re.MULTILINE))
    if not matches:
        return [("", text, 0)]

    sections: list[tuple[str, str, int]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(("", preamble, 0))

    stack: list[str] = []
    for position, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        stack = stack[: level - 1]
        stack.append(title)
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((" / ".join(stack), body, start))
    return sections


def _split_long_unit(unit: str, target_tokens: int) -> list[str]:
    sentences = _SENTENCE_SPLIT.split(unit)
    parts: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        sentence_tokens = count_tokens(sentence)
        if current and current_tokens + sentence_tokens > target_tokens:
            parts.append(" ".join(current))
            current, current_tokens = [], 0
        if sentence_tokens > target_tokens:
            # A single sentence longer than the budget: hard-split on words.
            words = sentence.split()
            step = max(1, target_tokens)
            for i in range(0, len(words), step):
                parts.append(" ".join(words[i : i + step]))
            continue
        current.append(sentence)
        current_tokens += sentence_tokens
    if current:
        parts.append(" ".join(current))
    return [p for p in parts if p.strip()]


def chunk_text(
    text: str,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Chunk a document, preserving structure and overlapping for continuity."""
    if not text or not text.strip():
        return []
    if overlap_tokens >= target_tokens:
        overlap_tokens = target_tokens // 4

    chunks: list[Chunk] = []
    index = 0

    for section_path, body, offset in _sections(text):
        units: list[str] = []
        for paragraph in _PARAGRAPH_SPLIT.split(body):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if count_tokens(paragraph) > target_tokens:
                units.extend(_split_long_unit(paragraph, target_tokens))
            else:
                units.append(paragraph)

        buffer: list[str] = []
        buffer_tokens = 0
        for unit in units:
            unit_tokens = count_tokens(unit)
            if buffer and buffer_tokens + unit_tokens > target_tokens:
                content = "\n\n".join(buffer)
                start = offset + body.find(buffer[0])
                chunks.append(
                    Chunk(
                        index=index,
                        content=content,
                        token_count=count_tokens(content),
                        section_path=section_path,
                        char_start=max(0, start),
                        char_end=max(0, start) + len(content),
                    )
                )
                index += 1
                # Carry the tail of the previous chunk so a fact split across a
                # boundary is still retrievable from at least one chunk.
                carry: list[str] = []
                carry_tokens = 0
                for previous in reversed(buffer):
                    previous_tokens = count_tokens(previous)
                    if carry_tokens + previous_tokens > overlap_tokens:
                        break
                    carry.insert(0, previous)
                    carry_tokens += previous_tokens
                buffer, buffer_tokens = carry, carry_tokens
            buffer.append(unit)
            buffer_tokens += unit_tokens

        if buffer:
            content = "\n\n".join(buffer)
            start = offset + body.find(buffer[0])
            chunks.append(
                Chunk(
                    index=index,
                    content=content,
                    token_count=count_tokens(content),
                    section_path=section_path,
                    char_start=max(0, start),
                    char_end=max(0, start) + len(content),
                )
            )
            index += 1

    return chunks
