#!/usr/bin/env python3
"""Ensure Garage buckets privacy-notices and ADMIN_S3_BUCKET exist (idempotent)."""
from __future__ import annotations

import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def main() -> int:
    endpoint = os.environ.get("S3_ENDPOINT") or os.environ.get("S3_INTERNAL_ENDPOINT") or "http://127.0.0.1:3900"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get("S3_REGION", "garage"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY") or os.environ.get("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY") or os.environ.get("S3_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    for attempt in range(45):
        try:
            client.list_buckets()
            break
        except Exception:
            if attempt == 44:
                raise
            time.sleep(2)

    admin = os.environ.get("ADMIN_S3_BUCKET", "pef-admin")
    for name in ("privacy-notices", admin):
        try:
            client.head_bucket(Bucket=name)
            print(f"ok exists {name}")
        except ClientError:
            client.create_bucket(Bucket=name)
            print(f"ok created {name}")
    print("garage-init done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
