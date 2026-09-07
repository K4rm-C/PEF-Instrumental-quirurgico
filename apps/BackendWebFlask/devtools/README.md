# Frontend Preview Harness (Development Only)

This folder contains a **development-only** tool for visually reviewing the frontend
templates in `templates/` and `static/` in an actual browser. It is **not** part of the
production application and is not imported by, or wired into, `app.py`.

## What this is

A small standalone Flask app (`frontend_preview.py`) that renders the complete Operator CDE
presentation layer — Sign In, Operator Dashboard, Counting Sessions, New Counting Session,
Session History, Active Counting Session, Count Validation (both states), and Closed
Session Details, plus the Close Session / Supervisor Review Required modals — using the
real templates and CSS already built under `apps/BackendWebFlask/templates/` and
`apps/BackendWebFlask/static/`. It exists purely to let the frontend be checked visually
while the real backend (`app.py`, database, authentication) does not exist yet.

## What this is NOT

- It is **not** the production Flask application. It does not import `app.py`,
  `extensions.py`, `controllers/`, `clients/`, or `models/`.
- It does **not** use a real database. It does not initialize SQLAlchemy, and does not
  require PostgreSQL, Redis, or MongoDB to be running.
- It does **not** perform real authentication. The Sign In form's submit button simply
  redirects to the dashboard preview — no credentials are checked anywhere.
- It does **not** configure or require Flask-Babel. All `{{ _("...") }}` calls in the real
  templates — including gettext-style parameterized ones like
  `{{ _("Showing %(shown)s of %(total)s sessions", shown=3, total=8) }}` — are temporarily
  satisfied by mapping the Jinja global `_` to a function that returns the English text,
  interpolating any `%(name)s` placeholders the same way real gettext would:
  ```python
  def _preview_gettext(text, **kwargs):
      return text % kwargs if kwargs else text

  app.jinja_env.globals["_"] = _preview_gettext
  ```
  This mapping exists only inside `frontend_preview.py`. The real templates still use
  `{{ _("...") }}` exactly as written for the eventual Flask-Babel integration — nothing in
  `templates/` was changed to make this preview work.
- It must never be started as part of, or instead of, the production Flask configuration.

## How to run it

From `apps/BackendWebFlask/`, using the project's existing virtual environment workflow:

```bash
# create/activate a virtual environment if you don't already have one (see setVirtualEnv.sh)
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Flask is the only dependency this preview needs (already listed in requirements.txt)
pip install flask

# run the preview server
python devtools/frontend_preview.py
```

The server starts on port **5001** (not the production port) with `debug=True`, and prints:

```
Frontend preview only - not production application
```

## Available pages

| URL | Renders |
|---|---|
| `http://127.0.0.1:5001/` | Redirects to `/preview/sign-in` |
| `http://127.0.0.1:5001/preview/sign-in` | `templates/auth/sign_in.html` |
| `http://127.0.0.1:5001/preview/operator/dashboard` | `templates/operator/dashboard.html`, with local demo data matching the approved Figma reference |
| `http://127.0.0.1:5001/preview/operator/profile` | `templates/shared/profile.html` with Operator CDE context |
| `http://127.0.0.1:5001/preview/operator/sessions` | `templates/operator/sessions/list.html` (Counting Sessions) |
| `http://127.0.0.1:5001/preview/operator/sessions/new` | `templates/operator/sessions/new.html` (New Counting Session) |
| `http://127.0.0.1:5001/preview/operator/session-history` | `templates/operator/sessions/history.html` (Session History) |
| `http://127.0.0.1:5001/preview/operator/sessions/active` | `templates/operator/sessions/active.html` (Active Counting Session) |
| `http://127.0.0.1:5001/preview/operator/sessions/validation` | `templates/operator/sessions/validation.html`, No Discrepancy state |
| `http://127.0.0.1:5001/preview/operator/sessions/validation-discrepancy` | `templates/operator/sessions/validation.html`, Discrepancy state |
| `http://127.0.0.1:5001/preview/operator/sessions/closed` | `templates/operator/sessions/closed_details.html` (Closed Session Details) |

Submitting the Sign In form (`POST /preview/sign-in`) redirects straight to the dashboard
preview — this is a preview-only convenience, not authentication. Submitting New Counting
Session's form (`POST /preview/operator/sessions/new`) likewise redirects to the Active
Counting Session preview, and does not create any real session.

### Full click-through demo flow

Because every action button/link across these pages resolves to a real preview route (via
context-provided URLs, never hard-coded into the shared templates), the entire approved
Operator workflow can be clicked through end to end from Sign In:

```
Sign In → Dashboard → New Counting Session → (Start Session) → Active Counting Session
  → (Validate Count) → Count Validation [Discrepancy state, WS-026]
    → Confirm Validation → Supervisor Review Required modal
      → Submit for Review → Counting Sessions (WS-026 now "Pending Review", green banner)

Count Validation [No Discrepancy state, WS-024, reached directly via its preview URL]
  → Confirm Validation → Close Counting Session modal
    → Close Session → Closed Session Details
```

`?feedback=progress_saved` (Save Progress), `?feedback=review_saved` (Save Review), and
`?feedback=review_submitted` (Submit for Review) each reload the relevant page with the
matching one-off banner from the approved Figma wording ("Progress saved successfully.",
"Review saved successfully.", "Review submitted successfully.").


### Shared My Profile previews

The same `templates/shared/profile.html` template is reused for every authenticated role:

- `http://127.0.0.1:5001/preview/operator/profile`
- `http://127.0.0.1:5001/preview/supervisor/profile`
- `http://127.0.0.1:5001/preview/admin/profile`

Each route passes its role-specific `current_user`, `profile_role`, and `nav_urls` context.
The page is read-only and the My Profile sidebar item becomes active on that view.

## What you can test here

- **Theme switching** (Light / Dark / System) — fully functional, backed by the real
  `static/js/theme.js` and `localStorage`, exactly as it will behave in production.
- **Language selector dropdown** — opens and shows English / Español (México); selecting an
  option does not translate the page, since Flask-Babel integration has not been built yet.
  This preview does not fake or hard-code any client-side translation.
- **Operator sidebar navigation** — clickable between all four implemented pages in this
  preview (see "Sidebar navigation" below), with the correct item highlighted as active on
  each page. Breadcrumb, stat cards, charts, and tables all render from the real templates
  and demo data.
- **New Counting Session's Operation / Procedure select** — changing it updates the
  read-only Patient, Physician / Surgeon, and Operating Room fields via
  `static/js/pages/new-counting-session.js`, reading `data-*` attributes already rendered
  on each `<option>` (no data is duplicated into JavaScript).
- **New Counting Session's sticky action bar** — Cancel / Start Session stays pinned to the
  bottom of the main content area (never under the sidebar) regardless of viewport height or
  scroll position. The same sticky pattern is reused by Active Counting Session and Count
  Validation.
- **Active Counting Session's Instrument Count +/-** — click the stepper buttons to see the
  counted quantity, Difference, and row highlight update live via
  `static/js/pages/counting-session.js`. This is preview-only: nothing is saved.
- **Count Validation** — visit `/sessions/validation` and `/sessions/validation-discrepancy`
  to compare the No Discrepancy and Discrepancy states side by side; both are rendered from
  the same `validation.html` template via `has_discrepancy`.
- **Close Session / Supervisor Review Required modals** — "Confirm Validation" opens the
  correct modal for the page's state, using the existing generic `static/js/modal.js`
  (no modal-specific JavaScript). Escape, the backdrop, and each modal's secondary button all
  close it.

## Sidebar navigation

`partials/sidebar_operator.html` accepts an optional `nav_urls` dict; each link falls back
to `"#"` independently when a key is missing, so passing nothing (today's production
behavior) is unaffected. This preview passes real preview routes via a small
`build_nav_urls()` helper in `frontend_preview.py`, called from each route, so the sidebar
can be clicked through between Dashboard, Counting Sessions, New Session, and Session
History during review. This is documented here rather than hard-coded into the shared
template, so the production app can later pass real `url_for(...)` results through the same
`nav_urls` mechanism without any template changes.

## Demo data

`frontend_preview.py` passes plain Python dicts/lists as Jinja context — the same shapes
documented in `../FRONTEND_INTEGRATION.md` (`current_user`, `dashboard_stats`,
`sessions_by_day`, `session_status_breakdown`, `recent_sessions`, `sessions`,
`session_history`, `operations`, `available_kits`, `available_capture_stations`,
`expected_inventory`, `instrument_readiness`, `session`, `instrument_counts`,
`session_summary`, `current_alerts`, `has_discrepancy`, `pre_post_comparison`,
`count_comparison`, `discrepancy_reasons`, `discrepancy_summary`, `pre_procedure_status`,
`post_procedure_status`, `closure_summary`, `validated_count`, `activity_log`). No backend
models are imported and no database is queried.

## Endpoint/URL notes

The only `url_for()` calls made *from Jinja* across every page (and their shared
partials/macros/modals) are `url_for('static', filename=...)`, which Flask resolves
automatically from this app's `static_folder`. The sidebar's `nav_urls`, and every
preview-to-preview action link (Start Session, Validate Count, both modals' primary
actions, Save Progress/Review, Submit for Review, Back to Sessions, etc.), are resolved in
Python and passed to templates as plain context strings, so no additional dummy endpoints
were needed beyond the ten routes in `frontend_preview.py`.
