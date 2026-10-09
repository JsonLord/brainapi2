import hashlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from deploy.hf_space import DeploymentError, REQUIRED_FILES, stage_revision, upload_revision


def run_git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    run_git(root, "init")
    for name in (*REQUIRED_FILES, "src/app.py", "console/package.json", "scripts/another_runtime.py"):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("committed content\n")
    run_git(root, "add", ".")
    run_git(root, "-c", "user.name=Deployment Test", "-c", "user.email=deployment-test@example.invalid", "commit", "-qm", "complete source")
    return root


def test_stage_uses_complete_commit_and_excludes_local_changes(source, tmp_path):
    (source / "scripts/start_with_ollama.py").write_text("uncommitted replacement")
    (source / ".env").write_text("local-only configuration")
    stage = tmp_path / "stage"
    manifest = stage_revision(source, "HEAD", stage)
    assert (stage / "scripts/start_with_ollama.py").read_text() == "committed content\n"
    assert "scripts/another_runtime.py" in manifest["sha256"]
    assert not (stage / ".env").exists()
    assert manifest["sha256"]["scripts/start_with_ollama.py"] == hashlib.sha256(b"committed content\n").hexdigest()


def test_missing_committed_wrapper_fails_even_if_local_file_exists(source, tmp_path):
    run_git(source, "rm", "--cached", "scripts/start_with_ollama.py")
    run_git(source, "-c", "user.name=Deployment Test", "-c", "user.email=deployment-test@example.invalid", "commit", "-qm", "partial source")
    assert (source / "scripts/start_with_ollama.py").exists()
    with pytest.raises(DeploymentError, match="scripts/start_with_ollama.py"):
        stage_revision(source, "HEAD", tmp_path / "stage")


def test_upload_sends_every_staged_file_and_detects_remote_omission(source, tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    manifest = stage_revision(source, "HEAD", stage)
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(CommitOperationAdd=lambda **kwargs: kwargs))

    class API:
        def repo_info(self, *args, **kwargs):
            return SimpleNamespace(sha="previous-hf-commit")

        def create_commit(self, **kwargs):
            assert kwargs["parent_commit"] == "previous-hf-commit"
            assert {item["path_in_repo"] for item in kwargs["operations"]} == set(manifest["sha256"])
            return SimpleNamespace(oid="new-hf-commit", commit_url="https://huggingface.co/spaces/test/commit/new-hf-commit")

        def list_repo_files(self, *args, **kwargs):
            assert kwargs["revision"] == "new-hf-commit"
            return [name for name in manifest["sha256"] if name != "scripts/start_with_ollama.py"]

    with pytest.raises(DeploymentError, match="Uploaded HF commit is missing source files: scripts/start_with_ollama.py"):
        upload_revision(API(), stage, manifest)
