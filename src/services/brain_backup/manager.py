"""Startup restore gate and conflict-safe content checkpoint coordinator."""
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .git import BackupError
from .postgres import canonical
from .state import generation, locked, write_status

CHUNK = 32 * 1024 * 1024


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(root, profile, postgres_major=15):
    """Validate every payload before any database write. No extra source files."""
    from src.constants.data import Brain, Workspace
    from src.services.brain_identity import validate_brain_id
    root = Path(root)
    try:
        manifest = json.loads((root / "manifest.json").read_text())
        if (manifest["format_version"] != 1 or manifest["postgres_major"] != postgres_major
                or manifest["schema_revision"] != 1 or manifest["embedding"] != profile
                or manifest["stored_dimension"] != profile["stored_dimension"]
                or manifest["embedding_profile"] != profile["profile"]):
            raise BackupError("BACKUP_INCOMPATIBLE_MANIFEST")
        checks = manifest["checksums"]
        hold = manifest.get("maintenance", {})
        if hold and (set(hold) != {"id", "quiesced"} or hold["quiesced"] is not True
                     or not re.fullmatch(r"[0-9a-f]{32}", str(hold["id"]))):
            raise BackupError("BACKUP_INVALID_MAINTENANCE")
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and p.relative_to(root).parts[0] != ".git"}
        expected = {"manifest.json", *checks}
        if actual != expected or any(p.is_symlink() for p in root.rglob("*") if p.relative_to(root).parts[0] != ".git"):
            raise BackupError("BACKUP_UNEXPECTED_FILES")
        for name, checksum in checks.items():
            if (not re.fullmatch(r"(?:system/registry\.json|brains/[A-Za-z0-9_-]+\.dump\.part[0-9]{4})", name)
                    or not re.fullmatch(r"[0-9a-f]{64}", checksum) or sha(root / name) != checksum):
                raise BackupError("BACKUP_CHECKSUM_MISMATCH")
        registry = json.loads((root / "system/registry.json").read_text())
        names, ids = set(), set()
        for b in registry["brains"]:
            validate_brain_id(b["name_key"])
            clean = Brain(id=b["id"], name_key=b["name_key"]).model_dump(mode="json")
            if b["document"] != clean or b["name_key"] in names or b["id"] in ids:
                raise BackupError("BACKUP_INVALID_REGISTRY")
            names.add(b["name_key"])
            ids.add(b["id"])
        if manifest["brains"] != sorted(names) or set(manifest["fingerprints"]) != names:
            raise BackupError("BACKUP_INVALID_REGISTRY")
        for w in registry["workspaces"]:
            obj = Workspace.model_validate(w["document"])
            if (obj.model_dump(mode="json") != w["document"] or obj.brain_id not in names
                    or (obj.id, obj.brain_id, obj.slug) != (w["id"], w["brain_id"], w["slug"])):
                raise BackupError("BACKUP_INVALID_WORKSPACE")
        payloads = {"system/registry.json"}
        for name, parts in manifest["archives"].items():
            if name not in names or not parts or parts != [f"brains/{name}.dump.part{i:04d}" for i in range(len(parts))]:
                raise BackupError("BACKUP_INVALID_ARCHIVE")
            payloads.update(parts)
        if payloads != set(checks):
            raise BackupError("BACKUP_INVALID_ARCHIVE")
        digest = hashlib.sha256(canonical({"registry": registry, "fingerprints": manifest["fingerprints"], "embedding": profile})).hexdigest()
        if digest != manifest["content_digest"]:
            raise BackupError("BACKUP_CONTENT_DIGEST_MISMATCH")
        return manifest, registry
    except BackupError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise BackupError("BACKUP_INVALID_MANIFEST") from None


class Manager:
    def __init__(self, repository, postgres, root):
        self.repo = repository
        self.pg = postgres
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.receipt_path = self.root / "receipt.json"

    def receipt(self):
        if not self.receipt_path.exists():
            return None
        try:
            receipt = json.loads(self.receipt_path.read_text())
            if not isinstance(receipt, dict) or set(receipt) != {"head", "digest"}:
                raise ValueError("invalid receipt")
            for key, length in (("head", 40), ("digest", 64)):
                if receipt[key] is not None and not re.fullmatch(f"[0-9a-f]{{{length}}}", receipt[key]):
                    raise ValueError("invalid receipt")
            return receipt
        except (OSError, ValueError):
            raise BackupError("BACKUP_LOCAL_RECEIPT_CORRUPT") from None

    def remember(self, head, digest):
        temp = self.receipt_path.with_suffix(".tmp")
        temp.write_bytes(canonical({"head": head, "digest": digest}))
        temp.replace(self.receipt_path)

    def remote(self):
        head = self.repo.fetch()
        if head is None:
            return None, None, None
        if not (self.repo.path / "manifest.json").exists():
            # An initialized README-only repository has no snapshot yet. A
            # deleted manifest or any backup-shaped content is corruption.
            files = set(filter(None, self.repo.run("ls-files", "-z").stdout.decode().split("\0")))
            history = self.repo.run("log", "-1", "--format=%H", "HEAD", "--", "manifest.json").stdout.strip()
            if history or files - {"README.md", "LICENSE", ".gitignore", ".gitattributes"}:
                raise BackupError("BACKUP_REMOTE_MANIFEST_MISSING")
            return head, None, None
        manifest, registry = validate(self.repo.path, self.pg.profile, self.pg.major_version())
        return head, manifest, registry

    def startup(self):
        with locked(self.root / "operation.lock"):
            head, manifest, registry = self.remote()
            local = self.pg.has_local_content()
            receipt = self.receipt()
            if not manifest and receipt and receipt["digest"]:
                raise BackupError("BACKUP_REMOTE_HISTORY_MISSING")
            if local:
                # Only a matching checkpoint lineage permits retaining local
                # content. Unrelated populated stores require explicit recovery.
                if manifest and (not receipt or receipt["head"] != head):
                    raise BackupError("BACKUP_LOCAL_REMOTE_CONFLICT")
            elif manifest:
                with tempfile.TemporaryDirectory(dir=self.root) as temp:
                    staged = Path(temp)
                    (staged / "brains").mkdir()
                    for name, parts in manifest["archives"].items():
                        archive = staged / "brains" / f"{name}.dump"
                        with archive.open("wb") as dest:
                            for part in parts:
                                with (self.repo.path / part).open("rb") as src:
                                    shutil.copyfileobj(src, dest)
                        self.pg.tool("pg_restore", self.pg.system, "--list", str(archive))
                    self.pg.restore(staged, registry)
            # A previous container may still be completing a shutdown push.
            # Never publish readiness/writability from a stale restore.
            latest, _, _ = self.remote()
            if latest != head:
                from .maintenance import fail
                fail("BACKUP_STARTUP_REMOTE_ADVANCED")
                raise BackupError("BACKUP_STARTUP_REMOTE_ADVANCED")
            from .maintenance import initialize
            initialize(manifest.get("maintenance") if manifest else None, preserve_work=local)
            self.remember(head, manifest["content_digest"] if manifest else None)
            write_status(state="ready", commit=head, restored=bool(manifest and not local),
                         restored_checkpoint=head if manifest and not local else None,
                         remote_checkpoint=head, restore_qualified=bool(manifest),
                         checkpoint_generation=generation(), remote_verified=True,
                         last_success=datetime.now(timezone.utc).isoformat() if manifest else None)

    def verify_checkpoint(self, expected):
        with locked(self.root / "operation.lock"):
            head, manifest, _ = self.remote()
            if head != expected or manifest is None:
                write_status(state="degraded", remote_checkpoint=head, remote_verified=False,
                             error="BACKUP_REMOTE_CONFLICT")
                raise BackupError("BACKUP_REMOTE_CONFLICT")
            write_status(remote_checkpoint=head, remote_verified=True)
            return head

    def verify_startup(self):
        receipt = self.receipt()
        if not receipt:
            raise BackupError("BACKUP_STARTUP_RESTORE_REQUIRED")
        with locked(self.root / "operation.lock"):
            head, _, _ = self.remote()
            if head != receipt["head"]:
                from .maintenance import fail
                fail("BACKUP_STARTUP_REMOTE_ADVANCED")
                raise BackupError("BACKUP_STARTUP_REMOTE_ADVANCED")
            write_status(remote_checkpoint=head, remote_verified=True)

    def reconcile(self):
        """A sealed handoff pins publication until explicit checkpoint/resume.

        Read paths can lazily initialize derived indexes. Neither periodic nor
        shutdown reconciliation may move the handoff behind the operator's SHA.
        """
        from .maintenance import status
        control = status()
        if control.get("quiesced") and control.get("sealed"):
            receipt = self.receipt()
            if not receipt or not receipt["head"]:
                raise BackupError("BACKUP_STARTUP_RESTORE_REQUIRED")
            self.verify_checkpoint(receipt["head"])
            return receipt["head"], False
        return self.backup()

    def backup(self, maintenance=None):
        with locked(self.root / "operation.lock"):
            head, manifest, _ = self.remote()
            receipt = self.receipt()
            if receipt is None or receipt["head"] != head:
                raise BackupError("BACKUP_REMOTE_CONFLICT")
            before = generation()
            if maintenance is None:
                from .maintenance import hold_manifest
                maintenance = hold_manifest()
            with tempfile.TemporaryDirectory(dir=self.root) as temp:
                output = Path(temp)
                registry, fingerprints = self.pg.snapshot(output)
                digest = hashlib.sha256(canonical({"registry": registry, "fingerprints": fingerprints,
                                                   "embedding": self.pg.profile})).hexdigest()
                if manifest and digest == manifest["content_digest"] and manifest.get("maintenance", {}) == maintenance:
                    write_status(state="ready", commit=head, dirty=generation() != before,
                                 checkpoint_generation=before, remote_checkpoint=head, remote_verified=True,
                                 last_success=datetime.now(timezone.utc).isoformat(),
                                 checked_at=datetime.now(timezone.utc).isoformat())
                    return head, False
                archives = {}
                for archive in (output / "brains").glob("*.dump"):
                    parts = []
                    with archive.open("rb") as src:
                        for i, chunk in enumerate(iter(lambda: src.read(CHUNK), b"")):
                            part = archive.with_name(f"{archive.name}.part{i:04d}")
                            part.write_bytes(chunk)
                            parts.append(part.relative_to(output).as_posix())
                    archives[archive.stem] = parts
                    archive.unlink()
                checks = {p.relative_to(output).as_posix(): sha(p) for p in output.rglob("*") if p.is_file()}
                from src.constants.data import BRAIN_VERSION
                document = {"format_version": 1, "schema_revision": 1, "postgres_major": self.pg.major_version(), "created_at": datetime.now(timezone.utc).isoformat(),
                            "brainapi_version": BRAIN_VERSION, "source_sha": (os.getenv("BUILD_SHA") or "unknown"),
                            "embedding_profile": self.pg.profile["profile"], "stored_dimension": self.pg.profile["stored_dimension"],
                            "embedding": self.pg.profile, "brains": sorted(fingerprints), "fingerprints": fingerprints,
                            "archives": archives, "checksums": checks, "content_digest": digest}
                document["maintenance"] = maintenance
                (output / "manifest.json").write_bytes(canonical(document))
                validate(output, self.pg.profile, self.pg.major_version())
                new_head = self.repo.publish(output, head)
                # A successful push alone is insufficient for a handoff receipt.
                verified, verified_manifest, _ = self.remote()
                if verified != new_head or verified_manifest is None:
                    raise BackupError("BACKUP_REMOTE_CONFLICT")
                self.remember(new_head, digest)
                write_status(state="ready", commit=new_head, dirty=generation() != before,
                             checkpoint_generation=before, remote_checkpoint=new_head,
                             remote_verified=True,
                             last_success=datetime.now(timezone.utc).isoformat())
                return new_head, True
