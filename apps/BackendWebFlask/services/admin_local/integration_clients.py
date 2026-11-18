"""MS-07: machine clients of the institution (PEF-DS08 `integration_client`) administered by its IT admin.

- client_id is generated (public identifier); the secret is generated with `secrets`, shown ONCE to the
  administrator and stored only as a werkzeug hash (same scheme AuthService verifies). It is never logged,
  audited or retrievable afterwards: a lost secret is rotated.
- Scopes come from a closed list (DS08 + MS-07 sync scopes). A client is always of the administrator's
  institution (D-2); another institution's client does not exist for it (404).
- Deactivation and secret rotation revoke every live token of the client through AuthService
  (POST /api/v1/auth/clients/<id>/revoke). The IntegrationService also checks `active` and the current scopes
  in PostgreSQL on every request, so a deactivation is effective at once even if that call fails.
- Every change is audited (CREATE_ / UPDATE_ / ACTIVATE_ / DEACTIVATE_INTEGRATION_CLIENT,
  ROTATE_INTEGRATION_CLIENT_SECRET) without any secret.
"""
from __future__ import annotations

import json
import re
import secrets
import unicodedata
import uuid

import requests
from flask import current_app, g

from i18n import N_
from sqlalchemy import text as sql
from werkzeug.security import generate_password_hash

from . import audit
from .common import parse_uuid, status_flag, text
from .database import db
from .errors import bad_request, not_found

SCOPES = (
    ('sync.catalog.read', N_('Synchronize the catalog (families, kits, instruments, trays)')),
    ('sync.locations.read', N_('Synchronize rooms, capture stations and cameras')),
    ('sync.practitioners.read', N_('Synchronize physicians')),
    ('sync.sessions.read', N_('Synchronize counting session status (no patient data)')),
    ('fhir.location.read', N_('FHIR Location (operating rooms)')),
    ('fhir.practitioner.read', N_('FHIR Practitioner (physicians)')),
    ('fhir.encounter.read', N_('FHIR Encounter (operations)')),
    ('fhir.patient.read', N_('FHIR Patient (patient data)')),
    ('fhir.export.bundle', N_('FHIR export Bundle of an Encounter')),
    # MS-12: traceable batch export of closed counting sessions (no patient data).
    ('export.sessions.batch', N_('Batch export of closed counting sessions (no patient data)')),
)
SCOPE_CODES = tuple(code for code, _ in SCOPES)


class RevocationFailed(Exception):
    pass


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    return (re.sub(r'[^a-z0-9]+', '-', ascii_name.lower()).strip('-') or 'client')[:40]


def _row(r) -> dict:
    return {'id': str(r['id']), 'client_id': r['client_id'], 'name': r['name'], 'scopes': list(r['scopes'] or []),
            'active': bool(r['active']),
            'last_used_at': r['last_used_at'].isoformat() if r['last_used_at'] else None,
            'created_at': r['created_at'].isoformat() if r['created_at'] else None,
            'secret_rotated_at': r['secret_rotated_at'].isoformat() if r['secret_rotated_at'] else None}


def _scopes(payload) -> list[str]:
    raw = payload.get('scopes')
    values = raw if isinstance(raw, list) else [v for v in str(raw or '').replace(',', ' ').split() if v]
    unknown = sorted(set(values) - set(SCOPE_CODES))
    if unknown:
        raise bad_request('invalid_scope', 'Unknown scope: ' + ', '.join(unknown), 'scopes')
    if not values:
        raise bad_request('scopes_required', 'Choose at least one permission for the client.', 'scopes')
    return sorted(set(values))


_SELECT = ('SELECT id, client_id, name, scopes, active, last_used_at, created_at, secret_rotated_at '
           'FROM integration_client WHERE institution_id = :inst')


def list_clients(actor):
    rows = db.execute(sql(_SELECT + ' ORDER BY name'), {'inst': actor.institution_id}).mappings().all()
    return {'items': [_row(r) for r in rows], 'scopes': [{'code': c, 'label': l} for c, l in SCOPES]}


def _owned(actor, client_id):
    row_id = parse_uuid(client_id)
    row = db.execute(sql(_SELECT + ' AND id = :id'), {'inst': actor.institution_id, 'id': row_id}).mappings().first() \
        if row_id else None
    if not row:
        raise not_found('integration_client_not_found', 'Integration client not found.')
    return row


def get_client(actor, client_id):
    return _row(_owned(actor, client_id))


def create_client(actor, payload):
    name = text(payload, 'name', required=True, max_length=160, label='Name')
    scopes = _scopes(payload)
    secret = secrets.token_urlsafe(32)
    row_id, client_id = uuid.uuid4(), f'{_slug(name)}-{secrets.token_hex(4)}'
    db.execute(sql("""
        INSERT INTO integration_client (id, client_id, name, secret_hash, scopes, active, institution_id,
                                        created_by_user_id, secret_rotated_at, created_at, updated_at)
        VALUES (:id, :cid, :name, :hash, CAST(:scopes AS jsonb), :active, :inst, :by, now(), now(), now())
    """), {'id': row_id, 'cid': client_id, 'name': name, 'hash': generate_password_hash(secret),
           'scopes': json.dumps(scopes), 'active': status_flag(payload), 'inst': actor.institution_id,
           'by': actor.id})
    audit.record('CREATE_INTEGRATION_CLIENT', 'integration_client', row_id)
    db.commit()
    return {**get_client(actor, str(row_id)), 'client_secret': secret}


def update_client(actor, client_id, payload):
    row = _owned(actor, client_id)
    name = text(payload, 'name', required=True, max_length=160, label='Name')
    scopes = _scopes(payload)
    active = status_flag(payload)
    changed = (name, scopes) != (row['name'], sorted(row['scopes'] or []))
    db.execute(sql('UPDATE integration_client SET name = :n, scopes = CAST(:s AS jsonb), active = :a, updated_at = now() '
                   'WHERE id = :id'), {'n': name, 's': json.dumps(scopes), 'a': active, 'id': row['id']})
    audit.record_update('INTEGRATION_CLIENT', 'integration_client', row['id'], was_active=row['active'],
                        is_active=active, changed=changed)
    db.commit()
    warning = None
    if row['active'] and not active:
        warning = _revoke_quietly(row['id'])
    return {**get_client(actor, client_id), 'revocation_warning': warning}


def rotate_secret(actor, client_id):
    row = _owned(actor, client_id)
    secret = secrets.token_urlsafe(32)
    db.execute(sql('UPDATE integration_client SET secret_hash = :h, secret_rotated_at = now(), updated_at = now() '
                   'WHERE id = :id'), {'h': generate_password_hash(secret), 'id': row['id']})
    audit.record('ROTATE_INTEGRATION_CLIENT_SECRET', 'integration_client', row['id'])
    db.commit()
    return {**get_client(actor, client_id), 'client_secret': secret, 'revocation_warning': _revoke_quietly(row['id'])}


def _revoke_quietly(row_id):
    try:
        revoke_client_tokens(row_id)
        return None
    except RevocationFailed as exc:
        current_app.logger.warning('could not revoke the tokens of integration client %s: %s', row_id, exc)
        return 'revocation_failed'


def revoke_client_tokens(row_id):
    token = getattr(g, 'auth_access_token', None)
    if not token:
        raise RevocationFailed('no verified access token in this request')
    try:
        response = requests.post(
            f"{current_app.config['AUTH_SERVICE_URL']}/api/v1/auth/clients/{row_id}/revoke",
            timeout=current_app.config.get('AUTH_SERVICE_TIMEOUT', 5),
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json', 'X-Correlation-ID': uuid.uuid4().hex})
    except requests.RequestException as exc:
        raise RevocationFailed(type(exc).__name__) from exc
    if response.status_code != 200:
        raise RevocationFailed(f'HTTP {response.status_code}')
    return (response.json() or {}).get('revoked', 0)
