"""Bounded Git operations; token is only passed via ephemeral child environment."""
import os
import re
import subprocess
from pathlib import Path


class BackupError(RuntimeError):
    """Messages are fixed codes: never include subprocess output or credentials."""


class GitRepository:
    def __init__(self, path, url, branch="main", token=None):
        self.path = Path(path)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) or ".." in branch:
            raise BackupError("BACKUP_INVALID_BRANCH")
        self.branch = branch
        self.url = url
        self.token = token

    @classmethod
    def configured(cls, path):
        token = os.getenv("GITHUB_PAT")
        if not token:
            raise BackupError("BACKUP_GITHUB_PAT_REQUIRED")
        repo = os.getenv("BRAIN_BACKUP_REPOSITORY", "JsonLord/nodepad-brain-backup")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise BackupError("BACKUP_INVALID_REPOSITORY")
        return cls(path, f"https://github.com/{repo}.git",
                   os.getenv("BRAIN_BACKUP_BRANCH", "main"), token)

    def run(self, *args, check=True):
        env = os.environ.copy()
        env.update(GIT_TERMINAL_PROMPT="0", GIT_TRACE="0", GIT_CURL_VERBOSE="0",
                   GIT_TRACE_CURL="0", GIT_TRACE_PACKET="0")
        if self.token:
            # Executable contains no credential; neither URL nor Git config does.
            helper = self.path.parent / "askpass.py"
            helper.write_text("#!/usr/bin/env python3\nimport os,sys\nprint('x-access-token' if 'Username' in sys.argv[1] else os.environ['GITHUB_PAT'])\n")
            helper.chmod(0o700)
            env.update(GIT_ASKPASS=str(helper), GITHUB_PAT=self.token,
                       GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="credential.helper",
                       GIT_CONFIG_VALUE_0="")
        try:
            result = subprocess.run(["git", "-C", str(self.path), *args], env=env,
                                    capture_output=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired):
            raise BackupError("BACKUP_GIT_UNAVAILABLE") from None
        if check and result.returncode:
            # Live qualification must distinguish access, transport and local
            # failures without emitting Git stderr (which may contain secrets).
            operation = next((arg.upper().replace("-", "_") for arg in args
                              if arg in {"init", "ls-remote", "fetch", "checkout", "clean", "rev-parse", "add", "commit", "push"}), "OPERATION")
            failure = self._failure_category(result.stderr)
            raise BackupError(f"BACKUP_GIT_{operation}_{failure}")
        return result

    @staticmethod
    def _failure_category(stderr):
        message = stderr.lower()
        categories = (
            ("AUTHENTICATION_FAILED", (b"authentication failed", b"invalid username or token", b"invalid username or password", b"error: 401")),
            ("REPOSITORY_ACCESS_DENIED", (b"repository not found", b"error: 403", b"access denied")),
            ("CREDENTIAL_HELPER_FAILED", (b"askpass", b"could not read username", b"could not read password")),
            ("TLS_FAILED", (b"ssl certificate", b"certificate verification", b"gnutls_handshake")),
            ("NETWORK_FAILED", (b"could not resolve", b"failed to connect", b"connection timed out", b"connection reset")),
            ("OWNERSHIP_FAILED", (b"dubious ownership",)),
            ("REMOTE_CONFLICT", (b"non-fast-forward", b"fetch first")),
            ("FILESYSTEM_FAILED", (b"permission denied", b"read-only file system", b"no space left")),
        )
        return next((category for category, markers in categories if any(marker in message for marker in markers)), "OPERATION_FAILED")

    def fetch(self):
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (self.path / ".git").exists():
            self.run("init", "--quiet")
        refs = self.run("ls-remote", "--heads", self.url).stdout.decode().splitlines()
        head = next((line.split()[0] for line in refs if line.split()[1] == f"refs/heads/{self.branch}"), None)
        if head is None:
            # A populated repo with a missing configured branch is not first boot.
            if refs:
                raise BackupError("BACKUP_REMOTE_BRANCH_MISSING")
            return None
        self.run("fetch", "--quiet", "--no-tags", self.url, f"refs/heads/{self.branch}")
        self.run("checkout", "--quiet", "--detach", "FETCH_HEAD")
        self.run("clean", "-fdq")
        return self.run("rev-parse", "HEAD").stdout.decode().strip()

    def publish(self, source, base):
        # Compare before copying and rely on non-force push's atomic ref check.
        current = self.fetch()
        if current != base:
            raise BackupError("BACKUP_REMOTE_CONFLICT")
        import shutil
        for child in self.path.iterdir():
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        for child in Path(source).iterdir():
            shutil.copytree(child, self.path / child.name) if child.is_dir() else shutil.copy2(child, self.path / child.name)
        self.run("add", "--all")
        self.run("-c", "user.name=BrainAPI Backup", "-c", "user.email=backup@brainapi.local",
                 "commit", "--quiet", "-m", "Checkpoint registered Brain content")
        # No force, rebase, or blind retry: divergent state needs operator review.
        self.run("push", "--quiet", self.url, f"HEAD:refs/heads/{self.branch}")
        return self.run("rev-parse", "HEAD").stdout.decode().strip()
