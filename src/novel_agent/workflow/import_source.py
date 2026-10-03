"""Deterministic Project-local novel import extraction; no OCR or semantic splitting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from novel_agent.domain.errors import InvalidChange, UnsafeProjectPath
from novel_agent.infrastructure.filesystem.paths import resolve_project_file

_CHAPTER = re.compile(
    r"^(?:#{1,3}\s*)?(?P<title>(?:第[一二三四五六七八九十百千万零〇0-9]+[章节回]"
    r"|Chapter\s+[0-9]+)(?:[\s:：·.、-]+[^\r\n]+)?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ImportedChapter:
    title: str
    manuscript: str


def extract_novel_source(root: Path, relative_path: str) -> tuple[ImportedChapter, ...]:
    path = resolve_project_file(root, relative_path)
    relative = path.relative_to(root.resolve(strict=True))
    if not relative.parts or relative.parts[0].casefold() != "sources":
        raise UnsafeProjectPath("Novel import source must be under Project sources/")
    suffix = path.suffix.casefold()
    if suffix not in {".txt", ".md", ".pdf"}:
        raise InvalidChange("Novel import supports only .txt, .md and .pdf")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise InvalidChange("PDF text extraction failed") from exc
    else:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise InvalidChange("Novel import source must be UTF-8 text") from exc
    if not text.strip():
        raise InvalidChange("Novel import source has no extractable text; OCR is unsupported")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings = [
        (index, match.group("title").strip())
        for index, line in enumerate(lines)
        if (match := _CHAPTER.match(line.strip()))
    ]
    if not headings:
        return (ImportedChapter(path.stem, text.strip()),)
    chapters = []
    for number, (start, title) in enumerate(headings):
        end = headings[number + 1][0] if number + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if not body:
            raise InvalidChange(f"Imported Chapter has no manuscript: {title}")
        chapters.append(ImportedChapter(title, body))
    preface = "\n".join(lines[: headings[0][0]]).strip()
    if preface:
        chapters.insert(0, ImportedChapter("前言", preface))
    return tuple(chapters)
