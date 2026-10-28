"""Health checks of the portal (PEF-DS09 §1.3.7), for MonitoringService and container healthchecks.

Unauthenticated and cheap: /health/live never touches anything; /health/ready (and /health) run
SELECT 1 on PostgreSQL. They call no microservice (Auth, Session, VisionWorker...): the portal's
readiness is its own process and its own database, not the state of its dependencies, which
MonitoringService checks one by one.
"""

import re
import uuid

from flask import jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from controllers.routes import web_bp
from extensions import db


CORRELATION_ID = re.compile(r'^[A-Za-z0-9._:\-]{1,64}$')


def _correlation_id():
    incoming = request.headers.get('X-Correlation-ID', '')
    return incoming if CORRELATION_ID.fullmatch(incoming) else str(uuid.uuid4())


def _answer(ok_status, failed_status, checks):
    ok = all(value == 'ok' for value in checks.values())
    correlation_id = _correlation_id()
    response = jsonify({'status': ok_status if ok else failed_status, 'checks': checks, 'correlation_id': correlation_id})
    response.status_code = 200 if ok else 503
    response.headers['X-Correlation-ID'] = correlation_id
    return response


def _database():
    try:
        with db.engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        return 'ok'
    except SQLAlchemyError:
        return 'error'


@web_bp.route('/health/live')
def health_live():
    return _answer('live', 'live', {})


@web_bp.route('/health')
@web_bp.route('/health/ready')
def health_ready():
    return _answer('ready', 'not_ready', {'database': _database()})
