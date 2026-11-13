"""Isolated test stack only: give back the demo instruments that an E2E marked as LOST.

The MS-06 and MS-07/08 end-to-end scripts exercise the real "physical shortage" path (the Supervisor records the
missing instrument as lost). That is correct behaviour, but on the shared test database it consumes the small demo
stock of the kit families, and the next run is then (correctly) refused with stock_block. This helper restores, for
the given sessions only, every instrument whose LAST cycle event is `lost` recorded by one of those sessions: the
instrument gets back the cycle status it had right before (its previous cycle event) and becomes active again. The
cycle history is kept (a restoring event is appended with a note). It refuses to run against anything but the
isolated test PostgreSQL (127.0.0.1:55632).

    python scripts/e2e_restore_stock.py --all-e2e-lost      # one-off: every lost instrument of the test database
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_test_env import load_test_env  # noqa: E402

_LOST = """
SELECT i.id AS instrument_id, last.session_id,
       (SELECT prev.cycle_status_id FROM instrument_cycle_event prev
        WHERE prev.instrument_id = i.id AND prev.occurred_at < last.occurred_at
        ORDER BY prev.occurred_at DESC LIMIT 1) AS previous_status
FROM instrument i
JOIN cat_instrument_cycle_status s ON s.id = i.cycle_status_id AND s.code = 'lost'
JOIN LATERAL (SELECT e.session_id, e.occurred_at FROM instrument_cycle_event e WHERE e.instrument_id = i.id
              ORDER BY e.occurred_at DESC LIMIT 1) last ON TRUE
"""


def _guard(url: str) -> None:
    if '127.0.0.1:55632' not in url and 'localhost:55632' not in url:
        raise SystemExit('refusing: only the isolated test PostgreSQL (port 55632) may be restored')


def restore_lost(engine, session_ids=None) -> int:
    _guard(str(engine.url))
    restored = 0
    with engine.begin() as c:
        for row in c.execute(text(_LOST)).mappings().all():
            if session_ids is not None and str(row['session_id']) not in {str(s) for s in session_ids}:
                continue
            status = row['previous_status'] or c.execute(text(
                "SELECT id FROM cat_instrument_cycle_status WHERE code = 'available'")).scalar()
            if status is None:
                continue
            c.execute(text('UPDATE instrument SET cycle_status_id = :s, active = TRUE, updated_at = now() WHERE id = :i'),
                      {'s': status, 'i': row['instrument_id']})
            c.execute(text("INSERT INTO instrument_cycle_event (instrument_id, cycle_status_id, notes) "
                           "VALUES (:i, :s, 'E2E test data restored (isolated test stack)')"),
                      {'i': row['instrument_id'], 's': status})
            restored += 1
    return restored


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--all-e2e-lost', action='store_true', required=True)
    parser.parse_args()
    engine = create_engine(load_test_env()['DATABASE_URL'], future=True)
    print('restored', restore_lost(engine))


if __name__ == '__main__':
    main()
