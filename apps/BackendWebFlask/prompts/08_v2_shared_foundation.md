You are implementing the second visual version of the Flask/Jinja web frontend for the Surgical Instrument Traceability System.

IMPORTANT: This is an evolution of the existing frontend, NOT a frontend rewrite.

Before modifying code, inspect:

- apps/BackendWebFlask/ui_reference/
- apps/BackendWebFlask/ui_reference_v2/
- apps/BackendWebFlask/ui_reference_v2/SCREEN_MANIFEST_V2.md
- apps/BackendWebFlask/templates/
- apps/BackendWebFlask/static/css/
- apps/BackendWebFlask/static/js/
- apps/BackendWebFlask/controllers/
- apps/BackendWebFlask/models/
- apps/BackendWebFlask/FRONTEND_INTEGRATION.md if present
- existing frontend prompts 01 through 07

The PNG images inside ui_reference_v2 are the approved V2 visual source of truth.

Do not render those PNG files directly in the application.
Recreate the interface using HTML, Jinja, CSS, existing components and application data.

--------------------------------------------------
PHASE 1 — AUDIT BEFORE CODING
--------------------------------------------------

First inspect the existing implementation and identify:

1. reusable layouts
2. reusable partials
3. reusable components
4. reusable CSS
5. existing routes
6. existing view-data helpers
7. screens that already exist
8. screens that are new in V2
9. places where V1 structure can be extended instead of replaced

Preserve the existing Flask MVC architecture.

Do not create React, Vue, Tailwind, Bootstrap, or another frontend framework.

Do not create a second frontend implementation alongside the current one.

--------------------------------------------------
PHASE 2 — V2 SHARED VISUAL FOUNDATION
--------------------------------------------------

Update the existing shared visual system only where necessary to match V2.

Review and reuse:

templates/base.html
templates/layouts/
templates/partials/
templates/components/
static/css/tokens.css
static/css/base.css
static/css/layout.css
static/css/components/

Preserve component reuse.

Shared visual elements should support the V2 references for all three roles.

This includes:

- application header
- breadcrumbs
- sidebar structure
- cards
- KPI cards
- tables
- forms
- filters
- badges
- alerts
- modal styling
- pagination
- language selector
- light/dark selector
- user profile area

Do not implement role-specific V2 screens yet except where required to validate shared components.

--------------------------------------------------
PHASE 3 — PUBLIC LANDING PAGE
--------------------------------------------------

Use:

ui_reference_v2/shared/public-landing-page.png

as the visual source of truth.

Create a real Jinja template for the public landing page.

Recommended location:

templates/shared/landing.html

Update the root route:

/

The root route must render the Public Landing Page instead of redirecting directly to Sign In.

The Landing Page Sign In actions should link to the existing sign-in route.

Do not add fake backend functionality.

Preserve the information architecture and sections shown in the approved V2 reference.

--------------------------------------------------
PHASE 4 — SIGN IN
--------------------------------------------------

Use:

ui_reference_v2/shared/sign-in.png

as the visual source of truth.

Update the existing:

templates/auth/sign_in.html

Do not replace the existing authentication workflow.

Keep:

Institutional Email
Password
Sign In
EN / ES
Light / Dark

Do not add password recovery.

The Sign In form must continue to use the existing backend authentication route and behavior.

--------------------------------------------------
PHASE 5 — ROLE LAYOUT PREPARATION
--------------------------------------------------

Update shared layouts and sidebars only as needed so the future V2 Operator, Supervisor and Administrator screens can be implemented without duplicating styles.

Do NOT yet migrate all Operator, Supervisor or Administrator pages.

Do not remove existing routes or templates that will be migrated in later prompts.

--------------------------------------------------
PHASE 6 — VALIDATION
--------------------------------------------------

After implementation:

1. run the Flask application or available frontend preview harness
2. verify `/`
3. verify `/sign-in`
4. verify authenticated layouts still render
5. verify no existing backend functionality was broken
6. check browser console for JavaScript errors
7. check for missing templates
8. check for missing CSS/static files

If automated tests exist, run relevant tests.

--------------------------------------------------
DOCUMENTATION
--------------------------------------------------

Create or update a V2 implementation note documenting:

- files modified
- files created
- shared components reused
- known pending work
- role-specific migration intentionally deferred

Do not claim Operator, Supervisor or Administrator V2 are complete in this prompt.

--------------------------------------------------
CONSTRAINTS
--------------------------------------------------

- Keep Flask + Jinja.
- Preserve existing working backend logic.
- Preserve current database integration.
- Do not hardcode logic that already exists in models/controllers.
- Do not invent database fields.
- Do not replace server-side behavior with frontend simulation.
- Do not delete ui_reference V1.
- Do not delete ui_reference_v2.
- Do not modify approved reference images.
- All visible UI text must remain in English.
- Prioritize visual fidelity to ui_reference_v2 while preserving functional behavior.