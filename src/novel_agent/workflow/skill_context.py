"""Stage-scoped Skill context assembled from immutable Project snapshots."""

from __future__ import annotations

import re

from novel_agent.domain.models import Project
from novel_agent.domain.skill import SkillRef
from novel_agent.prompts.prompt import SKILL_CONTEXT_HEADER
from novel_agent.services.skill import SkillService
from novel_agent.workflow.models import WorkflowRequest

_MAX_CONTEXT = 3000


def skill_context(
    skills: SkillService,
    project: Project,
    request: WorkflowRequest,
    stage: str,
    chapter_index: int = 0,
    evidence: str = "",
) -> tuple[tuple[SkillRef, ...], str]:
    refs: list[SkillRef] = []
    if not request.skill_bindings and not request.skill_candidate_refs:
        refs.extend(request.skill_refs)  # Checkpoints created before scoped bindings.
    else:
        refs.extend(
            binding.ref
            for binding in request.skill_bindings
            if not binding.chapter_indexes or chapter_index in binding.chapter_indexes
        )
        for ref in request.skill_candidate_refs:
            if ref in refs:
                continue
            definition = skills.load_snapshot(project, ref)
            if definition.tags and any(tag in evidence.casefold() for tag in definition.tags):
                refs.append(ref)
    parts = []
    used = []
    remaining = _MAX_CONTEXT
    for ref in refs:
        definition = skills.load_snapshot(project, ref)
        if definition.stages and stage not in definition.stages:
            continue
        if remaining < 120:
            break
        body = definition.files["SKILL.md"].decode("utf-8-sig")
        body = re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", body, count=1, flags=re.S)
        header = SKILL_CONTEXT_HEADER.format(name=ref.name)
        budget = min(remaining - len(header), 2200)
        if budget <= 0:
            break
        text = header + body[:budget]
        for resource in definition.resources:
            extra = f"\n参考 {resource}：\n" + definition.files[resource].decode("utf-8")
            if len(text) + len(extra) <= remaining:
                text += extra
        parts.append(text)
        used.append(ref)
        remaining -= len(text) + 2
    return tuple(used), "\n\n".join(parts)
