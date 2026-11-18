"""Session revocation through AuthService (D-1): the only AuthService call of user administration.

Users are administered in-process (admin_local.users) on the PostgreSQL identity AuthService reads.
After a deactivation or a password reset is committed, the user's Redis sessions are revoked by
AuthService itself (POST /api/v1/auth/users/<id>/sessions/revoke -> token_store.revoke_user_tokens),
with the administrator's verified access token. The portal never touches the Redis key format.

A deactivated account is closed anyway on its next request (AuthService checks `user.active` and
`institution.active` in PostgreSQL on every /verify and /refresh); revocation also ends tokens of a
password reset, which PostgreSQL alone would not.
"""
import uuid

import requests
from flask import current_app, g


class RevocationFailed(Exception):
    pass


def revoke_user_sessions(user_id):
    """Revoke every session of `user_id`; returns how many. Raises RevocationFailed."""
    # MS-03: an open live camera WebSocket of the user is closed at once too (best effort, never raises).
    from services.vision_bridge import revoke_live_user
    revoke_live_user(user_id)
    token = getattr(g, 'auth_access_token', None)
    if not token:
        raise RevocationFailed('no verified access token in this request')
    try:
        response = requests.request(
            'POST', f"{current_app.config['AUTH_SERVICE_URL']}/api/v1/auth/users/{user_id}/sessions/revoke",
            timeout=current_app.config.get('AUTH_SERVICE_TIMEOUT', 5),
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json',
                     'X-Correlation-ID': uuid.uuid4().hex},
        )
    except requests.RequestException as exc:
        raise RevocationFailed(type(exc).__name__) from exc
    if response.status_code != 200:
        raise RevocationFailed(f'HTTP {response.status_code}')
    return (response.json() or {}).get('revoked', 0)
