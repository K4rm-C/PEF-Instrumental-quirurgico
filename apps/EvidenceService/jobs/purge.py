"""Retention / purge job (PEF-DS05 lifecycle rules 1–3, §1.3.2 rule 12).

    python -m jobs.purge [--dry-run]

Candidates: clinical rows of the evidence bucket with ``purged_at`` NULL and (``purge_requested_at``
set, or ``retention_until`` <= now). ``blocked_at`` alone never purges.

Per asset, one short transaction:
  1. ``SELECT … WHERE id = ? AND <still a candidate> FOR UPDATE SKIP LOCKED`` (row locked alone);
  2. DeleteObject through the INTERNAL endpoint (bounded by the dependency timeout, 1 attempt);
  3. ``UPDATE … SET purged_at = now WHERE purged_at IS NULL``; COMMIT.
The row lock is held only for that one bounded DELETE: it blocks no reader (GET) and no upload, and
it guarantees the object is deleted only while the row is verifiably a candidate (a purge is
irreversible, so the decision and the delete stay under the same lock). A second worker skips a
locked row (SKIP LOCKED) and later finds it purged: purged_at is set exactly once.

Idempotent and retry-safe: a missing object deletes successfully (S3 semantics). If the delete
succeeded but the UPDATE/commit failed, the row stays unpurged and the next run deletes again (no-op)
and marks it — eventual consistency; the blob is never restored or copied. A storage failure leaves
``purged_at`` NULL (the asset stays a candidate). Rows are never deleted.

Audit: ``access_audit`` only accepts a ``user`` or ``client`` actor (CHECK) and the job is neither, so
no ``evidence.purge`` row is written (no fake actor); the technical log with the run id is the record
until a DS08 machine identity exists.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError

from jobs.common import (EXIT_DEPENDENCY, EXIT_FAILURES, EXIT_OK, DependencyError, emit_summary, job_logger,
                         new_run_id, precheck, utc_now)
from repositories import maintenance_repository as repo
from storage.s3_adapter import StorageOperationError


def run_purge(database, storage, *, batch_size: int, dry_run: bool = False, run_id: str | None = None) -> dict:
    run_id = run_id or new_run_id()
    log = job_logger("purge", run_id)
    summary = {"scanned": 0, "eligible": 0, "purged": 0, "skipped": 0, "failed": 0, "dry_run": dry_run}
    bucket = storage.bucket
    after = None
    while True:
        try:
            with database.connect() as connection:
                batch = repo.purge_candidates(connection, bucket, utc_now(), after, batch_size)
        except (OperationalError, InterfaceError):
            raise DependencyError("database") from None
        if not batch:
            break
        after = batch[-1]["id"]
        summary["scanned"] += len(batch)
        for candidate in batch:
            if dry_run:
                summary["eligible"] += 1
                log.info("dry-run candidate asset=%s reason=%s", candidate["id"], candidate["reason"])
                continue
            outcome = _purge_one(database, storage, candidate["id"], bucket, log)
            summary[outcome] += 1
            if outcome != "skipped":   # confirmed as a candidate under lock
                summary["eligible"] += 1
        if len(batch) < batch_size:
            break
    return summary


def _purge_one(database, storage, asset_id, bucket: str, log) -> str:
    try:
        with database.begin() as connection:
            now = utc_now()
            row = repo.lock_purge_candidate(connection, asset_id, bucket, now)
            if row is None:
                return "skipped"   # purged meanwhile, no longer a candidate, or locked by another worker
            storage.delete_object(row["object_key"])
            repo.mark_purged(connection, asset_id, now)
        log.info("purged asset=%s", asset_id)
        return "purged"
    except StorageOperationError:
        log.error("purge failed asset=%s step=storage_delete (stays a candidate)", asset_id)
        return "failed"
    except (OperationalError, InterfaceError):
        raise DependencyError("database") from None
    except SQLAlchemyError as exc:
        log.error("purge failed asset=%s step=database (%s); object may already be deleted, "
                  "the next run completes it", asset_id, type(exc).__name__)
        return "failed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.purge", description="Evidence retention / purge job")
    parser.add_argument("--dry-run", action="store_true", help="only list candidates (no delete, no update)")
    args = parser.parse_args(argv)

    from config import ConfigError, Settings
    from correlation import configure_logging
    from database import create_database
    from storage.s3_adapter import S3Storage

    run_id = new_run_id()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    configure_logging(settings.log_level)
    log = job_logger("purge", run_id)
    database, storage = create_database(settings), S3Storage(settings)
    try:
        precheck(database, storage)
        summary = run_purge(database, storage, batch_size=settings.purge_batch_size, dry_run=args.dry_run,
                            run_id=run_id)
    except DependencyError as exc:
        log.error("aborted: %s unavailable", exc.dependency)
        return EXIT_DEPENDENCY
    emit_summary(log, "purge", run_id, summary)
    return EXIT_FAILURES if summary["failed"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
