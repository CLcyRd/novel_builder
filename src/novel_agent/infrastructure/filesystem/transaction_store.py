"""Versioned pending journals, permanent Commit receipts and safe cleanup."""

from __future__ import annotations

import json
import re
import warnings
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from novel_agent.domain.errors import RecoveryConflict
from novel_agent.infrastructure.filesystem.transaction_io import (
    atomic_write,
    json_bytes,
    make_directory,
    move_directory,
    safe_child,
    safe_transaction_path,
    sync_directory,
)

_AREAS = ("staging", "pending", "receipts", "garbage")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


class TransactionCleanupWarning(UserWarning):
    """A completed transaction needs maintenance; formal writes succeeded."""


def _warn(message: str, collector: list[str] | None) -> None:
    if collector is None:
        warnings.warn(message, TransactionCleanupWarning, stacklevel=3)
    else:
        collector.append(message)


def _read_json(root: Path, path: Path) -> dict:
    safe_child(root, path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryConflict(f"Unreadable Commit data: {path.name}") from exc
    if not isinstance(value, dict):
        raise RecoveryConflict(f"Invalid Commit data: {path.name}")
    return value


def _validate_record(value: dict, commit_id: UUID, *, receipt: bool = False) -> dict:
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] not in ({2} if receipt else {1, 2})
        or value.get("commit_id") != str(commit_id)
        or not isinstance(value.get("fingerprint"), str)
        or not _HASH.fullmatch(value["fingerprint"])
        or not isinstance(value.get("result"), dict)
        or not isinstance(value.get("status"), str)
        or value["status"] not in ({"COMPLETED"} if receipt else {
            "PREPARED", "APPLYING", "COMPLETED"
        })
    ):
        raise RecoveryConflict(f"Invalid Commit record: {commit_id}")
    kind = value.get("kind")
    if kind is None and not receipt:
        kind = (
            "ARTIFACT_REVISION"
            if value["result"].get("kind") == "ARTIFACT_REVISION"
            else "COMMIT"
        )
    if (
        not isinstance(kind, str)
        or kind not in {"COMMIT", "ARTIFACT_REVISION"}
        or (receipt and kind != "COMMIT")
    ):
        raise RecoveryConflict(f"Invalid Commit kind: {commit_id}")
    value["kind"] = kind
    if not receipt and not isinstance(value.get("entries"), list):
        raise RecoveryConflict(f"Missing Commit entries: {commit_id}")
    return value


def _commit_id(name: str) -> UUID:
    try:
        value = UUID(name)
    except ValueError as exc:
        raise RecoveryConflict(f"Invalid Commit directory: {name}") from exc
    if str(value) != name:
        raise RecoveryConflict(f"Noncanonical Commit directory: {name}")
    return value


def transaction_root(root: Path) -> Path:
    return safe_transaction_path(root, ".novel-agent/commits")


def receipt_path(root: Path, commit_id: UUID) -> Path:
    return safe_transaction_path(
        root, f".novel-agent/commits/receipts/{str(commit_id)[:2]}/{commit_id}.json"
    )


def manifest_path(root: Path, commit_id: UUID) -> Path:
    return safe_transaction_path(root, f".novel-agent/commits/pending/{commit_id}/manifest.json")


def ensure_layout_unlocked(root: Path) -> Path:
    """Scan legacy history once; a READY marker keeps it out of the read path."""
    transactions = transaction_root(root)
    make_directory(root, transactions)
    marker = safe_child(root, transactions / "layout.json")
    layout = _read_json(root, marker) if marker.exists() else None
    if layout is not None and (
        layout.get("schema_version") != 2
        or not isinstance(layout.get("status"), str)
        or layout["status"] not in {"MIGRATING", "READY"}
    ):
        raise RecoveryConflict("Unsupported Commit layout version or migration state")
    for area in _AREAS:
        path = safe_child(root, transactions / area)
        if layout is not None and layout["status"] == "READY":
            if not path.is_dir():
                raise RecoveryConflict(f"Missing Commit directory: {area}")
        else:
            make_directory(root, path)
    if layout is not None and layout["status"] == "READY":
        return transactions
    atomic_write(marker, json_bytes({"schema_version": 2, "status": "MIGRATING"}))
    for directory in sorted(transactions.iterdir()):
        if directory.name in {*_AREAS, "layout.json"}:
            continue
        safe_child(root, directory)
        if directory.name.startswith(".novel-tmp-") and directory.is_file():
            move_directory(root, directory, transactions / "staging" / directory.name)
            continue
        if not directory.is_dir():
            raise RecoveryConflict(f"Unexpected legacy Commit entry: {directory.name}")
        if directory.name.startswith(".staging-"):
            destination = transactions / "staging" / directory.name
        else:
            commit_id = _commit_id(directory.name)
            _validate_record(_read_json(root, directory / "manifest.json"), commit_id)
            destination = transactions / "pending" / directory.name
        move_directory(root, directory, destination)
    atomic_write(marker, json_bytes({"schema_version": 2, "status": "READY"}))
    return transactions


def load_manifest(root: Path, directory: Path) -> dict:
    safe_child(root, directory)
    if not directory.is_dir():
        raise RecoveryConflict(f"Invalid Commit directory: {directory.name}")
    return _validate_record(
        _read_json(root, directory / "manifest.json"), _commit_id(directory.name)
    )


def _same_result(first: dict, second: dict) -> bool:
    return all(first.get(key) == second.get(key) for key in (
        "commit_id", "kind", "fingerprint", "result"
    ))


def get_manifest(root: Path, commit_id: UUID) -> dict | None:
    ensure_layout_unlocked(root)
    pending_path = manifest_path(root, commit_id)
    pending = load_manifest(root, pending_path.parent) if pending_path.parent.exists() else None
    receipt_file = receipt_path(root, commit_id)
    receipt = (
        _validate_record(_read_json(root, receipt_file), commit_id, receipt=True)
        if receipt_file.exists()
        else None
    )
    if pending and receipt and (
        pending["status"] != "COMPLETED" or not _same_result(pending, receipt)
    ):
        raise RecoveryConflict(f"Commit journal and receipt disagree: {commit_id}")
    return pending or receipt


def _write_receipt(root: Path, manifest: dict) -> None:
    commit_id = UUID(manifest["commit_id"])
    path = receipt_path(root, commit_id)
    if path.exists():
        receipt = _validate_record(_read_json(root, path), commit_id, receipt=True)
        if not _same_result(manifest, receipt):
            raise RecoveryConflict(f"Commit journal and receipt disagree: {commit_id}")
        return
    make_directory(root, path.parent)
    atomic_write(path, json_bytes({
        "schema_version": 2,
        "commit_id": str(commit_id),
        "kind": "COMMIT",
        "fingerprint": manifest["fingerprint"],
        "status": "COMPLETED",
        "result": manifest["result"],
        "completed_at": manifest.get("completed_at") or datetime.now(UTC).isoformat(),
    }))


def _tree(root: Path, directory: Path) -> list[Path]:
    """Check every resolved path before any recursive removal; never follow links."""
    safe_child(root, directory)
    paths = []
    for child in sorted(directory.iterdir()):
        safe_child(root, child)
        if child.is_dir():
            paths.extend(_tree(root, child))
        elif not child.is_file():
            raise RecoveryConflict(f"Unexpected Commit cleanup entry: {child.name}")
        paths.append(child)
    return paths


def _remove_tree(root: Path, directory: Path) -> None:
    transactions = transaction_root(root)
    safe_child(root, directory)
    if directory.parent not in {transactions / "garbage", transactions / "staging"}:
        raise RecoveryConflict("Commit cleanup target is outside its designated directory")
    if directory.is_file() and directory.name.startswith(".novel-tmp-"):
        directory.unlink()
    else:
        # The complete tree is validated before deleting the first file.
        paths = _tree(root, directory)
        for path in paths:
            safe_child(root, path)
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        directory.rmdir()
    sync_directory(directory.parent)


def finalize_completed_unlocked(
    root: Path, directory: Path, manifest: dict, collector: list[str] | None = None
) -> None:
    if manifest["status"] != "COMPLETED":
        raise RecoveryConflict("Cannot clean an unfinished Commit")
    # Detect a conflicting duplicate receipt even for internal transactions.
    get_manifest(root, UUID(manifest["commit_id"]))
    try:
        if manifest["kind"] == "COMMIT":
            _write_receipt(root, manifest)
        garbage = transaction_root(root) / "garbage" / directory.name
        move_directory(root, directory, garbage)
        _remove_tree(root, garbage)
    except OSError as exc:
        _warn(f"Commit {directory.name} completed; cleanup pending: {exc}", collector)


def cleanup_storage_unlocked(root: Path, collector: list[str]) -> None:
    transactions = ensure_layout_unlocked(root)
    for area in ("garbage", "staging"):
        for path in sorted((transactions / area).iterdir()):
            safe_child(root, path)
            if area == "garbage":
                _commit_id(path.name)
                if (path / "manifest.json").exists():
                    manifest = load_manifest(root, path)
                    if manifest["status"] != "COMPLETED":
                        raise RecoveryConflict("Unfinished Commit found in cleanup directory")
                    if manifest["kind"] == "COMMIT":
                        receipt = get_manifest(root, UUID(path.name))
                        if receipt is None or not _same_result(manifest, receipt):
                            raise RecoveryConflict("Cannot clean Commit without matching receipt")
            elif not path.name.startswith((".staging-", ".novel-tmp-")):
                raise RecoveryConflict(f"Unexpected staging entry: {path.name}")
            try:
                _remove_tree(root, path)
            except OSError as exc:
                _warn(f"Transaction cleanup pending for {path.name}: {exc}", collector)
    # A hard exit during receipt creation can leave its unpublished temp file.
    # This history traversal is confined to explicit maintenance.
    for path in _tree(root, transactions / "receipts"):
        if path.is_file() and path.name.startswith(".novel-tmp-"):
            try:
                safe_child(root, path).unlink()
                sync_directory(path.parent)
            except OSError as exc:
                _warn(f"Receipt temp cleanup pending for {path.name}: {exc}", collector)


def storage_stats_unlocked(root: Path) -> dict[str, int]:
    transactions = ensure_layout_unlocked(root)
    paths = _tree(root, transactions)
    files = [path for path in paths if path.is_file()]
    return {
        "pending_transactions": len(tuple((transactions / "pending").iterdir())),
        "receipt_count": sum(path.suffix == ".json" for path in files
                             if path.is_relative_to(transactions / "receipts")),
        "garbage_transactions": len(tuple((transactions / "garbage").iterdir())),
        "staging_entries": len(tuple((transactions / "staging").iterdir())),
        "disk_bytes": sum(path.stat().st_size for path in files),
    }
