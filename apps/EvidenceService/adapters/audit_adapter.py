"""Transitional audit writer (PEF-DS07 `access_audit`, PEF-DS09 §1.3.5 rule 7).

Until an Audit Service exists, Evidence writes `access_audit` directly — and ONLY through this
module. Stored: occurred_at, actor (user), action, resource (`media_asset` id), institution,
outcome, correlation_id (the effective X-Correlation-ID, at most 64 characters). Never stored:
signed URLs, bucket, object keys, tokens, credentials, request bodies or IP.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.engine import Connection

from repositories.tables import access_audit

ACTION_DOWNLOAD_URL = "evidence.download_url"   # each emission of a signed URL (Phase B)
ACTION_UPLOAD = "evidence.upload"               # each new clinical asset (Phase C)
ACTOR_TYPE = "user"
RESOURCE_TYPE = "media_asset"
OUTCOME = "success"


def record(
    connection: Connection,
    *,
    action: str,
    actor_user_id: uuid.UUID,
    asset_id: uuid.UUID,
    institution_id: uuid.UUID,
    correlation_id: str | None,
    occurred_at: datetime,
) -> None:
    connection.execute(
        access_audit.insert().values(
            id=uuid.uuid4(),
            occurred_at=occurred_at,
            actor_type=ACTOR_TYPE,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=RESOURCE_TYPE,
            resource_id=asset_id,
            institution_id=institution_id,
            outcome=OUTCOME,
            correlation_id=correlation_id,
        )
    )
