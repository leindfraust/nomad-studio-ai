"""Filesystem job store: one directory per job, atomic snapshots, OS-backed claim.

The record is the truth. Nothing important lives only in memory, so a killed
process is resumable and a reviewer can read the state with ``cat``.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import timezone
from pathlib import Path

from ..contracts import Attempt, Brief, JobRecord, JobState, utcnow
from ..errors import JobBusy, JobStateError


class JobStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        # Readers (the SSE stream) and the worker share this store. On Windows a
        # plain open() does not grant FILE_SHARE_DELETE, so a read in progress makes
        # os.replace fail with WinError 5. Serialising in-process access removes the
        # race; the retry below covers another process or a scanner holding the file.
        self._io = threading.RLock()

    # --- records -----------------------------------------------------------
    def create(self, brief: Brief, *, dry_run: bool = False) -> JobRecord:
        job_id = f"{utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        record = JobRecord(id=job_id, brief=brief, dry_run=dry_run)
        self.artifact_dir(job_id).mkdir(parents=True, exist_ok=True)
        self.save(record)
        return record

    def get(self, job_id: str) -> JobRecord:
        path = self._job_dir(job_id) / "job.json"
        with self._io:
            if not path.is_file():
                raise FileNotFoundError(f"no such job: {job_id}")
            return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, record: JobRecord) -> JobRecord:
        record.updated_at = utcnow()
        record.revision += 1
        path = self._job_dir(record.id) / "job.json"
        temporary = path.with_suffix(".json.tmp")
        with self._io:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _replace_atomically(temporary, path)
        return record

    def list(self) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        with self._io:
            for path in sorted(self._root.glob("*/job.json"), reverse=True):
                try:
                    jobs.append(JobRecord.model_validate_json(path.read_text(encoding="utf-8")))
                except (ValueError, OSError):
                    continue  # a truncated or briefly locked file is skipped, not fatal
        return jobs

    def exists(self, job_id: str) -> bool:
        return (self._job_dir(job_id) / "job.json").is_file()

    def artifact_dir(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "artifacts"

    def resolve_artifact(self, job_id: str, relative: str) -> Path:
        """Map a request path to a file inside this job's artifacts, or refuse."""
        base = self.artifact_dir(job_id).resolve()
        candidate = (base / relative).resolve()
        if candidate != base and base not in candidate.parents:
            raise PermissionError("artifact path escapes the job directory")
        return candidate

    def is_stalled(self, record: JobRecord, window_s: float) -> bool:
        if record.state in (JobState.FINALIZED, JobState.FAILED):
            return False
        age = (utcnow() - record.updated_at).total_seconds()
        return age > window_s

    # --- single writer -----------------------------------------------------
    def claim(self, job_id: str) -> None:
        lock = self._job_dir(job_id) / "run.lock"
        if not self._job_dir(job_id).is_dir():
            raise FileNotFoundError(f"no such job: {job_id}")
        for attempt in (1, 2):
            try:
                handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = _read_pid(lock)
                if holder and _pid_alive(holder) and attempt == 1:
                    raise JobBusy(f"job {job_id} is already running (pid {holder})")
                lock.unlink(missing_ok=True)  # stale claim from a dead process
                continue
            with os.fdopen(handle, "w", encoding="ascii") as stream:
                stream.write(str(os.getpid()))
            return
        raise JobBusy(f"job {job_id} is claimed by another process")

    def release(self, job_id: str) -> None:
        (self._job_dir(job_id) / "run.lock").unlink(missing_ok=True)

    def log(self, record: JobRecord, action: str, outcome: str, detail: str | None = None) -> None:
        record.attempts.append(
            Attempt(at=utcnow(), action=action, outcome=outcome, detail=detail)
        )

    # --- internals ---------------------------------------------------------
    def _job_dir(self, job_id: str) -> Path:
        if not job_id or any(char in job_id for char in "/\\:"):
            raise JobStateError(f"invalid job id: {job_id!r}")
        return self._root / job_id


def _replace_atomically(source: Path, destination: Path) -> None:
    """os.replace, retried past the Windows lock that a concurrent handle causes.

    ERROR_ACCESS_DENIED / ERROR_SHARING_VIOLATION here are transient: an editor, an
    antivirus scan or another process reading the record. A bounded retry is correct;
    a failure after it is real and propagates.
    """
    attempts = 40
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.025)


def _read_pid(lock: Path) -> int | None:
    try:
        return int(lock.read_text(encoding="ascii").strip())
    except (ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
        if process:
            ctypes.windll.kernel32.CloseHandle(process)  # type: ignore[attr-defined]
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def now_iso() -> str:
    return utcnow().astimezone(timezone.utc).isoformat()


def sleep_seconds(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
