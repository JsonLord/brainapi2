"""Stage and deploy a complete Git revision to the BrainAPI Docker Space."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

SPACE = "Leon4gr45/brain"
REQUIRED_FILES = (
    "Dockerfile", "entrypoint.sh", "README.md", ".dockerignore", ".hfignore",
    "scripts/start_with_ollama.py", "scripts/preload_ollama_models.sh",
    "scripts/preload_docker_models.py", "deploy/supervisord.conf",
    "deploy/nginx.conf", "pyproject.toml", "poetry.lock",
)
REQUIRED_DIRECTORIES = ("deploy", "scripts", "src", "console")


class DeploymentError(RuntimeError):
    pass


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def validate_stage(folder: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (folder / name).is_file()]
    missing.extend(name + "/" for name in REQUIRED_DIRECTORIES if not (folder / name).is_dir())
    if missing:
        raise DeploymentError("Incomplete HF build context: " + ", ".join(missing))


def stage_revision(root: Path, revision: str, folder: Path) -> dict:
    commit = git(root, "rev-parse", "--verify", revision + "^{commit}")
    if folder.exists() and any(folder.iterdir()):
        raise DeploymentError("Staging directory must be empty: " + str(folder))
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as exported:
        subprocess.run(["git", "-C", str(root), "archive", "--format=tar", commit], stdout=exported, check=True)
        exported.seek(0)
        with tarfile.open(fileobj=exported) as archive:
            if any(not (entry.isfile() or entry.isdir()) for entry in archive.getmembers()):
                raise DeploymentError("Git archive contains links or special files; inspect before deployment")
            archive.extractall(folder, filter="data")
    validate_stage(folder)
    files = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            relative = path.relative_to(folder).as_posix()
            if path.name in {".env", ".env.development", "gcp_credentials.json"}:
                raise DeploymentError("Credential file is tracked in the deployment source: " + relative)
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    tracked = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-rz", "--name-only", commit]).decode().split("\0")
    if set(files) != set(filter(None, tracked)):
        raise DeploymentError("Git archive differs from the complete tracked tree")
    return {"source_commit": commit, "file_count": len(files), "sha256": files}


def inspect_remote(api, revision: str) -> list[str]:
    files = set(api.list_repo_files(SPACE, repo_type="space", revision=revision))
    missing = [name for name in REQUIRED_FILES if name not in files]
    print(json.dumps({"space": SPACE, "revision": revision, "missing_required_files": missing}))
    return missing


def upload_revision(api, folder: Path, manifest: dict):
    from huggingface_hub import CommitOperationAdd

    validate_stage(folder)
    current = api.repo_info(SPACE, repo_type="space")
    operations = [CommitOperationAdd(path_in_repo=name, path_or_fileobj=folder / name) for name in manifest["sha256"]]
    commit = api.create_commit(
        repo_id=SPACE, repo_type="space", operations=operations,
        parent_commit=current.sha,
        commit_message="Deploy complete BrainAPI source " + manifest["source_commit"][:12],
        commit_description="All tracked files from Git commit " + manifest["source_commit"] + "; required Docker inputs checked before upload.",
    )
    print(json.dumps({"source_commit": manifest["source_commit"], "hf_commit": commit.oid, "commit_url": commit.commit_url}))
    remote_files = set(api.list_repo_files(SPACE, repo_type="space", revision=commit.oid))
    missing = set(manifest["sha256"]) - remote_files
    if missing:
        raise DeploymentError("Uploaded HF commit is missing source files: " + ", ".join(sorted(missing)))
    # HF stores ordinary files as Git blobs and LFS files by their SHA-256.
    paths = api.get_paths_info(SPACE, list(manifest["sha256"]), repo_type="space", revision=commit.oid)
    verified = set()
    for item in paths:
        name = item.path
        if name not in manifest["sha256"]:
            continue
        contents = (folder / name).read_bytes()
        lfs = getattr(item, "lfs", None)
        if lfs:
            actual = lfs.get("sha256") if isinstance(lfs, dict) else lfs.sha256
            expected = manifest["sha256"][name]
        else:
            actual = item.blob_id
            expected = hashlib.sha1(b"blob " + str(len(contents)).encode() + b"\0" + contents).hexdigest()
        if actual != expected:
            raise DeploymentError("HF content verification failed: " + name)
        verified.add(name)
    if verified != set(manifest["sha256"]):
        raise DeploymentError("HF metadata did not verify the entire uploaded source tree")
    print("Verified complete HF source tree:", len(verified), "files")
    return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "inspect", "upload"])
    parser.add_argument("--revision", default="HEAD", help="Git revision for stage/upload; HF revision for inspect")
    parser.add_argument("--output", type=Path, help="Empty directory for stage output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.action == "stage":
        if not args.output:
            parser.error("stage requires --output")
        manifest = stage_revision(root, args.revision, args.output)
        report = args.output.with_name(args.output.name + ".manifest.json")
        report.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"source_commit": manifest["source_commit"], "file_count": manifest["file_count"], "stage": str(args.output), "manifest": str(report)}))
        return
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise DeploymentError("HF_TOKEN is missing; supply it securely in environment settings")
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    api.whoami()
    if args.action == "inspect":
        inspect_remote(api, "main" if args.revision == "HEAD" else args.revision)
        return
    with tempfile.TemporaryDirectory(prefix="brainapi-hf-") as temporary:
        folder = Path(temporary) / "source"
        manifest = stage_revision(root, args.revision, folder)
        upload_revision(api, folder, manifest)


if __name__ == "__main__":
    try:
        main()
    except DeploymentError as error:
        raise SystemExit(str(error)) from None
