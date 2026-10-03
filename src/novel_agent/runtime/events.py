"""User-visible execution events separate from assistant text."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RuntimeEvent:
    kind: Literal[
        "Thinking",
        "Reading",
        "Searching",
        "Calling Tool",
        "Delegating",
        "Generating",
        "Waiting",
        "Committing",
        "Retrying",
        "Completed",
        "Cancelled",
        "Failed",
    ]
    detail: str = ""
