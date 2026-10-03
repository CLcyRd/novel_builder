"""Durable file primitives and canonical boundaries for transaction storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from novel_agent.domain.errors import RecoveryConflict


def digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def read_bytes_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def sync_directory(path: Path) -> None:
    # Windows does not expose directory fsync through Python. File contents are
    # flushed on every platform; directory entries are also flushed on POSIX.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".novel-tmp-", delete=False)
    temp = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def safe_transaction_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if not path.parts or path.is_absolute() or path.drive or ".." in path.parts:
        raise RecoveryConflict("Unsafe path in Commit storage")
    root = root.resolve(strict=True)
    target = root / path
    for parent in (target, *target.parents):
        if parent == root:
            break
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise RecoveryConflict("Linked path in Commit storage")
    if not target.resolve(strict=False).is_relative_to(root):
        raise RecoveryConflict("Commit storage path escapes Project")
    return target


def safe_child(root: Path, path: Path) -> Path:
    return safe_transaction_path(root, path.relative_to(root).as_posix())


def make_directory(root: Path, path: Path) -> None:
    safe_child(root, path)
    if path.exists():
        if not path.is_dir():
            raise RecoveryConflict(f"Commit directory is not a directory: {path.name}")
        return
    make_directory(root, path.parent)
    path.mkdir()
    sync_directory(path.parent)


def move_directory(root: Path, source: Path, destination: Path) -> None:
    safe_child(root, source)
    safe_child(root, destination)
    if destination.exists():
        raise RecoveryConflict(f"Duplicate Commit storage entry: {destination.name}")
    source.rename(destination)
    sync_directory(destination.parent)
    if source.parent != destination.parent:
        sync_directory(source.parent)
