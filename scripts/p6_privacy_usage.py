"""P6 (decision pending): read-only report of how the privacy notices are used today, per institution.

It changes NOTHING. It answers what the team needs to decide between (a) global notices (current schema) and
(b) notices per institution with `institution_id NULL = global` (see docs/MS_DIAGNOSTICO_Y_PLAN.md §19.4):

- every privacy_notice_version (version, active, effective date) and, for each one, how many sessions of each
  institution were agreed with it (session_processing_agreement), who agreed them (count) and the first / last date;
- notices used by more than one institution (they would have to stay global, or be duplicated, under option b);
- notices never used (they could be assigned to an institution without touching any historical agreement).

    python scripts/p6_privacy_usage.py            # isolated test stack (.env.test) by default
    python scripts/p6_privacy_usage.py --json     # machine-readable
"""
import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402

USAGE_SQL = """
SELECT n.id, n.version, n.active, n.effective_at, i.id AS institution_id, i.name AS institution,
       count(a.id) AS sessions, count(DISTINCT a.agreed_by_user_id) AS agreed_by, min(a.agreed_at) AS first_at,
       max(a.agreed_at) AS last_at
FROM privacy_notice_version n
LEFT JOIN session_processing_agreement a ON a.privacy_notice_version_id = n.id
LEFT JOIN work_session w ON w.id = a.session_id
LEFT JOIN operation o ON o.id = w.operation_id
LEFT JOIN institution i ON i.id = o.institution_id
GROUP BY n.id, n.version, n.active, n.effective_at, i.id, i.name
ORDER BY n.effective_at, n.version, i.name
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--database-url', default=None)
    args = parser.parse_args()
    engine = create_engine(args.database_url or load_test_env()['DATABASE_URL'], future=True)
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        rows = connection.execute(text(USAGE_SQL)).mappings().all()
    notices = {}
    for r in rows:
        item = notices.setdefault(str(r['id']), {'version': r['version'], 'active': r['active'],
                                                 'effective_at': r['effective_at'].isoformat(), 'institutions': []})
        if r['institution_id']:
            item['institutions'].append({'institution': r['institution'], 'sessions': r['sessions'],
                                         'agreed_by_users': r['agreed_by'],
                                         'first_at': r['first_at'].isoformat() if r['first_at'] else None,
                                         'last_at': r['last_at'].isoformat() if r['last_at'] else None})
    summary = {'notices': len(notices),
               'shared_by_several_institutions': sorted(v['version'] for v in notices.values() if len(v['institutions']) > 1),
               'never_used': sorted(v['version'] for v in notices.values() if not v['institutions'])}
    if args.json:
        print(json.dumps({'summary': summary, 'notices': notices}, indent=2, ensure_ascii=False))
        return
    for notice_id, item in notices.items():
        print(f"{item['version']:<16} active={item['active']!s:<5} effective={item['effective_at'][:10]}  id={notice_id}")
        for inst in item['institutions']:
            print(f"    {inst['institution']:<40} sessions={inst['sessions']:<5} users={inst['agreed_by_users']:<3} "
                  f"{(inst['first_at'] or '')[:10]} .. {(inst['last_at'] or '')[:10]}")
    print(f"\n{summary['notices']} notices; used by several institutions: {summary['shared_by_several_institutions'] or 'none'}; "
          f"never used: {summary['never_used'] or 'none'}")


if __name__ == '__main__':
    main()
