# V3 Implementation Notes (Supervisor + Operator)

Maintenance note for the V3 UI (`ui_reference_v3/`). Administrator and Shared/Public/Auth are
unchanged from V2.

## A. V3 demo / non-UUID architecture

- **Fixtures:** `controllers/demo_data.py`. Plain data plus pure projection helpers; no
  database access and no Flask request objects.
- **Accessors:** the V3 section at the end of `controllers/view_data.py`. Routes call only
  these (`operator_v3_*`, `supervisor_v3_*`, `v3_*`). Templates never re-declare fixture
  values.
- **Gate:** `use_v3_fixture(session_id=None)`.
  - List screens use fixtures only when `FRONTEND_DEMO_MODE=true`.
  - Detail screens always use fixtures for explicit demo ids (`WS-021`…`WS-028`), and for
    other non-UUID ids only in demo mode.
  - A UUID never uses fixtures.
- **Templates:**
  - Supervisor tab shells use `?tab=` with `_details_*.html` and `_review_*.html` partials.
  - Operator pages share `operator/sessions/_context_bar.html`.
  - Shared component macro: `macros.evidence_figure`.
- **CSS:**
  - `components/tabs.css` and `components/evidence.css` are global and additive.
  - `pages/v3-pages.css` holds cross-role V3 page primitives plus the Supervisor-only rules.
    It was renamed from `supervisor-v3.css`; the rules are unchanged.
  - `pages/operator-v3.css` holds the Operator-only rules.
- **Header identity:** demo pages show a display copy (Sophia Turner / Alex Morgan) via
  `v3_supervisor_display_user` / `v3_operator_display_user`. The authenticated user, the auth
  service and the seeds are never modified, so Profile shows the real account.

## B. RF / UUID architecture

- UUID sessions keep the RF path: `services/rf_session.py` and the RF POST handlers in
  `routes.py`.
- **Supervisor RF templates:** `supervisor/sessions/rf_details.html`, `rf_review.html`,
  `schedule.html` (RF scheduling, including privacy confirmation).
- **Operator RF templates:**
  - `operator/sessions/rf_begin.html`: a verbatim copy of the pre-V3 `begin.html`.
  - `details.html` and `manual_*.html`.
  - `operator/sessions/v2/*.html`: verbatim copies of the V2 click-through pages. UUIDs reach
    them through the RF vision-start redirect and the old stub chain.
- **RF lists:** these render RF rows inside the V3 list layout through the
  `*_rows_from_rf` / `*_from_rf` adapters.
  - The Operator list keeps the pre-V3 inclusion: every RF row, with RF action and details
    URLs unchanged.

## C. Why both coexist

The approved V3 workflow (AI evidence, review cases, Operator-closed sessions) has no backing
models or persistence yet. The RF path is the working database implementation, covered by
`scripts/smoke_rf_*.py`. V3 is therefore fixture-driven and isolated from RF until the backend
supports it.

## D. Lifecycle mismatch (intentional, unresolved)

| | V3 demo | RF backend |
|---|---|---|
| Who closes the session | Operator, right after validation or escalation (`POST /operator/sessions/<id>/close`, demo only, no DB write) | Supervisor, via `confirm_spd_close` |
| Status model | Session status (Closed) is separate from review status (Review Required / Reviewed – Unresolved / Resolved) | `in_progress → correction_required / awaiting_spd_review → closed` |

Do not reconcile these in UI code. It needs a backend lifecycle change.

## E. Demo statelessness

- Nothing is persisted: no database, no cookie, no server session.
- Demo state travels in query parameters:
  - Operator: `scenario=clean`; the default is escalated.
  - Tray verification: `state=`, `attempt=`.
  - Escalation: `state=prepared|escalated`.
  - Supervisor: `tab=`, `case=`, `resolved=`, `unresolved=`, `outcome=resolved|unresolved`.
  - Banners: `created=WS-027`, `validated=WS-026`, `closed=WS-026&outcome=`.
- `?closed=WS-026` hides WS-026 from that one Assigned Sessions response. A plain reload
  shows the static fixture again.

## F. Evidence asset

- Expected path: `static/img/demo/ws-026-final-tray.{jpg|jpeg|png|webp}`. It is not
  committed.
- `view_data.v3_evidence()` detects the file and gives both roles the same URL.
- Without it, `evidence_figure` renders a styled placeholder with no overlay boxes.
- The overlay coordinates in `demo_data.WS026_EVIDENCE` must be re-measured once the real
  image is added.

## G. Demo sessions

- **WS-026** is the canonical cross-role case: Appendectomy, OR-3, Maria García / PT-10482,
  Delivery Kit, CDE-01, Alex Morgan → Sophia Turner.
  - Counts: 11 / 10 / 10 / −1, with two review cases (Mayo-Hegar: Missing Instrument;
    Suture Needle Set: Evidence Verification).
  - The timeline runs 15:15 → 15:45.
  - Supervisor uses the escalated projection only. Operator also supports `scenario=clean`
    (11 / 11 / 11 / 0).
- **WS-027** is created by the Supervisor wizard (2026-10-03 08:30) and listed by the
  Operator as Ready to Start. Its walkthrough is summary-only.
- **WS-021–025 and WS-028** are supporting list rows with summary-only detail pages.

## H. Legacy files intentionally retained

| File(s) | Why kept |
|---|---|
| `operator/dashboard.html`, `operator/sessions/new.html` (+ `js/pages/new-counting-session.js`) | Still rendered by `/legacy/operator/dashboard` and `/legacy/operator/sessions/new` (old URLs redirect there) |
| `operator/sessions/history.html` | `/operator/sessions/history` route (backward-compatible URL; not in V3 navigation) |
| `operator/sessions/awaiting_review.html`, `correction_requested.html`, `closed_details.html` | Still rendered by `/awaiting-review`, `/correction`, `/closed/<id>` routes; `v2/ready_to_close.html` links to `closed_details` |
| `operator/sessions/v2/*.html` (+ `js/pages/counting-session.js`, `css/pages/active-counting-session.css`, `components/modals/close_session.html`) | Rendered for UUID / non-demo ids |
| `operator/sessions/active.html` | No app route; still used by the documented `devtools/frontend_preview.py` |
| `devtools/frontend_preview.py`, `devtools/README.md` | Documented dev tool. **Stale:** 18/43 preview routes fail because current templates use `web.*` endpoints, and its port 5001 clashes with the auth service. Replace or retire it in a future task |
| `supervisor/sessions/schedule.html`, `rf_details.html`, `rf_review.html` | RF path |
| `css/components/counter.css` | Linked globally from `base.html`; removing it would change every page's HTML |

Removed in the V3 cleanup (no includes or routes anywhere): `components/modals/request_correction.html`,
`reject_review.html` and `supervisor_review_required.html`. Older docs
(`FRONTEND_INTEGRATION.md`, `V2_IMPLEMENTATION_NOTES.md`) still describe them as history.
