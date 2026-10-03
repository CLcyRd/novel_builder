"""Read-only, Project-scoped Skill catalog and immutable Workflow snapshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from novel_agent.domain.errors import InvalidChange, UnsafeProjectPath
from novel_agent.domain.models import Project, SessionMetadata
from novel_agent.domain.skill import SkillRef
from novel_agent.infrastructure.filesystem.transaction import atomic_write

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_MAX_FILE = 32_000
_MAX_SKILL = 100_000
_WORKFLOWS = {
    "CREATE_CHAPTER", "CONTINUE_CHAPTER", "BACKFILL_CHAPTER", "REWRITE_CHAPTER",
    "MULTI_CHAPTER", "IMPORT_NOVEL",
}
_STAGES = {"summary", "outline", "manuscript", "review"}


@dataclass(frozen=True)
class SkillDefinition:
    ref: SkillRef
    description: str
    files: dict[str, bytes]
    activation: str = "manual"
    workflows: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _read_skill(
    directory: Path, source: str, boundary: Path, *, expected_name: str | None = None
) -> SkillDefinition:
    name = expected_name or directory.name
    if _is_link(directory) or not _NAME.fullmatch(name):
        raise InvalidChange(f"Invalid Skill directory: {directory.name}")
    files = {}
    for path in sorted(directory.rglob("*")):
        if _is_link(path) or not path.resolve().is_relative_to(boundary.resolve()):
            raise UnsafeProjectPath("Skill path escapes its library or uses a link")
        if path.is_dir():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative != "SKILL.md" and not (
            relative.startswith(("references/", "assets/")) and path.suffix in {".md", ".txt"}
        ):
            raise InvalidChange(f"Unsupported Skill file: {relative}")
        data = path.read_bytes()
        if len(data) > _MAX_FILE:
            raise InvalidChange(f"Skill file is too large: {relative}")
        data.decode("utf-8")
        files[relative] = data
    if "SKILL.md" not in files or sum(map(len, files.values())) > _MAX_SKILL:
        raise InvalidChange(f"Missing or oversized SKILL.md: {directory.name}")
    body = files["SKILL.md"].decode("utf-8-sig").replace("\r\n", "\n")
    match = re.match(r"\A---\n(.*?)\n---\n", body, re.S)
    if not match:
        raise InvalidChange(f"Skill frontmatter is missing: {directory.name}")
    fields = dict(
        re.findall(
            r"^(name|description|activation|workflows|stages|tags|resources):[ \t]*(.*)$",
            match.group(1), re.M,
        )
    )
    if fields.get("name") != name or not fields.get("description"):
        raise InvalidChange(f"Skill name/description is invalid: {directory.name}")
    activation = fields.get("activation", "manual").strip().lower()
    workflows = tuple(item.upper() for item in _csv(fields.get("workflows", "")))
    stages = tuple(item.lower() for item in _csv(fields.get("stages", "")))
    tags = tuple(item.casefold() for item in _csv(fields.get("tags", "")))
    resources = _csv(fields.get("resources", ""))
    if activation not in {"manual", "auto"}:
        raise InvalidChange(f"Invalid Skill activation: {name}")
    if set(workflows) - _WORKFLOWS or set(stages) - _STAGES:
        raise InvalidChange(f"Invalid Skill workflow/stage metadata: {name}")
    if any(
        resource not in files or not resource.startswith("references/")
        for resource in resources
    ):
        raise InvalidChange(f"Invalid Skill resource reference: {name}")
    digest = hashlib.sha256()
    for relative, data in sorted(files.items()):
        digest.update(relative.encode("utf-8") + b"\0" + data + b"\0")
    return SkillDefinition(
        SkillRef(name=name, source=source, content_hash=digest.hexdigest()),
        fields["description"].strip(),
        files,
        activation,
        workflows,
        stages,
        tags,
        resources,
    )


class SkillService:
    def __init__(self, builtin_root: Path | None = None) -> None:
        self.builtin_root = builtin_root or Path(__file__).resolve().parents[1] / "builtin_skills"

    def list(self, project: Project) -> tuple[SkillDefinition, ...]:
        found = {}
        for source, root in (("builtin", self.builtin_root), ("project", project.root / "skills")):
            if not root.exists():
                continue
            if _is_link(root) or not root.is_dir():
                raise UnsafeProjectPath(f"Unsafe Skill library: {root}")
            for path in sorted(root.iterdir()):
                if not path.is_dir() or _is_link(path):
                    raise InvalidChange(f"Invalid Skill entry: {path.name}")
                skill = _read_skill(path, source, root)
                found[skill.ref.name] = skill  # Project overrides builtin.
        return tuple(found[name] for name in sorted(found))

    def get(self, project: Project, name: str) -> SkillDefinition:
        try:
            return next(skill for skill in self.list(project) if skill.ref.name == name)
        except StopIteration as exc:
            raise InvalidChange(f"Unknown Skill: {name}") from exc

    def enabled(self, project: Project, session: SessionMetadata) -> tuple[SkillDefinition, ...]:
        return tuple(
            skill
            for skill in self.list(project)
            if skill.ref.name in session.enabled_skills
            or (skill.ref.source == "builtin" and skill.ref.name not in session.disabled_skills)
        )

    def snapshot(self, project: Project, skill: SkillDefinition) -> SkillRef:
        library = project.root / ".novel-agent" / "skill-snapshots"
        root = library / skill.ref.content_hash
        if _is_link(library) or _is_link(root):
            raise UnsafeProjectPath("Skill snapshot directory uses a link")
        for relative, content in skill.files.items():
            target = root / relative
            linked_parent = any(
                _is_link(parent)
                for parent in target.parents
                if parent.is_relative_to(project.root)
            )
            if _is_link(target) or linked_parent:
                raise UnsafeProjectPath("Skill snapshot path uses a link")
            if target.exists() and target.read_bytes() != content:
                raise InvalidChange("Skill snapshot hash collision or corruption")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, content)
        return skill.ref

    def load_snapshot(self, project: Project, ref: SkillRef) -> SkillDefinition:
        root = project.root / ".novel-agent" / "skill-snapshots"
        if not re.fullmatch(r"[a-f0-9]{64}", ref.content_hash):
            raise InvalidChange("Invalid Skill snapshot reference")
        if _is_link(root) or not root.is_dir():
            raise InvalidChange("Skill snapshot directory is missing or unsafe")
        skill = _read_skill(root / ref.content_hash, ref.source, root, expected_name=ref.name)
        if skill.ref.name != ref.name or skill.ref.content_hash != ref.content_hash:
            raise InvalidChange("Skill snapshot changed")
        return skill

    @staticmethod
    def virtual_files(skills: tuple[SkillDefinition, ...]) -> dict:
        from deepagents.backends.utils import create_file_data

        return {
            f"/skills/{skill.ref.name}/{relative}": create_file_data(data.decode("utf-8"))
            for skill in skills
            for relative, data in skill.files.items()
        }
