"""Multipart upload handling for POST /api/v1/evidence (PEF-DS09 §1.3.5, PEF-DS05 §1.3.2 rules 6–8).

- ``EvidenceRequest`` makes Werkzeug write every uploaded file part to an on-disk
  ``tempfile.TemporaryFile`` (never the default in-memory spool), bounds the number of parts and
  the memory of text fields. Flask's MAX_CONTENT_LENGTH (largest per-type limit + 64 KiB of
  multipart overhead) caps the whole request envelope; the real per-file limit is enforced here
  by counting bytes.
- ``parse_upload_form`` accepts exactly one ``session_id`` (text), one ``kind`` (text) and one
  ``file`` (file part); anything missing, repeated or extra → 400. The client filename and the
  part's declared Content-Type are ignored.
- ``inspect_file`` streams the temporary file in chunks: counts bytes (stop at limit+1 → 413),
  updates SHA-256 incrementally and keeps only the first bytes for the signature check. The
  bytes, not the filename/extension/declared type, decide the type; it must match ``kind``.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from typing import BinaryIO

from flask import Request, request
from werkzeug.datastructures import FileStorage

from errors import ApiError

MULTIPART_MIMETYPE = "multipart/form-data"
MULTIPART_OVERHEAD_BYTES = 64 * 1024
UPLOAD_PARTS = ("session_id", "kind", "file")
CHUNK_SIZE = 64 * 1024
SIGNATURE_BYTES = 8

# kind → (canonical content type, object key extension). DS05 §1.3.2 rule 2: jpg / gif.
KINDS = {"jpeg": ("image/jpeg", "jpg"), "gif": ("image/gif", "gif")}


def new_upload_tempfile() -> BinaryIO:
    """On-disk temporary file for an uploaded part (deleted when closed). Patched by tests to track it."""
    return tempfile.TemporaryFile("w+b")


class EvidenceRequest(Request):
    max_form_parts = 10                 # session_id, kind, file + headroom for a clean 400
    # Bounds text-field memory. Werkzeug's multipart decoder also applies it to its internal buffer,
    # which holds ~64 KiB input chunks, so it must stay well above that (256 KiB).
    max_form_memory_size = 256 * 1024

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        return new_upload_tempfile()


def max_request_bytes(jpeg_max: int, gif_max: int) -> int:
    """Multipart envelope limit: the largest per-file limit plus multipart overhead."""
    return max(jpeg_max, gif_max) + MULTIPART_OVERHEAD_BYTES


@dataclass(frozen=True)
class UploadForm:
    session_id: str
    kind: str
    file: FileStorage


def parse_upload_form() -> UploadForm:
    if request.mimetype != MULTIPART_MIMETYPE:
        raise ApiError(400, "Content-Type must be multipart/form-data",
                       details={"content_type": request.mimetype or None})
    form, files = request.form, request.files
    unknown = sorted((set(form.keys()) | set(files.keys())) - set(UPLOAD_PARTS))
    if unknown:
        raise ApiError(400, "Unknown parts in multipart body", details={"unknown_fields": unknown})
    for name in ("session_id", "kind"):
        if name in files:
            raise ApiError(400, f"{name} must be a text part", details={"field": name})
    if "file" in form:
        raise ApiError(400, "file must be a file part", details={"field": "file"})
    missing = [name for name in ("session_id", "kind") if name not in form] + (["file"] if "file" not in files else [])
    if missing:
        raise ApiError(400, "Required parts are missing", details={"missing_fields": missing})
    repeated = [name for name in ("session_id", "kind") if len(form.getlist(name)) > 1]
    repeated += ["file"] if len(files.getlist("file")) > 1 else []
    if repeated:
        raise ApiError(400, "Each part must appear exactly once", details={"repeated_fields": repeated})
    return UploadForm(session_id=form["session_id"].strip(), kind=form["kind"].strip(), file=files["file"])


def validate_kind(kind: str) -> tuple[str, str]:
    if kind not in KINDS:
        raise ApiError(400, "kind must be jpeg or gif", details={"field": "kind", "allowed": sorted(KINDS)})
    return KINDS[kind]


def detected_kind(head: bytes) -> str | None:
    """Type from the file signature: JPEG = FF D8 FF; GIF = GIF87a / GIF89a."""
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


@dataclass(frozen=True)
class InspectedFile:
    size: int
    sha256: str


def inspect_file(stream: BinaryIO, kind: str, max_bytes: int) -> InspectedFile:
    """Stream once: size (≤ max_bytes), SHA-256 and signature; leaves the stream at position 0."""
    stream.seek(0)
    digest = hashlib.sha256()
    head = b""
    size = 0
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise ApiError(413, "File exceeds the size limit for its kind",
                           details={"field": "file", "kind": kind, "max_bytes": max_bytes})
        if len(head) < SIGNATURE_BYTES:
            head += chunk[:SIGNATURE_BYTES - len(head)]
        digest.update(chunk)
    if size == 0:
        raise ApiError(400, "file is empty", details={"field": "file"})
    if detected_kind(head) != kind:
        raise ApiError.validation_error("File content does not match kind", details={"field": "file", "kind": kind})
    stream.seek(0)
    return InspectedFile(size=size, sha256=digest.hexdigest())
