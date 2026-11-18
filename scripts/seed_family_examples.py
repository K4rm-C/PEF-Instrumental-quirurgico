#!/usr/bin/env python3
"""Upload data/seeds/example_images into Admin object storage and link family_example rows.

Idempotent: if a family already has an example with the same SHA-256, it is skipped.
If it has a different image, the previous object is deleted and replaced.

Usage (from repo root PEF-Instrumental-quirurgico):

    python scripts/seed_family_examples.py

Env (same as portal Admin storage; load .env first if needed):
    DATABASE_URL or POSTGRES_* 
    ADMIN_STORAGE_BACKEND=s3|local  (default s3 for this seed)
    ADMIN_S3_BUCKET=pef-admin
    S3_ENDPOINT / S3_INTERNAL_ENDPOINT (host: http://127.0.0.1:3900)
    S3_ACCESS_KEY / S3_SECRET_KEY
    ADMIN_STORAGE_DIR  (when backend=local)
"""
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "data" / "seeds" / "example_images"
PREFIX = "family-examples/"

# Filenames: NN_CODE.jpg → instrument_family.code
CODE_BY_STEM = {
    "00_ADSON_PLAIN": "ADSON_PLAIN",
    "01_ADSON_TEETH": "ADSON_TEETH",
    "02_FOERSTER": "FOERSTER",
    "03_ALLIS": "ALLIS",
    "04_KELLY_FORCEPS": "KELLY_FORCEPS",
    "05_ROCHESTER": "ROCHESTER",
    "06_POZZI": "POZZI",
    "07_OLSENHEG": "OLSENHEG",
    "08_MAYOHEG": "MAYOHEG",
    "09_MAYO_CURVED": "MAYO_CURVED",
    "10_MAYO_STRAIGHT": "MAYO_STRAIGHT",
    "11_FARABEUF": "FARABEUF",
    "12_SCALPEL3": "SCALPEL3",
    "13_MOSQUITO": "MOSQUITO",
    "14_SCALPEL4": "SCALPEL4",
    "15_CRILE_WOOD": "CRILE_WOOD",
    "16_PROBE": "PROBE",
}


def _load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(*names, default=""):
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _database_url() -> str:
    url = _env("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    user = _env("POSTGRES_USER", default="MedAdmin")
    password = _env("POSTGRES_PASSWORD", default="MedMin027")
    host = _env("POSTGRES_HOST", default="127.0.0.1")
    port = _env("POSTGRES_PORT", default="5433")
    db = _env("POSTGRES_DB", default="InstruMed")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _content_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp"}.get(ext, "application/octet-stream")


def _s3_client():
    import boto3
    from botocore.config import Config as BotoConfig

    endpoint = _env("S3_ENDPOINT", "S3_INTERNAL_ENDPOINT", "S3_PUBLIC_ENDPOINT",
                    default="http://127.0.0.1:3900")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=_env("S3_REGION", default="garage"),
        aws_access_key_id=_env("S3_ACCESS_KEY_ID", "S3_ACCESS_KEY"),
        aws_secret_access_key=_env("S3_SECRET_ACCESS_KEY", "S3_SECRET_KEY"),
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def _ensure_bucket(client, bucket: str) -> None:
    from botocore.exceptions import ClientError

    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def put_object(data: bytes, content_type: str) -> tuple[str, str, str]:
    """Returns (bucket, object_key, gcs_uri)."""
    backend = _env("ADMIN_STORAGE_BACKEND", default="s3").lower()
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(content_type, ".bin")
    key = f"{PREFIX}{uuid.uuid4().hex}{ext}"

    if backend == "s3":
        bucket = _env("ADMIN_S3_BUCKET", default="pef-admin")
        client = _s3_client()
        _ensure_bucket(client, bucket)
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return bucket, key, f"s3://{bucket}/{key}"

    bucket = "local"
    root = Path(_env(
        "ADMIN_STORAGE_DIR",
        default=str(ROOT / "apps" / "BackendWebFlask" / "instance" / "admin_storage"),
    ))
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return bucket, key, f"local://admin-storage/{key}"


def delete_object(bucket: str, key: str) -> None:
    if not bucket or not key:
        return
    backend = _env("ADMIN_STORAGE_BACKEND", default="s3").lower()
    if backend == "s3" and bucket != "local":
        try:
            _s3_client().delete_object(Bucket=bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: could not delete s3://{bucket}/{key}: {exc}", file=sys.stderr)
        return
    root = Path(_env("ADMIN_STORAGE_DIR", default=str(ROOT / "apps" / "BackendWebFlask" / "instance" / "admin_storage")))
    path = (root / key).resolve()
    if root.resolve() in path.parents:
        path.unlink(missing_ok=True)


def main() -> int:
    _load_dotenv()
    if not IMAGES.is_dir():
        print(f"missing images dir: {IMAGES}", file=sys.stderr)
        return 1

    try:
        import psycopg
    except ImportError:
        print("psycopg is required: pip install 'psycopg[binary]'", file=sys.stderr)
        return 1

    files = sorted(p for p in IMAGES.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    if not files:
        print(f"no images in {IMAGES}", file=sys.stderr)
        return 1

    backend = _env("ADMIN_STORAGE_BACKEND", default="s3").lower()
    print(f"backend={backend} images={len(files)} db={_database_url().split('@')[-1]}")

    uploaded = skipped = replaced = 0
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            for path in files:
                code = CODE_BY_STEM.get(path.stem)
                if not code:
                    print(f"skip unknown file {path.name}")
                    continue
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                content_type = _content_type(path)

                cur.execute(
                    "SELECT id, name FROM instrument_family WHERE code = %s",
                    (code,),
                )
                family = cur.fetchone()
                if family is None:
                    print(f"MISSING family code={code} ({path.name})")
                    continue
                family_id, family_name = family

                cur.execute(
                    """
                    SELECT fe.id, ma.id, ma.sha256, ma.bucket, ma.object_key
                    FROM family_example fe
                    LEFT JOIN media_asset ma ON ma.id = fe.media_asset_id
                    WHERE fe.family_id = %s
                    ORDER BY fe.sort_order, fe.id
                    LIMIT 1
                    """,
                    (family_id,),
                )
                existing = cur.fetchone()
                if existing and existing[2] == digest:
                    print(f"OK skip {code} (same sha256)")
                    skipped += 1
                    continue

                bucket, key, uri = put_object(data, content_type)
                asset_id = uuid.uuid4()
                kind = "jpeg" if content_type == "image/jpeg" else "other"
                cur.execute(
                    """
                    INSERT INTO media_asset (
                        id, gcs_uri, bucket, object_key, content_type, kind,
                        storage_provider, sha256, size_bytes, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'other', %s, %s, now(), now()
                    )
                    """,
                    (asset_id, uri, bucket, key, content_type, kind, digest, len(data)),
                )

                if existing:
                    example_id, old_asset_id, _old_sha, old_bucket, old_key = existing
                    cur.execute(
                        "UPDATE family_example SET media_asset_id = %s, example_text = %s, sort_order = 0 WHERE id = %s",
                        (asset_id, f"Reference photo — {family_name}", example_id),
                    )
                    if old_asset_id:
                        cur.execute("DELETE FROM media_asset WHERE id = %s", (old_asset_id,))
                        delete_object(old_bucket, old_key)
                    print(f"OK replace {code} -> {uri}")
                    replaced += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO family_example (id, family_id, media_asset_id, example_text, sort_order)
                        VALUES (%s, %s, %s, %s, 0)
                        """,
                        (uuid.uuid4(), family_id, asset_id, f"Reference photo — {family_name}"),
                    )
                    print(f"OK upload {code} -> {uri}")
                    uploaded += 1
        conn.commit()

    print(f"done uploaded={uploaded} replaced={replaced} skipped={skipped}")
    return 0


if __name__ == "__main__":
    # Clean accidental broken key line if present in older edits — put_object rebuilds key.
    raise SystemExit(main())
