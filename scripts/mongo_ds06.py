"""PEF-DS06 MongoDB operations from the console (see GUIA_MONGODB.md).

  python scripts/mongo_ds06.py init                 create both collections, validators and indexes
  python scripts/mongo_ds06.py indexes              list indexes and validators
  python scripts/mongo_ds06.py session <uuid>       checkpoints, telemetry and aggregations of a session
  python scripts/mongo_ds06.py verify-ids           every session_id in MongoDB exists in PostgreSQL

env: MONGO_URL, MONGO_DB (default pef), DATABASE_URL (verify-ids). Uses the same schema definitions as
the writers (BackendWebFlask services/mongo_checkpoints.py and VisionWorker telemetry.py).
"""
import json
import os
import sys
from pathlib import Path

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'BackendWebFlask'))


def _db():
    return MongoClient(os.environ['MONGO_URL'], serverSelectionTimeoutMS=3000, tz_aware=True)[
        os.environ.get('MONGO_DB') or 'pef']


def _print(value):
    print(json.dumps(value, default=str, indent=2, ensure_ascii=False))


def init():
    from services import mongo_checkpoints
    db = _db()
    mongo_checkpoints.ensure_schema(db)
    sys.path.insert(0, str(ROOT / 'apps' / 'VisionWorker'))
    os.environ.setdefault('VISION_UPLOAD_DIR', str(ROOT / 'apps' / 'VisionWorker' / 'uploads'))
    import telemetry
    telemetry.ensure_schema(db)
    print(f'collections ready in {db.name}: {sorted(db.list_collection_names())}')


def indexes():
    db = _db()
    for name in ('checkpoint_frame', 'session_telemetry'):
        options = db.command('listCollections', filter={'name': name})['cursor']['firstBatch']
        print(f'== {name}  validationLevel={options[0]["options"].get("validationLevel") if options else None}')
        _print(db[name].index_information())


def session(session_id):
    from services import mongo_checkpoints as mc
    db = _db()
    docs = mc.list_checkpoints(session_id, db=db)
    print(f'== checkpoint_frame ({len(docs)})')
    for doc in docs:
        print(f"#{doc['session_checkpoint_seq']} {doc['captured_at']:%Y-%m-%d %H:%M:%S} {doc['reason']:<12} "
              f"phase={doc.get('phase_code', '-')} detections={len(doc['detections'])} media={doc.get('media_asset_id', '-')}")
    print('== checkpoints by reason')
    _print(mc.checkpoint_summary(session_id, db=db))
    print('== telemetry summary')
    _print(mc.telemetry_summary(session_id, db=db))


def verify_ids():
    import psycopg
    db = _db()
    ids = set(db.checkpoint_frame.distinct('session_id')) | set(db.session_telemetry.distinct('session_id'))
    dsn = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
    with psycopg.connect(dsn) as conn:
        found = {str(r[0]) for r in conn.execute('SELECT id FROM work_session WHERE id::text = ANY(%s)', (list(ids),))}
    missing = sorted(ids - found)
    print(f'{len(ids)} session ids in MongoDB, {len(found)} found in work_session, missing: {missing}')
    return 1 if missing else 0


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command == 'init':
        init()
    elif command == 'indexes':
        indexes()
    elif command == 'session' and len(sys.argv) > 2:
        session(sys.argv[2])
    elif command == 'verify-ids':
        sys.exit(verify_ids())
    else:
        print(__doc__)
        sys.exit(2)
