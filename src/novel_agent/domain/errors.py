"""Errors that callers can handle without inspecting filesystem exceptions."""


class ProjectError(Exception):
    """Base error for Project operations."""


class InvalidProjectName(ProjectError, ValueError):
    """A user-facing Project or Session name is invalid."""


class ProjectAlreadyExists(ProjectError, FileExistsError):
    """The requested Project directory already exists."""


class ProjectNotFound(ProjectError, FileNotFoundError):
    """The requested Project does not exist."""


class InvalidProject(ProjectError):
    """Project data or directory layout is invalid."""


class ChapterNotFound(ProjectError, LookupError):
    """A chapter ID is not registered in this Project."""


class InvalidChapter(ProjectError, ValueError):
    """A formal chapter does not match the documented format."""


class UnsafeProjectPath(ProjectError, ValueError):
    """A path escapes or accesses protected parts of the Project."""


class InvalidAttachment(ProjectError, ValueError):
    """An attachment is missing, unreadable, or has an unsupported type."""


class ArtifactNotFound(ProjectError, LookupError):
    """An Artifact ID or exact revision does not exist."""


class InvalidArtifact(ProjectError, ValueError):
    """An Artifact cannot be used for this operation."""


class InvalidChange(ProjectError, ValueError):
    """A deterministic Project change is invalid."""


class DecisionRequired(ProjectError):
    """A stale dependency, target, or ambiguous text anchor needs user choice."""

    def __init__(self, reason: str, paths: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.paths = paths


class RecoveryConflict(ProjectError):
    """A pending Commit cannot safely finish because a file changed externally."""


class GenerationBlocked(DecisionRequired):
    """Required generation evidence is incomplete, conflicting, or over budget."""
