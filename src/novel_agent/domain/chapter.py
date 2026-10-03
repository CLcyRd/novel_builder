"""A single formal chapter and its explicit Markdown serialization."""

from __future__ import annotations

import re
from dataclasses import dataclass

from novel_agent.domain.errors import InvalidChapter

SECTIONS = ("summary", "outline", "manuscript")
MARKERS = tuple(
    f"<!-- novel-agent:{section}:{boundary} -->"
    for section in SECTIONS
    for boundary in ("start", "end")
)


@dataclass(frozen=True)
class ChapterDocument:
    title: str
    summary: str
    outline: str
    manuscript: str


def _validate_content(document: ChapterDocument) -> None:
    if not document.title or document.title.strip() != document.title:
        raise InvalidChapter("chapter title must be nonempty and have no surrounding whitespace")
    if "\n" in document.title or "\r" in document.title:
        raise InvalidChapter("chapter title must be a single line")
    if any("\r" in getattr(document, section) for section in SECTIONS):
        raise InvalidChapter("chapter sections must use LF newlines")
    for section in SECTIONS:
        content_lines = getattr(document, section).split("\n")
        if any(line in MARKERS for line in content_lines):
            raise InvalidChapter("reserved section marker inside chapter content")


def serialize_chapter(document: ChapterDocument) -> str:
    """Write canonical wrappers without altering the three section strings."""
    _validate_content(document)
    parts = [f"# {document.title}\n\n"]
    for i, section in enumerate(SECTIONS):
        value = getattr(document, section)
        parts.append(f"<!-- novel-agent:{section}:start -->\n{value}\n")
        parts.append(f"<!-- novel-agent:{section}:end -->\n")
        if i < len(SECTIONS) - 1:
            parts.append("\n")
    return "".join(parts)


_CHAPTER_PATTERN = re.compile(
    r"\A# (?P<title>[^\r\n]+)\n\n"
    + "\n\n".join(
        rf"<!-- novel-agent:{section}:start -->\n(?P<{section}>.*?)\n"
        rf"<!-- novel-agent:{section}:end -->"
        for section in SECTIONS
    )
    + r"\n\Z",
    re.DOTALL,
)


def parse_chapter(text: str) -> ChapterDocument:
    """Reject ambiguity or corruption instead of guessing chapter boundaries."""
    if "\r" in text:
        raise InvalidChapter("formal chapters must use LF newlines")
    match = _CHAPTER_PATTERN.fullmatch(text)
    if match is None:
        raise InvalidChapter("chapter is missing a title or ordered section markers")
    document = ChapterDocument(**match.groupdict())
    _validate_content(document)
    if serialize_chapter(document) != text:
        raise InvalidChapter("chapter is not in canonical format")
    return document
