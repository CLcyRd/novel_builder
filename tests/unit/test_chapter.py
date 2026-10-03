import pytest

from novel_agent.domain.chapter import ChapterDocument, parse_chapter, serialize_chapter
from novel_agent.domain.errors import InvalidChapter


@pytest.mark.parametrize(
    "summary,outline,manuscript",
    [
        ("", "", ""),
        ("概述\n", "- 场景一\n- 场景二", "# 内部标题\n\n雨落了下来。\n"),
        ("包含标点、数字123", "", "First line\nSecond line"),
    ],
)
def test_section_content_round_trips_exactly(summary, outline, manuscript):
    chapter = ChapterDocument("第一章 雨夜", summary, outline, manuscript)
    assert parse_chapter(serialize_chapter(chapter)) == chapter


@pytest.mark.parametrize(
    "bad_text",
    [
        "# 第一章\n正文",
        serialize_chapter(ChapterDocument("第一章", "", "", "")).replace("\n", "\r\n"),
        serialize_chapter(ChapterDocument("第一章", "", "", "")).replace(
            "<!-- novel-agent:outline:end -->", "<!-- novel-agent:summary:end -->"
        ),
        serialize_chapter(ChapterDocument("第一章", "", "", "")) + "extra",
    ],
)
def test_corrupt_or_ambiguous_format_fails(bad_text):
    with pytest.raises(InvalidChapter):
        parse_chapter(bad_text)


def test_reserved_marker_cannot_be_embedded_in_manuscript():
    chapter = ChapterDocument("第一章", "", "", "正文\n<!-- novel-agent:manuscript:end -->\n后文")
    with pytest.raises(InvalidChapter):
        serialize_chapter(chapter)
