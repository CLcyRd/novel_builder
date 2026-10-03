from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from novel_agent.domain.models import ChapterEntry, ProjectMetadata, SessionMetadata


def test_project_index_requires_exactly_one_order_entry_per_chapter():
    chapter_id = uuid4()
    base = dict(
        project_id=uuid4(),
        name="雨夜",
        created_at=datetime.now(UTC),
        initial_session_id=uuid4(),
        chapters={str(chapter_id): ChapterEntry(chapter_id=chapter_id, filename="0001.md")},
    )
    with pytest.raises(ValidationError, match="chapter_order"):
        ProjectMetadata(**base)
    with pytest.raises(ValidationError, match="chapter_order"):
        ProjectMetadata(**base, chapter_order=[chapter_id, chapter_id])
    assert ProjectMetadata(**base, chapter_order=[chapter_id]).chapter_order == (chapter_id,)


def test_session_metadata_defaults_are_isolated_from_workflow_controls():
    session = SessionMetadata(
        session_id=uuid4(),
        project_id=uuid4(),
        name="新会话",
        created_at=datetime.now(UTC),
    )
    assert session.execution_mode == "FREEFORM"
    assert session.auto_approval is False
    assert session.current_model is None
