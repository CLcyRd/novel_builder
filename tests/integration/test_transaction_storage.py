"""Transaction retention, one-time migration and process-crash boundaries."""

import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from novel_agent.application import (
    build_artifact_service,
    build_commit_service,
    build_project_service,
)
from novel_agent.cli.shell import NovelCLI
from novel_agent.domain.artifact import ArtifactType
from novel_agent.domain.change import SetProjectText
from novel_agent.domain.errors import InvalidChange, RecoveryConflict
from novel_agent.infrastructure.filesystem import transaction, transaction_store
from novel_agent.infrastructure.filesystem.artifact_repository import FileArtifactRepository
from novel_agent.infrastructure.filesystem.project_repository import FileProjectRepository
from novel_agent.infrastructure.filesystem.transaction import get_manifest, json_bytes
from novel_agent.infrastructure.filesystem.transaction_store import (
    TransactionCleanupWarning,
    manifest_path,
    receipt_path,
)
from novel_agent.infrastructure.models.gateway import ModelDefinition, ModelGateway
from novel_agent.services.artifact import ArtifactService
from novel_agent.services.commit import CommitResult, CommitService


@pytest.fixture
def project(tmp_path):
    return build_project_service().create_project(tmp_path, "Storage", "Main")


def _storage(project):
    return project.root / ".novel-agent/commits"


def _legacy(project, *, completed=True, text="# Legacy\n"):
    changes = (SetProjectText(text=text),)
    prepared = build_commit_service().prepare(project, changes)
    result = CommitResult(
        commit_id=prepared.commit_id,
        project_id=project.project_id,
        changed_files=("PROJECT.md",),
        changed_chapters=(),
        committed_artifacts=(),
    )
    manifest = {
        "schema_version": 1,
        "commit_id": str(prepared.commit_id),
        "status": "COMPLETED" if completed else "PREPARED",
        "fingerprint": prepared.fingerprint,
        "entries": [{
            "path": "PROJECT.md", "before": prepared.baselines["PROJECT.md"],
            "after": transaction.digest(text.encode()),
        }],
        "result": result.model_dump(mode="json"),
    }
    directory = _storage(project) / str(prepared.commit_id)
    (directory / "staged").mkdir(parents=True)
    (directory / "staged/0000.bin").write_bytes(text.encode())
    (directory / "manifest.json").write_bytes(json_bytes(manifest))
    if completed:
        (project.root / "PROJECT.md").write_bytes(text.encode("utf-8"))
    (_storage(project) / "layout.json").unlink(missing_ok=True)
    return prepared.commit_id, changes, result


def test_completed_commit_releases_payload_but_preserves_permanent_idempotency(project):
    commits = build_commit_service()
    commit_id = uuid4()
    changes = (SetProjectText(text="# Formal\n" + "正文" * 10_000),)
    result = commits.commit(project, changes, commit_id=commit_id)
    receipt = get_manifest(project.root, commit_id)
    assert receipt["result"] == result.model_dump(mode="json")
    assert receipt["kind"] == "COMMIT" and "entries" not in receipt
    assert receipt_path(project.root, commit_id).stat().st_size < 2000
    assert not tuple(_storage(project).rglob("*.bin"))
    assert not tuple((_storage(project) / "pending").iterdir())
    assert commits.commit(project, changes, commit_id=commit_id) == result
    with pytest.raises(InvalidChange, match="different changes"):
        commits.commit(project, (SetProjectText(text="different"),), commit_id=commit_id)


def test_internal_revision_journals_are_reclaimed_without_losing_artifact_history(project):
    artifacts = build_artifact_service()
    first = artifacts.create(
        project, project.metadata.initial_session_id, ArtifactType.ANALYSIS, "PROJECT", "first"
    )
    latest = first
    for i in range(8):
        latest = artifacts.revise(project, latest.ref, f"revision {i}")
    assert artifacts.get(project, first.ref).content == "first"
    assert artifacts.get(project, latest.ref).revision == 9
    report = build_project_service().maintain_transactions(project)
    assert report.receipt_count == report.pending_transactions == 0
    assert report.garbage_transactions == report.staging_entries == 0
    assert not tuple(_storage(project).rglob("*.bin"))


def test_completed_legacy_history_migrates_without_replaying_later_edits(project):
    commit_id, changes, result = _legacy(project)
    (project.root / "PROJECT.md").write_bytes(b"# Later edit\n")
    orphan = _storage(project) / ".staging-orphan"
    orphan.mkdir()
    (orphan / "unused.bin").write_bytes(b"unpublished")
    projects = build_project_service()
    reopened = projects.open_project(project.root)
    assert projects.read_project_text(reopened) == "# Later edit\n"
    assert build_commit_service().commit(reopened, changes, commit_id=commit_id) == result
    assert not (_storage(project) / str(commit_id)).exists()
    assert json.loads((_storage(project) / "layout.json").read_text())["status"] == "READY"
    report = projects.maintain_transactions(reopened)
    assert report.reclaimed_bytes >= len(b"unpublished")
    assert report.receipt_count == 1 and report.staging_entries == 0


def test_prepared_legacy_transaction_recovers_before_receipt_and_cleanup(project):
    commit_id, changes, result = _legacy(project, completed=False)
    reopened = build_project_service().open_project(project.root)
    assert build_project_service().read_project_text(reopened) == "# Legacy\n"
    assert build_commit_service().commit(reopened, changes, commit_id=commit_id) == result
    assert not manifest_path(project.root, commit_id).exists()


def test_legacy_artifact_transaction_recovers_and_does_not_keep_receipt(project):
    def fail(index, _path):
        if index == 0:
            raise RuntimeError("interrupted revision")

    artifacts = ArtifactService(FileArtifactRepository(fail))
    with pytest.raises(RuntimeError):
        artifacts.create(
            project, project.metadata.initial_session_id, ArtifactType.ANALYSIS, "PROJECT", "saved"
        )
    directory = next((_storage(project) / "pending").iterdir())
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["schema_version"] = 1
    manifest.pop("kind")
    (directory / "manifest.json").write_bytes(json_bytes(manifest))
    directory.rename(_storage(project) / directory.name)
    (_storage(project) / "layout.json").unlink()
    reopened = build_project_service().open_project(project.root)
    artifacts = build_artifact_service()
    index = artifacts.list(reopened)[0]
    assert artifacts.get_by_id(reopened, index.artifact_id, 1).content == "saved"
    assert get_manifest(reopened.root, UUID(manifest["commit_id"])) is None
    assert build_project_service().maintain_transactions(reopened).receipt_count == 0


_CRASH_SCRIPT = """
import json, os, sys
from pathlib import Path
from uuid import UUID
from novel_agent.domain.change import SetProjectText
from novel_agent.infrastructure.filesystem import transaction, transaction_store
from novel_agent.infrastructure.filesystem.artifact_repository import FileArtifactRepository
from novel_agent.infrastructure.filesystem.project_repository import FileProjectRepository
from novel_agent.services.commit import CommitService

root, commit_id, phase = Path(sys.argv[1]), UUID(sys.argv[2]), sys.argv[3]
project = FileProjectRepository().open(root)
if phase in {'before_publish', 'after_publish'}:
    original = transaction.move_directory
    def move(root, source, destination):
        publishing = destination.parent.name == 'pending'
        if publishing and phase == 'before_publish':
            os._exit(73)
        original(root, source, destination)
        if publishing and phase == 'after_publish':
            os._exit(73)
    transaction.move_directory = move
elif phase == 'after_complete':
    original = transaction.atomic_write
    def write(path, data):
        original(path, data)
        if path.name == 'manifest.json' and json.loads(data).get('status') == 'COMPLETED':
            os._exit(73)
    transaction.atomic_write = write
elif phase == 'after_receipt':
    original = transaction_store._write_receipt
    def receipt(root, manifest):
        original(root, manifest)
        os._exit(73)
    transaction_store._write_receipt = receipt
elif phase == 'after_garbage_move':
    original = transaction_store.move_directory
    def move(root, source, destination):
        original(root, source, destination)
        if destination.parent.name == 'garbage':
            os._exit(73)
    transaction_store.move_directory = move
elif phase == 'during_cleanup':
    def remove(root, directory):
        next((directory / 'staged').iterdir()).unlink()
        os._exit(73)
    transaction_store._remove_tree = remove
def hook(index, path):
    if phase == 'during_apply':
        os._exit(73)
CommitService(FileProjectRepository(), FileArtifactRepository(), hook).commit(
    project, (SetProjectText(text='# Recovered\\n'),), commit_id=commit_id,
)
"""


@pytest.mark.parametrize("phase", [
    "before_publish", "after_publish", "during_apply", "after_complete",
    "after_receipt", "after_garbage_move", "during_cleanup",
])
def test_new_process_recovers_each_publish_finalize_and_cleanup_boundary(project, phase):
    commit_id = uuid4()
    child = subprocess.run(
        [sys.executable, "-B", "-c", _CRASH_SCRIPT, str(project.root), str(commit_id), phase],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert child.returncode == 73, child.stderr
    projects = build_project_service()
    reopened = projects.open_project(project.root)
    expected = "# Storage\n" if phase == "before_publish" else "# Recovered\n"
    assert projects.read_project_text(reopened) == expected
    changes = (SetProjectText(text="# Recovered\n"),)
    result = build_commit_service().commit(reopened, changes, commit_id=commit_id)
    assert build_commit_service().commit(reopened, changes, commit_id=commit_id) == result
    report = projects.maintain_transactions(reopened)
    assert report.receipt_count == 1
    assert report.pending_transactions == report.garbage_transactions == report.staging_entries == 0
    assert projects.read_project_text(reopened) == "# Recovered\n"


def test_migration_can_resume_after_hard_exit_between_legacy_moves(project):
    first_id, first_changes, first_result = _legacy(project, text="# First\n")
    second_id, second_changes, second_result = _legacy(project, text="# Second\n")
    # prepare() in the second fixture may already migrate the first history entry;
    # move it back so this test starts with two legacy directories.
    first_receipt = receipt_path(project.root, first_id)
    if first_receipt.exists():
        first_record = json.loads(first_receipt.read_text())
        first_receipt.unlink()
        directory = _storage(project) / str(first_id)
        (directory / "staged").mkdir(parents=True)
        (directory / "staged/0000.bin").write_text("# First\n")
        (directory / "manifest.json").write_bytes(json_bytes({
            "schema_version": 1, "commit_id": str(first_id), "status": "COMPLETED",
            "fingerprint": first_record["fingerprint"],
            "entries": [], "result": first_result.model_dump(mode="json"),
        }))
        (_storage(project) / "layout.json").unlink(missing_ok=True)
    (project.root / "PROJECT.md").write_bytes(b"# Later\n")
    script = """
import os, sys
from pathlib import Path
from novel_agent.infrastructure.filesystem import transaction_store
from novel_agent.infrastructure.filesystem.project_repository import FileProjectRepository
original = transaction_store.move_directory
def move(root, source, destination):
    original(root, source, destination)
    if source.parent.name == 'commits' and destination.parent.name == 'pending':
        os._exit(73)
transaction_store.move_directory = move
FileProjectRepository().open(Path(sys.argv[1]))
"""
    child = subprocess.run(
        [sys.executable, "-B", "-c", script, str(project.root)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert child.returncode == 73, child.stderr
    assert json.loads((_storage(project) / "layout.json").read_text())["status"] == "MIGRATING"
    reopened = build_project_service().open_project(project.root)
    for commit_id, changes, result in (
        (first_id, first_changes, first_result), (second_id, second_changes, second_result)
    ):
        assert build_commit_service().commit(reopened, changes, commit_id=commit_id) == result
    assert build_project_service().read_project_text(reopened) == "# Later\n"


def test_recovery_stops_before_writes_if_pending_and_receipt_disagree(project, monkeypatch):
    def fail(index, _path):
        raise RuntimeError("pause")

    commit_id = uuid4()
    with pytest.raises(RuntimeError):
        CommitService(FileProjectRepository(), FileArtifactRepository(), fail).commit(
            project, (SetProjectText(text="# Partly applied\n"),), commit_id=commit_id
        )
    directory = manifest_path(project.root, commit_id).parent
    pending = json.loads((directory / "manifest.json").read_text())
    path = receipt_path(project.root, commit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes({
        **pending, "schema_version": 2, "status": "COMPLETED", "fingerprint": "0" * 64,
    }))
    def must_not_apply(*args, **kwargs):
        pytest.fail("conflicting receipt must be detected before replay")
    monkeypatch.setattr(transaction, "_apply_manifest_unlocked", must_not_apply)
    with pytest.raises(RecoveryConflict, match="disagree"):
        build_project_service().open_project(project.root)
    assert directory.exists() and (directory / "staged").is_dir()


def test_cleanup_failure_is_warning_and_explicit_maintenance_retries(project, monkeypatch):
    original = transaction_store._remove_tree
    def fail(*args):
        raise PermissionError("locked cleanup file")
    monkeypatch.setattr(transaction_store, "_remove_tree", fail)
    with pytest.warns(TransactionCleanupWarning, match="completed; cleanup pending"):
        result = build_commit_service().commit(project, (SetProjectText(text="# Done\n"),))
    assert result.status == "COMPLETED"
    assert build_project_service().read_project_text(project) == "# Done\n"
    report = build_project_service().maintain_transactions(project)
    assert report.garbage_transactions == 1 and report.warnings
    monkeypatch.setattr(transaction_store, "_remove_tree", original)
    report = build_project_service().maintain_transactions(project)
    assert not report.warnings and report.garbage_transactions == 0
    assert report.reclaimed_bytes > 0 and report.receipt_count == 1


def test_receipt_write_failure_keeps_completed_pending_and_later_edits(project, monkeypatch):
    original = transaction_store._write_receipt
    def fail(*args):
        raise OSError("receipt disk unavailable")
    monkeypatch.setattr(transaction_store, "_write_receipt", fail)
    changes = (SetProjectText(text="# Done\n"),)
    commit_id = uuid4()
    with pytest.warns(TransactionCleanupWarning):
        result = build_commit_service().commit(project, changes, commit_id=commit_id)
    assert manifest_path(project.root, commit_id).is_file()
    (project.root / "PROJECT.md").write_bytes(b"# Later\n")
    monkeypatch.setattr(transaction_store, "_write_receipt", original)
    reopened = build_project_service().open_project(project.root)
    assert build_project_service().read_project_text(reopened) == "# Later\n"
    assert build_commit_service().commit(reopened, changes, commit_id=commit_id) == result


def test_thousand_history_receipts_are_never_scanned_or_read_by_normal_operations(
    project, monkeypatch
):
    result = build_commit_service().commit(project, (SetProjectText(text="# History\n"),))
    base = get_manifest(project.root, result.commit_id)
    history = set()
    for _ in range(1000):
        commit_id = uuid4()
        path = receipt_path(project.root, commit_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json_bytes({
            **base, "commit_id": str(commit_id),
            "result": {**base["result"], "commit_id": str(commit_id)},
        }))
        history.add(path)
    read = Path.read_text
    iterate = Path.iterdir
    forbidden = {_storage(project), _storage(project) / "receipts", _storage(project) / "garbage"}
    def guard_read(path, *args, **kwargs):
        assert path not in history, "normal operation read a historical receipt"
        return read(path, *args, **kwargs)
    def guard_iterate(path):
        assert path not in forbidden and path.parent != _storage(project) / "receipts"
        return iterate(path)
    monkeypatch.setattr(Path, "read_text", guard_read)
    monkeypatch.setattr(Path, "iterdir", guard_iterate)
    projects = build_project_service()
    reopened = projects.open_project(project.root)
    assert projects.read_project_text(reopened) == "# History\n"
    artifacts = build_artifact_service()
    artifact = artifacts.create(
        reopened, reopened.metadata.initial_session_id, ArtifactType.ANALYSIS, "PROJECT", "draft"
    )
    artifacts.revise(reopened, artifact.ref, "revised")
    build_commit_service().commit(reopened, (SetProjectText(text="# Future\n"),))


def test_maintenance_preserves_outside_files_when_cleanup_tree_contains_link(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "protected.txt"
    protected.write_text("preserve", encoding="utf-8")
    directory = _storage(project) / "garbage" / str(uuid4())
    directory.mkdir()
    try:
        (directory / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(RecoveryConflict, match="Linked"):
        build_project_service().maintain_transactions(project)
    assert protected.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize("layout", [
    {}, {"schema_version": 3, "status": "READY"},
    {"schema_version": 2, "status": "UNKNOWN"}, {"schema_version": 2, "status": []},
])
def test_unknown_layout_is_rejected_without_mutating_project(project, layout):
    before = (project.root / "PROJECT.md").read_bytes()
    (_storage(project) / "layout.json").write_bytes(json_bytes(layout))
    with pytest.raises(RecoveryConflict, match="Unsupported"):
        build_project_service().open_project(project.root)
    assert (project.root / "PROJECT.md").read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("schema_version", []), ("status", []), ("kind", []), ("fingerprint", {}),
])
def test_corrupt_receipt_fields_report_recovery_conflict(project, field, value):
    result = build_commit_service().commit(project, (SetProjectText(text="# Saved\n"),))
    path = receipt_path(project.root, result.commit_id)
    receipt = json.loads(path.read_text())
    receipt[field] = value
    path.write_bytes(json_bytes(receipt))
    with pytest.raises(RecoveryConflict, match="Invalid Commit"):
        get_manifest(project.root, result.commit_id)
    assert (project.root / "PROJECT.md").read_bytes() == b"# Saved\n"


def test_cli_maintenance_exposes_counts_space_and_warning_without_model_call(project):
    models = ModelGateway(
        {"fake": ModelDefinition("fake", "fake", "https://example.invalid", "none")}, "fake"
    )
    cli = NovelCLI(project.root.parent, models=models)
    cli.project = project
    result = build_commit_service().commit(project, (SetProjectText(text="# CLI\n"),))
    orphan = _storage(project) / "staging/.staging-orphan"
    orphan.mkdir()
    (orphan / "payload.bin").write_bytes(b"x" * 4096)
    output = cli._command("/project maintenance").text
    assert "正式 Commit 凭据：1" in output and "本次回收：4096 bytes" in output
    assert get_manifest(project.root, result.commit_id)["status"] == "COMPLETED"
    assert "maintenance" in cli._command("/help").text
