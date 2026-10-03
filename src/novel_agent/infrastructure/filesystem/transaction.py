"""Project-scoped lock and forward recovery for multi-file Commits."""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from novel_agent.domain.errors import DecisionRequired, InvalidChange, RecoveryConflict
from novel_agent.domain.models import TransactionMaintenanceReport
from novel_agent.infrastructure.filesystem.transaction_io import atomic_write as atomic_write
from novel_agent.infrastructure.filesystem.transaction_io import digest as digest
from novel_agent.infrastructure.filesystem.transaction_io import json_bytes as json_bytes
from novel_agent.infrastructure.filesystem.transaction_io import (
    move_directory,
    safe_child,
    sync_directory,
)
from novel_agent.infrastructure.filesystem.transaction_io import (
    read_bytes_or_none as read_bytes_or_none,
)
from novel_agent.infrastructure.filesystem.transaction_io import (
    safe_transaction_path as _safe_target,
)
from novel_agent.infrastructure.filesystem.transaction_store import (
    cleanup_storage_unlocked,
    ensure_layout_unlocked,
    finalize_completed_unlocked,
    load_manifest,
    manifest_path,
    storage_stats_unlocked,
)
from novel_agent.infrastructure.filesystem.transaction_store import get_manifest as get_manifest

_locks_guard = threading.Lock()
_thread_locks: dict[str, threading.RLock] = {}
_lock_depth = threading.local()


@contextmanager
def project_lock(root: Path) -> Iterator[None]:
    """One writer/reader boundary shared by all application Project operations."""
    key = str(root.resolve(strict=True)).casefold()
    with _locks_guard:
        local_lock = _thread_locks.setdefault(key, threading.RLock())
    with local_lock:
        depths = getattr(_lock_depth, "values", None)
        if depths is None:
            depths = _lock_depth.values = {}
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        depths[key] = 1
        try:
            with _project_file_lock(root):
                yield
        finally:
            del depths[key]


@contextmanager
def _project_file_lock(root: Path) -> Iterator[None]:
    internal = root / ".novel-agent"
    if not internal.is_dir():
        raise RecoveryConflict("Project internal directory is missing")
    lock_path = internal / "commit.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def recover_pending_unlocked(root: Path, *, cleanup_warnings: list[str] | None = None) -> None:
    transactions = ensure_layout_unlocked(root)
    for directory in sorted((transactions / "pending").iterdir()):
        manifest = load_manifest(root, directory)
        # An impossible receipt/pending combination must stop before target writes.
        get_manifest(root, UUID(manifest["commit_id"]))
        if manifest["status"] != "COMPLETED":
            _apply_manifest_unlocked(root, directory, manifest)
        finalize_completed_unlocked(root, directory, manifest, cleanup_warnings)


def maintain_transactions_unlocked(root: Path) -> TransactionMaintenanceReport:
    """Explicit maintenance may scan history; ordinary recovery never does."""
    before = storage_stats_unlocked(root)["disk_bytes"]
    cleanup_warnings: list[str] = []
    recover_pending_unlocked(root, cleanup_warnings=cleanup_warnings)
    cleanup_storage_unlocked(root, cleanup_warnings)
    stats = storage_stats_unlocked(root)
    return TransactionMaintenanceReport(
        **stats,
        reclaimed_bytes=max(0, before - stats["disk_bytes"]),
        warnings=tuple(cleanup_warnings),
    )


def _apply_manifest_unlocked(
    root: Path,
    directory: Path,
    manifest: dict,
    failure_hook: Callable[[int, str], None] | None = None,
) -> None:
    entries = manifest["entries"]
    # Validate all current files and all staged payloads before changing anything.
    for index, entry in enumerate(entries):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or any(key not in entry for key in ("before", "after"))
            or any(
                entry[key] is not None
                and (not isinstance(entry[key], str) or len(entry[key]) != 64)
                for key in ("before", "after")
            )
        ):
            raise RecoveryConflict("Invalid Commit file entry")
        target = _safe_target(root, entry["path"])
        current_hash = digest(read_bytes_or_none(target))
        if current_hash not in {entry["before"], entry["after"]}:
            raise RecoveryConflict(f"External modification during Commit: {entry['path']}")
        if entry["after"] is not None:
            staged = directory / "staged" / f"{index:04d}.bin"
            safe_child(root, staged)
            if digest(read_bytes_or_none(staged)) != entry["after"]:
                raise RecoveryConflict(f"Missing or damaged staged file: {entry['path']}")
    manifest["status"] = "APPLYING"
    atomic_write(directory / "manifest.json", json_bytes(manifest))
    for index, entry in enumerate(entries):
        target = _safe_target(root, entry["path"])
        current_hash = digest(read_bytes_or_none(target))
        if current_hash not in {entry["before"], entry["after"]}:
            raise RecoveryConflict(f"External modification during Commit: {entry['path']}")
        if current_hash != entry["after"]:
            if entry["after"] is None:
                target.unlink(missing_ok=True)
                sync_directory(target.parent)
            else:
                staged = directory / "staged" / f"{index:04d}.bin"
                safe_child(root, staged)
                atomic_write(target, staged.read_bytes())
        if failure_hook is not None:
            failure_hook(index, entry["path"])
    manifest["status"] = "COMPLETED"
    manifest["completed_at"] = datetime.now(UTC).isoformat()
    atomic_write(directory / "manifest.json", json_bytes(manifest))


def commit_files_unlocked(
    root: Path,
    commit_id: UUID,
    fingerprint: str,
    writes: dict[str, bytes | None],
    baselines: dict[str, str | None],
    result: dict,
    failure_hook: Callable[[int, str], None] | None = None,
    *,
    kind: str = "COMMIT",
) -> dict:
    if kind not in {"COMMIT", "ARTIFACT_REVISION"}:
        raise InvalidChange("Unknown transaction kind")
    existing = get_manifest(root, commit_id)
    if existing is not None:
        if existing.get("fingerprint") != fingerprint or existing["kind"] != kind:
            raise InvalidChange("Commit ID was already used for a different plan")
        if existing.get("status") != "COMPLETED":
            recover_pending_unlocked(root)
            # Internal Artifact transactions have no externally replayable ID
            # and therefore no receipt after recovery. Their saved result suffices.
        return existing["result"]
    transactions = ensure_layout_unlocked(root)
    directory = manifest_path(root, commit_id).parent
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=transactions / "staging") as tmp:
        preparing = Path(tmp)
        staged = preparing / "staged"
        staged.mkdir()
        entries = []
        for index, (relative, after) in enumerate(sorted(writes.items())):
            target = _safe_target(root, relative)
            before = digest(read_bytes_or_none(target))
            if before != baselines[relative]:
                raise DecisionRequired("Commit target changed before staging", (relative,))
            after_hash = digest(after)
            if after is not None:
                atomic_write(staged / f"{index:04d}.bin", after)
            entries.append({"path": relative, "before": before, "after": after_hash})
        manifest = {
            "schema_version": 2,
            "commit_id": str(commit_id),
            "kind": kind,
            "status": "PREPARED",
            "fingerprint": fingerprint,
            "entries": entries,
            "result": result,
        }
        atomic_write(preparing / "manifest.json", json_bytes(manifest))
        move_directory(root, preparing, directory)
    _apply_manifest_unlocked(root, directory, manifest, failure_hook)
    finalize_completed_unlocked(root, directory, manifest)
    return result
