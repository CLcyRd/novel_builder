"""Opt-in Phase 1 gate: real committed Project excerpt sent through ModelGateway."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from novel_agent.application import build_commit_service, build_project_service
from novel_agent.domain.change import CreateChapter, SetSection
from novel_agent.infrastructure.models.gateway import ModelGateway


@pytest.mark.real_llm
@pytest.mark.parametrize("provider", ["deepseek", "grok"])
def test_real_model_reads_committed_project_excerpt(tmp_path, provider):
    gateway = ModelGateway.from_env(Path(".env"))
    assert provider in gateway.available_models()
    service = build_project_service()
    project = service.create_project(tmp_path, "真实模型验收", "测试会话")
    chapter_id = uuid4()
    result = build_commit_service().commit(
        project,
        (
            CreateChapter("第一章 雨夜", chapter_id=chapter_id),
            SetSection(chapter_id, "manuscript", text="林舟在雨夜捡到一枚铜钥匙。"),
        ),
    )
    assert "MANUSCRIPT.md" in result.changed_files
    formal_paths = [project.root / relative for relative in result.changed_files]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in formal_paths}
    excerpt = service.read_section(project, chapter_id, "manuscript")
    assert excerpt == "林舟在雨夜捡到一枚铜钥匙。"
    response = gateway.invoke(excerpt, "请用一句中文概括，不要续写。", model=provider)
    assert response.provider == provider
    assert response.returned_model
    assert response.content.strip()
    assert before == {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in formal_paths
    }
    print(f"provider={response.provider} model={response.returned_model}")
