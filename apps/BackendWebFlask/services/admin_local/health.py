"""RF-AD-01 system health for the Administration dashboard (D-3: local pings, no MonitoringService).

"Auth/PG/Redis/Mongo/storage alcanzables (sin fingir microservicios)": each dependency is checked
independently with a short timeout and reported as ok / error / not_configured; a failing one never
blocks the others nor the rest of the dashboard. Nothing is written anywhere.
- auth: GET {AUTH_SERVICE_URL}/health (the same AuthService every role signs in with).
- postgresql: SELECT 1 on the portal's database.
- redis: RESP `AUTH` + `PING` over a raw socket to REDIS_URL (the portal has no Redis client).
- mongodb: `ping` with MONGO_URL (pymongo, already a portal dependency).
- storage: the Administration object storage (admin_local.storage.ping).
"""
import os
import socket
import time
from urllib.parse import unquote, urlsplit

import requests
from flask import current_app
from sqlalchemy import text as sql

from . import storage
from .database import db

TIMEOUT = 2.0


def _timed(check):
    started = time.monotonic()
    try:
        status, detail = check()
    except Exception as exc:  # noqa: BLE001 - a health check reports, it never raises
        status, detail = 'error', type(exc).__name__
    return {'status': status, 'detail': detail, 'ms': round((time.monotonic() - started) * 1000)}


def _auth():
    response = requests.get(f"{current_app.config['AUTH_SERVICE_URL']}/health", timeout=TIMEOUT)
    return ('ok' if response.ok else 'error'), f'HTTP {response.status_code}'


def _postgresql():
    db.execute(sql('SELECT 1')).scalar()
    return 'ok', 'SELECT 1'


def _redis():
    url = os.environ.get('REDIS_URL', '').strip()
    if not url:
        return 'not_configured', 'REDIS_URL'
    parts = urlsplit(url)
    with socket.create_connection((parts.hostname or 'localhost', parts.port or 6379), timeout=TIMEOUT) as conn:
        conn.settimeout(TIMEOUT)
        commands = []
        if parts.password:
            args = ['AUTH'] + ([unquote(parts.username)] if parts.username else []) + [unquote(parts.password)]
            commands.append(args)
        commands.append(['PING'])
        for args in commands:
            conn.sendall(f'*{len(args)}\r\n'.encode() + b''.join(
                f'${len(arg.encode())}\r\n'.encode() + arg.encode() + b'\r\n' for arg in args))
            reply = conn.recv(256)
            if reply.startswith(b'-'):
                return 'error', reply[1:].split(b'\r\n')[0].decode(errors='replace')[:80]
        return ('ok', 'PONG') if reply.startswith(b'+PONG') else ('error', 'unexpected reply')


def _mongodb():
    url = os.environ.get('MONGO_URL', '').strip()
    if not url:
        return 'not_configured', 'MONGO_URL'
    from pymongo import MongoClient
    client = MongoClient(url, serverSelectionTimeoutMS=int(TIMEOUT * 1000), connectTimeoutMS=int(TIMEOUT * 1000))
    try:
        client.admin.command('ping')
    finally:
        client.close()
    return 'ok', 'ping'


def _storage():
    ok, detail = storage.ping()
    return ('ok' if ok else 'error'), detail


CHECKS = ('auth', 'postgresql', 'redis', 'mongodb', 'storage')


def check_all():
    results = {}
    for name in CHECKS:
        results[name] = _timed(globals()[f'_{name}'])
        if name == 'postgresql' and results[name]['status'] != 'ok':
            db.rollback()
    return results
