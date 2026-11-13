"""MS-11: backup / restore check of Garage (S3) buckets: evidence (pef-evidence) and Administration files incl. YOLO weights
(pef-admin). Uses boto3 (already in the EvidenceService venv); credentials come from the environment, never arguments:

    set -a; . /etc/pef/evidence.env; set +a          # S3_INTERNAL_ENDPOINT, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY

    mirror   python s3_mirror.py mirror  --bucket pef-evidence --dest /var/backups/pef/<date>/s3
             READ-ONLY on the bucket: downloads every object to <dest>/<bucket>/<key> + MANIFEST.<bucket>.tsv
             (key, size, sha256). Existing files with the same size and sha256 are skipped (incremental).
    verify   python s3_mirror.py verify  --bucket pef-evidence --dest /var/backups/pef/<date>/s3
             READ-ONLY: every manifest entry exists in the bucket with the same size, every local file has its sha256.
    restore  python s3_mirror.py restore --dest /var/backups/pef/<date>/s3 --from-bucket pef-evidence --to-bucket <bucket>
             Uploads the backup into --to-bucket. Refuses to write into a bucket that already has objects unless
             --allow-existing is given: the restore TEST goes to an empty scratch bucket, never over production.

Exit code 0 only when everything matches. Never prints credentials.
"""
import argparse
import csv
import hashlib
import os
import sys
from pathlib import Path

import boto3
from botocore.config import Config


def client():
    endpoint = os.environ.get('S3_INTERNAL_ENDPOINT') or os.environ.get('S3_ENDPOINT')
    key = os.environ.get('S3_ACCESS_KEY_ID') or os.environ.get('S3_ACCESS_KEY')
    secret = os.environ.get('S3_SECRET_ACCESS_KEY') or os.environ.get('S3_SECRET_KEY')
    if not (endpoint and key and secret):
        sys.exit('S3_INTERNAL_ENDPOINT / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY are not set (load /etc/pef/evidence.env)')
    return boto3.client('s3', endpoint_url=endpoint, region_name=os.environ.get('S3_REGION', 'garage'),
                        aws_access_key_id=key, aws_secret_access_key=secret,
                        config=Config(s3={'addressing_style': 'path'}, retries={'max_attempts': 5}))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def objects(s3, bucket):
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket):
        yield from page.get('Contents', [])


def safe_target(root: Path, key: str) -> Path:
    target = (root / key).resolve()
    if root.resolve() not in target.parents:
        raise SystemExit(f'refused: object key escapes the backup directory: {key!r}')
    return target


def manifest_path(dest: Path, bucket: str) -> Path:
    return dest / f'MANIFEST.{bucket}.tsv'


def read_manifest(dest: Path, bucket: str):
    with manifest_path(dest, bucket).open(encoding='utf-8', newline='') as fh:
        return [(row[0], int(row[1]), row[2]) for row in csv.reader(fh, delimiter='\t')]


def mirror(s3, bucket, dest: Path):
    root = dest / bucket
    root.mkdir(parents=True, exist_ok=True)
    rows, copied, skipped = [], 0, 0
    previous = {}
    if manifest_path(dest, bucket).exists():
        previous = {k: (size, digest) for k, size, digest in read_manifest(dest, bucket)}
    for obj in objects(s3, bucket):
        key, size = obj['Key'], obj['Size']
        target = safe_target(root, key)
        if target.exists() and target.stat().st_size == size and key in previous and previous[key][0] == size:
            digest = previous[key][1]
            skipped += 1
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + '.part')
            s3.download_file(bucket, key, str(tmp))
            if tmp.stat().st_size != size:
                raise SystemExit(f'size mismatch while downloading {key}')
            tmp.replace(target)
            digest = sha256(target)
            copied += 1
        rows.append((key, size, digest))
    with manifest_path(dest, bucket).open('w', encoding='utf-8', newline='') as fh:
        csv.writer(fh, delimiter='\t').writerows(rows)
    print(f'{bucket}: {len(rows)} objects ({copied} downloaded, {skipped} unchanged) -> {root}')
    return True


def verify(s3, bucket, dest: Path, against=None):
    root = dest / (against or bucket)
    remote = {o['Key']: o['Size'] for o in objects(s3, bucket)}
    problems = []
    entries = read_manifest(dest, against or bucket)
    for key, size, digest in entries:
        local = safe_target(root, key)
        if not local.exists() or local.stat().st_size != size or sha256(local) != digest:
            problems.append(f'local copy damaged or missing: {key}')
        if remote.get(key) != size:
            problems.append(f'not in bucket {bucket} with the same size: {key}')
    extra = len(set(remote) - {k for k, _, _ in entries})
    for line in problems[:20]:
        print('FAIL ', line)
    print(f'{bucket}: {len(entries)} manifest entries, {len(problems)} problem(s), {extra} object(s) newer than the backup')
    return not problems


def restore(s3, source_bucket, target_bucket, dest: Path, allow_existing):
    existing = {b['Name'] for b in s3.list_buckets().get('Buckets', [])}
    if target_bucket not in existing:
        s3.create_bucket(Bucket=target_bucket)
    elif any(True for _ in objects(s3, target_bucket)) and not allow_existing:
        raise SystemExit(f'refused: bucket {target_bucket} is not empty (restore tests go to an EMPTY scratch bucket)')
    root = dest / source_bucket
    entries = read_manifest(dest, source_bucket)
    for key, _, _ in entries:
        s3.upload_file(str(safe_target(root, key)), target_bucket, key)
    print(f'{len(entries)} objects uploaded to {target_bucket}')
    return verify(s3, target_bucket, dest, against=source_bucket)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('action', choices=('mirror', 'verify', 'restore'))
    parser.add_argument('--bucket', help='bucket to mirror / verify')
    parser.add_argument('--dest', required=True, type=Path, help='backup directory')
    parser.add_argument('--from-bucket', help='restore: which backed-up bucket')
    parser.add_argument('--to-bucket', help='restore: target bucket (empty scratch bucket for a test)')
    parser.add_argument('--allow-existing', action='store_true')
    args = parser.parse_args()
    s3 = client()
    if args.action == 'mirror':
        ok = mirror(s3, args.bucket, args.dest)
    elif args.action == 'verify':
        ok = verify(s3, args.bucket, args.dest)
    else:
        if not (args.from_bucket and args.to_bucket):
            sys.exit('restore needs --from-bucket and --to-bucket')
        ok = restore(s3, args.from_bucket, args.to_bucket, args.dest, args.allow_existing)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
