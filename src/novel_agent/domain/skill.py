"""Immutable reference to the exact Skill version used for a task."""

from pydantic import BaseModel, ConfigDict, Field


class SkillRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    source: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
