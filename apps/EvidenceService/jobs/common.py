"""Shared plumbing of the internal jobs: run id, logging, dependency pre-checks, summary, exit codes.

Exit codes: 0 = completed without failures (also with no candidates); 1 = completed but some items
failed (they stay candidates for the next run); 2 = configuration or dependency error (nothing done).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from storage.s3_adapter import StorageUnavailable

EXIT_OK, EXIT_FAILURES, EXIT_DEPENDENCY = 0, 1, 2


class DependencyError(Exception):
    """A whole dependency is unusable (database or storage): the run stops."""

    def __init__(self, dependency: str):
        super().__init__(dependency)
        self.dependency = dependency


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobLog(logging.LoggerAdapter):
    """Prefixes every line with the run id (the job's correlation id; there is no HTTP request)."""

    def process(self, msg, kwargs):
        return f"run={self.extra['run_id']} {msg}", kwargs


def job_logger(name: str, run_id: str) -> JobLog:
    return JobLog(logging.getLogger(f"evidence.jobs.{name}"), {"run_id": run_id})


def new_run_id() -> str:
    return str(uuid.uuid4())


def precheck(database, storage) -> None:
    """Both stores must answer before anything is touched."""
    try:
        database.ping()
    except SQLAlchemyError:
        raise DependencyError("database") from None
    try:
        storage.check_bucket()
    except StorageUnavailable:
        raise DependencyError("storage") from None


def emit_summary(log: JobLog, job: str, run_id: str, summary: dict) -> None:
    line = json.dumps({"job": job, "run_id": run_id, **summary}, sort_keys=True)
    log.info("summary %s", line)
    print(line)
