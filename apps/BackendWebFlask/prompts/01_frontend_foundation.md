Your repository analysis and proposed frontend architecture are approved with the adjustments below.

You may now begin implementing the first frontend foundation.

IMPORTANT SCOPE RULE

Continue to work ONLY inside:

apps/BackendWebFlask/templates/
apps/BackendWebFlask/static/

You may also remove the existing empty placeholder file:

apps/BackendWebFlask/templates/html

because it must become a real template directory structure.

Do NOT modify:

- app.py
- extensions.py
- controllers/
- clients/
- models/
- database code
- authentication code
- Flask routes
- backend dependencies

Do not modify the other empty placeholder files owned by the backend team.

==================================================
ADJUSTMENTS TO YOUR PROPOSED STRUCTURE
==================================================

Do NOT create these yet:

- shared/profile.html
- notifications_dropdown.html
- errors/404.html
- errors/500.html

These interfaces have not been approved in Figma yet.

The notification bell and My Profile sidebar item may exist visually, but do not invent missing screens or dropdown behavior.

Add:

static/css/pages/

for page-specific styles that cannot reasonably belong to reusable components.

Keep page-specific CSS small.

Most visual styling should still come from reusable tokens, layout, and component styles.

==================================================
APPROVED INITIAL STRUCTURE
==================================================

Create the necessary foundation under:

templates/
├── base.html
├── layouts/
│   ├── authenticated.html
│   └── guest.html
├── partials/
│   ├── header.html
│   ├── sidebar_operator.html
│   ├── sidebar_supervisor.html
│   ├── sidebar_admin.html
│   ├── language_selector.html
│   ├── theme_selector.html
│   ├── user_menu.html
│   └── flash_messages.html
├── components/
│   ├── macros.html
│   └── modals/
├── auth/
│   └── sign_in.html
└── operator/
    └── dashboard.html

Create the necessary foundation under:

static/
├── css/
│   ├── tokens.css
│   ├── base.css
│   ├── layout.css
│   ├── components/
│   └── pages/
├── js/
│   ├── theme.js
│   ├── modal.js
│   └── dropdown.js
├── img/
└── icons/

Do not create all application pages yet.

==================================================
VISUAL REFERENCES FOR THIS ITERATION
==================================================

For this first implementation, inspect and use ONLY the approved Figma reference images corresponding to:

1. Shared Sign In
2. Operator Dashboard

They are located under:

apps/BackendWebFlask/ui_reference/

Find the corresponding PNG files yourself based on their directory and descriptive filename.

These PNG files are the visual source of truth.

Study them carefully before implementing.

Do not redesign them.

==================================================
FIRST ITERATION GOAL
==================================================

Implement only:

1. Global frontend design tokens
2. Base HTML/Jinja structure
3. Guest layout
4. Authenticated application shell
5. Shared top header
6. Operator sidebar
7. Sign In page
8. Operator Dashboard
9. Light / Dark / System theme support
10. Reusable visual components required by these two pages

Do NOT implement:

- Operator Counting Sessions
- New Counting Session
- Active Counting Session
- Supervisor pages
- Administrator pages
- business workflow logic

Those will be implemented in later iterations.

==================================================
HTML AND JINJA
==================================================

Use semantic HTML5.

All source identifiers must be English and descriptive.

Examples:

class="dashboard-stat-card"
id="sign-in-form"
id="theme-selector"

Use Jinja inheritance and includes instead of duplicating page shells.

For example:

sign_in.html
→ extends guest.html

operator/dashboard.html
→ extends authenticated.html
→ uses sidebar_operator.html

Prepare visible static interface strings for Flask-Babel.

For example:

{{ _("Dashboard") }}

{{ _("Institutional Email") }}

{{ _("Sign In") }}

{{ _("Sessions Today") }}

Do not configure Flask-Babel itself.

Do not create a JavaScript translation dictionary.

==================================================
IMPORTANT JAVASCRIPT INTERNATIONALIZATION RULE
==================================================

Do not hard-code user-visible English messages inside JavaScript when those messages will eventually need translation.

If JavaScript requires visible text, prefer receiving the translated value from the Jinja template using:

- data-* attributes
- hidden template values
- another clean server-rendered mechanism

JavaScript source code itself must remain in English.

==================================================
THEME IMPLEMENTATION
==================================================

Implement:

Light
Dark
System

using:

- CSS custom properties
- a data-theme attribute
- JavaScript
- localStorage
- prefers-color-scheme for System mode

Theme changes must not require backend support.

Avoid a flash of the wrong theme during page load as much as reasonably possible.

Use semantic design tokens rather than hard-coded component colors.

Examples:

--color-background
--color-surface
--color-text-primary
--color-text-secondary
--color-border
--color-primary
--color-success
--color-warning
--color-danger
--color-info

==================================================
DARK MODE
==================================================

The Figma references primarily represent Light mode.

Create a restrained accessible Dark mode derived from the same semantic design system.

Do not redesign the application for Dark mode.

Keep:

- hierarchy
- spacing
- component structure
- semantic status meaning

Ensure adequate text/background contrast.

==================================================
LANGUAGE SELECTOR
==================================================

Build the visual language selector shown in Figma.

It should represent:

English
Español (México)

However:

Do NOT implement locale persistence or Flask-Babel configuration.

Do NOT invent backend language routes.

The selector may remain frontend-ready for backend integration.

Document what the backend team will eventually need to connect.

==================================================
SIGN IN
==================================================

Implement the Sign In page according to the approved Figma reference.

Include the existing approved fields:

Institutional Email
Password

and:

Sign In

Keep the language and theme controls shown in the Figma design.

Do NOT add:

- Forgot Password
- Reset Password
- Create Account
- Social Login
- Role selector

The form action and authentication behavior are backend responsibilities.

Use a safe placeholder form action or leave integration clearly documented rather than inventing a Flask endpoint.

==================================================
AUTHENTICATED APPLICATION SHELL
==================================================

The Operator Dashboard must use this structure:

[ SIDEBAR ][ MAIN APPLICATION AREA ]

MAIN APPLICATION AREA:

[ TOP HEADER ]
[ PAGE CONTENT ]

The sidebar must remain full height.

The main content area must be capable of vertical scrolling independently when page content grows.

Use the approved Figma proportions and spacing as closely as possible.

==================================================
OPERATOR SIDEBAR
==================================================

Implement only the approved Operator sidebar:

HOME
- Dashboard

OPERATIONS
- Counting Sessions
- New Session
- Session History

ACCOUNT
- My Profile
- Sign Out

Use the approved four-square grid Dashboard icon.

Do not use a house icon.

Navigation URLs are not yet defined by the backend.

Do not invent final Flask routes.

Use safe placeholders and clearly identify them as integration points.

==================================================
TOP HEADER
==================================================

Implement the approved top header with:

- breadcrumb
- notification icon
- language selector
- theme selector
- user avatar
- user name
- user role
- user menu affordance

Do not invent a notifications dropdown.

Do not invent a My Profile page.

Use descriptive Jinja values such as:

current_user.name
current_user.role_label

where appropriate.

If temporary fallback content is necessary for visual development, clearly isolate it and make the intended backend context obvious.

==================================================
OPERATOR DASHBOARD
==================================================

Reproduce the approved Figma Operator Dashboard closely.

Include its existing:

- page title
- subtitle
- KPI cards
- chart/card areas
- Recent Counting Sessions section
- New Counting Session primary action
- statuses and badges

Do not invent new metrics or functionality.

Use Jinja-friendly structures for data that will later come from the backend.

For repeated content, prefer loops such as:

{% for session in recent_sessions %}

instead of duplicating static HTML rows when practical.

However, because no backend is currently available, the template must remain understandable and visually implementable without requiring backend modifications.

==================================================
CHARTS
==================================================

Do not introduce a chart framework in this first iteration unless absolutely necessary.

For dashboard visual placeholders, prefer lightweight semantic HTML/CSS/SVG that reproduces the Figma visual structure.

Do not implement business analytics logic.

The backend team will eventually provide real chart data.

==================================================
ACCESSIBILITY
==================================================

Use:

- semantic landmarks
- labels associated with form controls
- buttons for actions
- accessible names for icon-only controls
- visible keyboard focus states
- appropriate aria-expanded where dropdown controls require it
- sufficient contrast
- touch-friendly target sizes

Status information must use text in addition to color.

==================================================
RESPONSIVENESS
==================================================

Match the desktop Figma design first.

Also make the shell reasonably usable at tablet widths.

Do not create a separate mobile application design.

Avoid layouts that require fixed desktop-only pixel widths when responsive CSS can preserve the visual design.

==================================================
NO BACKEND CHANGES
==================================================

If something requires backend functionality, do not implement or guess it.

Instead, document it clearly as an integration point.

Examples:

- login submission
- current authenticated user
- logout
- language change persistence
- dashboard data
- URLs
- flash messages

==================================================
DOCUMENTATION
==================================================

Create:

apps/BackendWebFlask/FRONTEND_INTEGRATION.md

This file should concisely document:

- templates created
- shared Jinja context expected
- backend integration points
- theme behavior
- planned Flask-Babel integration
- route placeholders that still require backend implementation

Do not modify backend documentation.

==================================================
WORKFLOW
==================================================

Before making changes:

1. Inspect the Sign In PNG.
2. Inspect the Operator Dashboard PNG.
3. Identify their shared visual components.
4. Implement the reusable foundation.
5. Implement Sign In.
6. Implement Operator Dashboard.
7. Review your own changes for unnecessary duplication.

After implementation, report:

- files created
- important design decisions
- Jinja variables expected from backend
- anything that could not be determined from Figma
- anything that requires backend integration

Do not continue to other application screens until I review this first iteration.