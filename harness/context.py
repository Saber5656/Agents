"""Requirement continuity and visible-context persistence helpers.

This module is intentionally independent from provider execution.  It uses the
existing :class:`TaskStore` for durable requirement, revision, acceptance, and
completion state, while keeping requirement dependencies and local follow-ups
in an explicit, private sidecar.  Vault paths are always caller supplied.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .runner import redact


class ContextError(RuntimeError):
    """A context record cannot be safely read or saved."""


def _atomic_write(path: Path, data: str) -> None:
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise


class RequirementLedger:
    """Keep all requirements visible while selecting or resuming one unit."""

    def __init__(self, store, record_path: str | os.PathLike):
        self.store = store
        self.path = Path(record_path).expanduser()
        if not self.path.parent.is_dir():
            raise ContextError(f"requirement record parent is missing: {self.path.parent}")
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _locked(self):
        try:
            with self.lock_path.open("a+") as lock:
                self.lock_path.chmod(0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield
        except OSError as exc:
            raise ContextError(f"cannot lock requirement record: {exc}") from exc

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "requirements": {}, "followups": [], "selected": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContextError(f"invalid requirement record: {self.path}") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ContextError("unsupported requirement record")
        value.setdefault("requirements", {})
        value.setdefault("followups", [])
        value.setdefault("selected", [])
        return value

    def _save(self, value):
        _atomic_write(self.path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def add(self, text, *, acceptance=(), source=None, depends_on=()):
        depends_on = list(depends_on)
        with self._locked():
            existing = self._load()
            unknown = [item for item in depends_on if item not in existing["requirements"]]
            if unknown:
                raise ContextError(f"unknown requirement dependency: {unknown[0]}")
        row = self.store.create_requirement(text, source=source, acceptance=list(acceptance))
        with self._locked():
            record = self._load()
            record["requirements"][row["id"]] = {
                "depends_on": depends_on,
                "latest_text": text,
                "revisions": [],
                "selected": False,
            }
            self._save(record)
        return self._with_sidecar(row, record["requirements"][row["id"]])

    def select(self, requirement_id):
        self._require(requirement_id)
        with self._locked():
            record = self._load()
            if requirement_id not in record["selected"]:
                record["selected"].append(requirement_id)
            record["requirements"].setdefault(requirement_id, {})["selected"] = True
            self._save(record)
        return self.handoff()

    def revise(self, requirement_id, text):
        self._require(requirement_id)
        row = self.store.add_requirement_revision(requirement_id, text)
        with self._locked():
            record = self._load()
            detail = record["requirements"].setdefault(requirement_id, {})
            detail.setdefault("revisions", []).append(text)
            detail["latest_text"] = text
            self._save(record)
        return self._with_sidecar(row, detail)

    def record_followup(self, originating_task, purpose, *, evidence=()):
        if not isinstance(purpose, str) or not purpose.strip():
            raise ContextError("follow-up purpose is required")
        item = {
            "id": "followup_" + hashlib.sha256(
                f"{originating_task}\0{purpose}".encode()
            ).hexdigest()[:16],
            "originating_task": originating_task,
            "purpose": purpose,
            "evidence": list(evidence),
            "status": "local_only",
        }
        with self._locked():
            record = self._load()
            existing = next((x for x in record["followups"] if x["id"] == item["id"]), None)
            if existing is None:
                record["followups"].append(item)
            else:
                item = existing
            self._save(record)
        return item

    def _require(self, requirement_id):
        row = self.store.get_requirement(requirement_id)
        if row is None:
            raise ContextError(f"unknown requirement: {requirement_id}")
        return row

    @staticmethod
    def _with_sidecar(row, detail):
        result = dict(row)
        result.update({key: detail.get(key, default) for key, default in (
            ("depends_on", []), ("latest_text", row.get("text")),
            ("revisions", []), ("selected", False),
        )})
        return result

    def handoff(self):
        with self._locked():
            record = self._load()
        rows = []
        for row in self.store.list_requirements():
            rows.append(self._with_sidecar(row, record["requirements"].get(row["id"], {})))
        return {
            "requirements": rows,
            "selected": list(record["selected"]),
            "followups": list(record["followups"]),
        }

    def completion_report(self):
        report = self.store.completion_report()
        handoff = self.handoff()
        by_id = {row["id"]: row for row in handoff["requirements"]}
        missing = list(report["missing_tasks"])
        for row in handoff["requirements"]:
            for dependency in row.get("depends_on", []):
                dep = by_id.get(dependency)
                if dep is None:
                    missing.append({"requirement_id": row["id"], "reason": "unknown dependency", "dependency": dependency})
                    continue
                linked = set(dep.get("task_ids", []))
                if not linked:
                    missing.append({"requirement_id": row["id"], "reason": "dependency not complete", "dependency": dependency})
        report.update({"complete": not report["missing_requirements"] and not missing,
                       "missing_tasks": missing, "requirements": handoff["requirements"],
                       "followups": handoff["followups"]})
        return report


class VaultContext:
    """Append visible records as private, indexed Vault chunks."""

    def __init__(self, vault_root: str | os.PathLike, run_id: str, *, chunk_size=256 * 1024):
        root = Path(vault_root).expanduser()
        if not root.is_dir():
            raise ContextError(f"Vault does not exist: {root}")
        if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
            raise ContextError("run_id must be a single path component")
        if not isinstance(chunk_size, int) or chunk_size < 1:
            raise ContextError("chunk_size must be positive")
        self.root = root
        self.run_dir = root / run_id
        try:
            self.run_dir.mkdir(mode=0o700, exist_ok=True)
            probe = self.run_dir / ".write-probe"
            probe.touch(mode=0o600, exist_ok=False)
            probe.unlink()
        except OSError as exc:
            raise ContextError(f"Vault is not writable: {self.run_dir}") from exc
        self.chunk_size = chunk_size

    @staticmethod
    def _visible(value):
        if isinstance(value, dict):
            if value.get("type") == "reasoning" or value.get("channel") == "analysis":
                return None
            visible = {}
            for key, item in value.items():
                if key == "encrypted_content":
                    continue
                cleaned = VaultContext._visible(item)
                if cleaned is not None:
                    visible[key] = cleaned
            return visible
        if isinstance(value, list):
            return [item for item in (VaultContext._visible(item) for item in value) if item is not None]
        return value

    def _index_path(self):
        return self.run_dir / "context-index.json"

    def index(self):
        if not self._index_path().exists():
            return {"records": [], "complete": False, "truncation": "none"}
        try:
            return json.loads(self._index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContextError("context index is unreadable") from exc

    def save_records(self, stream, records, *, complete=False, truncation="none", env=None):
        if not isinstance(stream, str) or not stream or Path(stream).name != stream:
            raise ContextError("stream must be a simple name")
        env = dict(os.environ if env is None else env)
        rendered = []
        for record in records:
            clean = self._visible(record)
            if clean is not None:
                rendered.append(redact(json.dumps(clean, ensure_ascii=False) + "\n", env))
        text = "".join(rendered)
        old = self.index()
        records_index = [item for item in old.get("records", []) if item.get("stream") != stream]
        for offset in range(0, len(text) or 1, self.chunk_size):
            part = text[offset:offset + self.chunk_size]
            number = offset // self.chunk_size
            path = self.run_dir / f"{stream}-{number:04d}.jsonl"
            _atomic_write(path, part)
            records_index.append({"stream": stream, "path": path.name,
                                  "size": len(part.encode()), "availability": "complete" if complete else "available"})
        value = {"records": sorted(records_index, key=lambda item: item["path"]),
                 "complete": bool(complete), "truncation": truncation or "none"}
        _atomic_write(self._index_path(), json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        return {"status": "saved", **value}


def export_visible_jsonl(source, destination, *, env=None):
    """Export a visible JSONL source without inventing a fallback path."""
    source = Path(source)
    destination = Path(destination)
    if not source.is_file():
        raise ContextError(f"source record is missing: {source}")
    try:
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise ContextError(f"source record is unreadable: {source}") from exc
    context = VaultContext(destination.parent, destination.stem)
    return context.save_records(destination.stem, rows, complete=True, env=env)
