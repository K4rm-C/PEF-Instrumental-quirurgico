import requests
from flask import current_app, g, request
from flask_babel import gettext

from i18n import N_


ERROR_MESSAGES = {
    'supervisor_service_unavailable': N_('The supervisor service is not available right now. Try again in a moment.'),
    'forbidden': N_('Your role cannot perform this operation.'),
    'token_required': N_('Your session expired. Sign in again.'),
    'invalid_token': N_('Your session expired. Sign in again.'),
    'token_revoked': N_('Your session expired. Sign in again.'),
    'user_inactive': N_('Your session expired. Sign in again.'),
    'database_unavailable': N_('The database is not available right now. Try again in a moment.'),
    'session_store_unavailable': N_('Your session could not be verified. Try again in a moment.'),
    'not_found': N_('Record not found.'),
    'session_not_found': N_('Session not found.'),
    'discrepancy_not_found': N_('Record not found.'),
    'kit_not_found': N_('Record not found.'),
    'instrument_not_found': N_('Record not found.'),
    'invalid_status': N_('Choose a valid status.'),
    'field_required': N_('Fill in every required field.'),
    'notes_required': N_('Review notes are required.'),
    'change_reason_required': N_('A change reason is required when cycle status or active status changes.'),
}


class SupervisorServiceError(Exception):
    def __init__(self, status, code, message='', field=None):
        super().__init__(message or code)
        self.status = status
        self.status_code = status
        self.code = code
        self.message = message
        self.field = field

    @property
    def user_message(self):
        msgid = ERROR_MESSAGES.get(self.code)
        if msgid:
            return gettext(msgid)
        if self.code.endswith('_required'):
            return gettext('Fill in every required field.')
        return self.message or gettext('Unable to save this record right now.')


def _client_ip():
    forwarded = request.headers.get('X-Forwarded-For') or request.remote_addr or ''
    return forwarded.split(',')[0].strip()


def call(method, path, *, params=None, json=None):
    """Call SupervisorService on behalf of the authenticated web user."""
    token = getattr(g, 'auth_access_token', None)
    headers = {'X-Forwarded-For': _client_ip()}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        response = requests.request(
            method,
            f"{current_app.config['SUPERVISOR_SERVICE_URL']}/api/v1{path}",
            headers=headers,
            params={key: value for key, value in (params or {}).items() if value not in (None, '')},
            json=json,
            timeout=current_app.config['SUPERVISOR_SERVICE_TIMEOUT'],
        )
    except requests.RequestException as exc:
        current_app.logger.warning('SupervisorService unreachable: %s', exc)
        raise SupervisorServiceError(503, 'supervisor_service_unavailable') from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.ok:
        return payload
    error = (payload or {}).get('error') or {}
    raise SupervisorServiceError(
        response.status_code,
        error.get('code') or (
            'supervisor_service_unavailable' if response.status_code >= 500 else 'error'
        ),
        error.get('message') or '',
        error.get('field'),
    )


def get(path, **params):
    return call('GET', path, params=params)


def post(path, json=None):
    return call('POST', path, json=json if json is not None else {})


def put(path, json):
    return call('PUT', path, json=json)
