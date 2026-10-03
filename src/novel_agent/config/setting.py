"""Local limits for conversational context and CLI Session history."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # The short model context is separate from the user-visible conversation log.
    context_history_max_messages: int = 12
    context_history_max_chars: int = 600
    session_history_max_messages: int = 200
    session_history_max_chars: int = 4000
    session_history_display_messages: int = 20
    draft_max_bytes: int = 2_000_000
    generation_context_max_chars: int = 18_000

    def __post_init__(self) -> None:
        if min(
            self.context_history_max_messages,
            self.context_history_max_chars,
            self.session_history_max_messages,
            self.session_history_max_chars,
            self.session_history_display_messages,
            self.draft_max_bytes,
            self.generation_context_max_chars,
        ) < 1:
            raise ValueError("History limits must be positive")
        if self.session_history_display_messages > self.session_history_max_messages:
            raise ValueError("History display limit cannot exceed retention limit")


DEFAULT_SETTINGS = Settings()
