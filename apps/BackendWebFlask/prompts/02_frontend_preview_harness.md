# Frontend Preview Harness

Create a DEVELOPMENT-ONLY preview harness so the currently implemented frontend templates can be rendered and visually tested in a browser without modifying the real Flask backend.

## Scope

You may create development-only files under:

apps/BackendWebFlask/devtools/

You may inspect:

apps/BackendWebFlask/templates/
apps/BackendWebFlask/static/
apps/BackendWebFlask/FRONTEND_INTEGRATION.md
apps/BackendWebFlask/ui_reference/

Do NOT modify:

- app.py
- extensions.py
- controllers/
- clients/
- models/
- database code
- authentication code
- production Flask routes

Do NOT redesign or modify the existing templates unless an actual rendering bug makes it impossible to preview them. If you find such a problem, report it before changing the production frontend files.

## Goal

Create a small standalone Flask development application that renders:

1. Shared Sign In
2. Operator Dashboard

using the templates and static assets already implemented.

This preview application exists only to visually validate the frontend while the real backend is not yet available.

## Suggested location

Create:

apps/BackendWebFlask/devtools/frontend_preview.py

Do not use or modify the production app.py.

## Flask setup

The preview application should:

- use the existing templates/ directory
- use the existing static/ directory
- run independently from the production Flask application
- not initialize SQLAlchemy
- not require PostgreSQL
- not require Redis
- not require MongoDB
- not require authentication
- not require Flask-Babel to be installed/configured

## Flask-Babel compatibility

The templates already use translation calls such as:

{{ _("Dashboard") }}

For preview purposes only, register a temporary Jinja global `_` that simply returns the English source string unchanged.

Example concept:

app.jinja_env.globals["_"] = lambda text: text

This is ONLY for the preview harness.

Do not remove or replace `_()` calls from the real templates.

## Demo routes

Provide at least:

/preview/sign-in

→ renders:
templates/auth/sign_in.html

/preview/operator/dashboard

→ renders:
templates/operator/dashboard.html

Optionally:

/

→ redirects to /preview/sign-in

## Demo context

Inspect FRONTEND_INTEGRATION.md and the actual templates.

Provide realistic local demo context for all Jinja variables required to render the Operator Dashboard.

Use the same example content shown in the approved Figma Operator Dashboard whenever possible.

Expected concepts include:

current_locale

current_user:
- name
- role_label
- avatar_url

dashboard_stats

sessions_by_day

session_status_breakdown

recent_sessions

active_nav_item

Use plain Python dictionaries / lists or SimpleNamespace objects.

Do not use database models.

Do not import backend models.

Do not connect to PostgreSQL.

## URL compatibility

Inspect all url_for() calls used by the two currently implemented templates and their included partials.

The preview harness must provide harmless development-only routes/endpoints as needed so Jinja does not raise BuildError during rendering.

Do not modify production route assumptions merely to make the preview work.

Dummy preview routes may return to an existing preview page or return an empty development response.

Clearly comment that they are preview-only endpoint stubs.

## Sign In

The Sign In form must render visually.

Submitting the form does not need to authenticate a user.

If needed for preview purposes:

POST /preview/sign-in

may redirect directly to:

/preview/operator/dashboard

This behavior must be clearly identified as preview-only and must not be confused with real authentication.

## Static assets

Verify that the existing CSS and JavaScript are loaded through the preview Flask application.

Theme JavaScript must work.

Dropdown JavaScript already implemented for visual controls should work.

## Theme test

The preview must allow testing:

- Light
- Dark
- System

using the existing frontend implementation.

localStorage persistence should continue to work in the preview.

## Language selector

The Language selector should open and display:

- English
- Español (México)

Selecting a language does not need to translate the page yet because Flask-Babel backend integration has not been implemented.

Do not implement fake client-side translations.

## Development server

Run on a development-only port such as:

5001

Use:

debug=True

only in this preview tool.

Never introduce this configuration into production files.

## Console output

When the preview server starts, print clearly:

Frontend preview only — not production application

and show URLs such as:

http://127.0.0.1:5001/preview/sign-in
http://127.0.0.1:5001/preview/operator/dashboard

## Documentation

Create:

apps/BackendWebFlask/devtools/README.md

Explain:

- this preview tool is development-only
- how to start it
- which pages are currently available
- that it does not use the real backend/database/authentication
- that `_()` is temporarily mapped to the original English text only for preview rendering
- that this tool must not replace production Flask configuration

## Final validation

Before finishing:

1. Verify the Python preview file has valid syntax.
2. Verify all required Jinja templates can be located.
3. Verify all referenced static CSS/JS paths resolve conceptually.
4. Verify Sign In has enough context to render.
5. Verify Operator Dashboard has enough context to render.
6. Verify no backend-owned file was changed.

## Completion report

When finished, report:

- files created
- preview routes available
- exact command to run the preview
- any Jinja rendering issues discovered
- any missing variables or endpoint assumptions
- whether any production frontend file had to be changed

Do not continue to other application screens.