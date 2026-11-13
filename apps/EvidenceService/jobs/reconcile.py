"""Storage ↔ database reconciliation job (PEF-DS05 §1.3.2 rule 10).

    python -m jobs.reconcile [--dry-run] [--delete-orphans]

One streaming merge of two sorted sequences — the bucket listing (ListObjectsV2 pages, keys in
ascending byte order) and the bucket's ``media_asset`` rows (keyset batches ordered by object_key
COLLATE "C", the same byte order). Memory stays bounded by one page / one batch; no blob is read.

* **Object without row (orphan):** only keys that match the clinical format exactly —
  ``{institution_uuid}/{session_uuid}/{yyyy}/{asset_uuid}.{jpg|gif}`` — are considered; anything else
  (``models/…``, unknown prefixes, malformed keys) is reported as non-clinical and never deleted. A
  clinical orphan younger than EVIDENCE_ORPHAN_GRACE_SECONDS (age from the object's LastModified) is
  ignored: it may be an upload whose row is not committed yet. Older ones are candidates; they are
  deleted only with ``--delete-orphans`` (and not ``--dry-run``), after re-querying PostgreSQL for
  (bucket, key) right before the delete (a row committed meanwhile → kept).
* **Row without object (missing blob):** a row with ``purged_at`` NULL whose object is absent is
  confirmed with HeadObject and a re-read of the row, then only REPORTED (storage problem, failed purge
  bookkeeping, manual deletion…): no metadata change, no purged_at, no row delete, no re-upload. A
  purged row without object is correct; blocked rows are treated like any non-purged row.
* No ``access_audit``: an orphan has no clinical resource; deletions are in the technical log.
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from datetime import timedelta

from sqlalchemy.exc import InterfaceError, OperationalError

from jobs.common import (EXIT_DEPENDENCY, EXIT_FAILURES, EXIT_OK, DependencyError, emit_summary, job_logger,
                         new_run_id, precheck, utc_now)
from repositories import maintenance_repository as repo
from storage.s3_adapter import StorageOperationError

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CLINICAL_KEY = re.compile(rf"(?P<institution>{_UUID})/(?P<session>{_UUID})/(?P<year>\d{{4}})/(?P<asset>{_UUID})\.(jpg|gif)")
MIN_YEAR, MAX_YEAR = 2000, 2999


def is_clinical_key(key: str) -> bool:
    """Exactly the key format Evidence generates (DS05 §1.3.2 rule 2), lowercase UUIDs, plausible year."""
    match = _CLINICAL_KEY.fullmatch(key)
    if not match:
        return False
    try:
        for name in ("institution", "session", "asset"):
            uuid.UUID(match.group(name))
    except ValueError:
        return False
    return MIN_YEAR <= int(match.group("year")) <= MAX_YEAR


class ListingOrderError(Exception):
    """The storage listing was not in ascending key order: the merge cannot be trusted."""


def run_reconcile(database, storage, *, grace_seconds: int, batch_size: int, delete_orphans: bool = False,
                  dry_run: bool = False, prefix: str = "", page_size: int = 1000, run_id: str | None = None) -> dict:
    run_id = run_id or new_run_id()
    log = job_logger("reconcile", run_id)
    now = utc_now()
    grace = timedelta(seconds=grace_seconds)
    bucket = storage.bucket
    summary = {"objects_scanned": 0, "rows_scanned": 0, "orphan_candidates": 0, "orphans_deleted": 0,
               "orphans_kept_row_appeared": 0, "orphans_ignored_grace": 0, "nonclinical_ignored": 0,
               "missing_blob_candidates": 0, "purged_with_blob": 0, "failures": 0,
               "dry_run": dry_run, "delete_orphans": delete_orphans}
    report = {"orphans": [], "deleted": [], "missing": []}

    def on_orphan(key, last_modified):
        if not is_clinical_key(key):
            summary["nonclinical_ignored"] += 1
            return
        if now - last_modified < grace:
            summary["orphans_ignored_grace"] += 1
            return
        summary["orphan_candidates"] += 1
        report["orphans"].append(key)
        log.warning("orphan candidate bucket=%s object_key=%s", bucket, key)
        if not delete_orphans or dry_run:
            return
        try:
            with database.connect() as connection:
                if repo.asset_by_key(connection, bucket, key) is not None:   # upload finished during the scan
                    summary["orphans_kept_row_appeared"] += 1
                    return
            storage.delete_object(key)
        except (OperationalError, InterfaceError):
            raise DependencyError("database") from None
        except StorageOperationError:
            summary["failures"] += 1
            log.error("orphan delete failed object_key=%s", key)
            return
        summary["orphans_deleted"] += 1
        report["deleted"].append(key)
        log.warning("orphan deleted bucket=%s object_key=%s", bucket, key)

    def on_row_without_object(row):
        if row["purged_at"] is not None:
            return   # purged: the object is expected to be gone
        try:
            if storage.object_exists(row["object_key"]):
                return   # uploaded after it was passed in the listing
            with database.connect() as connection:
                current = repo.asset_by_key(connection, bucket, row["object_key"])
        except (OperationalError, InterfaceError):
            raise DependencyError("database") from None
        except StorageOperationError:
            summary["failures"] += 1
            return
        if current is None or current["purged_at"] is not None:
            return   # purged meanwhile
        summary["missing_blob_candidates"] += 1
        report["missing"].append(row["id"])
        log.error("missing blob candidate asset=%s", row["id"])

    objects = _ordered(storage.iter_objects(prefix, page_size=page_size))
    rows = repo.iter_bucket_rows(database, bucket, prefix, batch_size)
    try:
        obj, row = next(objects, None), next(rows, None)
        while obj is not None or row is not None:
            if row is None or (obj is not None and obj[0] < row["object_key"]):
                summary["objects_scanned"] += 1
                on_orphan(*obj)
                obj = next(objects, None)
            elif obj is None or row["object_key"] < obj[0]:
                summary["rows_scanned"] += 1
                on_row_without_object(row)
                row = next(rows, None)
            else:   # object and row match
                summary["objects_scanned"] += 1
                summary["rows_scanned"] += 1
                if row["purged_at"] is not None:
                    summary["purged_with_blob"] += 1   # reported only: purge marked done but object present
                    log.warning("purged asset with object present asset=%s", row["id"])
                obj, row = next(objects, None), next(rows, None)
    except StorageOperationError:
        raise DependencyError("storage") from None
    except (OperationalError, InterfaceError):
        raise DependencyError("database") from None
    summary["report"] = report
    return summary


def _ordered(objects):
    previous = None
    for key, last_modified in objects:
        if previous is not None and key <= previous:
            raise ListingOrderError()
        previous = key
        yield key, last_modified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.reconcile", description="Evidence storage reconciliation")
    parser.add_argument("--dry-run", action="store_true", help="report only (never deletes)")
    parser.add_argument("--delete-orphans", action="store_true",
                        help="delete clinical-format orphans older than the grace period")
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
    log = job_logger("reconcile", run_id)
    database, storage = create_database(settings), S3Storage(settings)
    try:
        precheck(database, storage)
        summary = run_reconcile(database, storage, grace_seconds=settings.orphan_grace_seconds,
                                batch_size=settings.purge_batch_size, delete_orphans=args.delete_orphans,
                                dry_run=args.dry_run, run_id=run_id)
    except DependencyError as exc:
        log.error("aborted: %s unavailable", exc.dependency)
        return EXIT_DEPENDENCY
    except ListingOrderError:
        log.error("aborted: storage listing not in ascending key order")
        return EXIT_DEPENDENCY
    summary.pop("report")
    emit_summary(log, "reconcile", run_id, summary)
    return EXIT_FAILURES if summary["failures"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
