# Operator Sessions — List, New Session and History

Implement the next approved Operator CDE frontend screens for the
“Surgical Instrument Traceability System”.

This iteration must build on the frontend foundation that is already implemented and visually approved.

IMPORTANT:

Do NOT redesign the application.

Reuse the existing:

- base layout
- authenticated layout
- Operator sidebar
- top header
- design tokens
- theme system
- buttons
- cards
- tables
- forms
- status badges
- dropdown patterns
- typography
- spacing conventions

The approved Figma PNG references located under:

apps/BackendWebFlask/ui_reference/

are the visual source of truth.

==================================================
1. SCOPE
==================================================

Implement only these Operator screens:

1. Counting Sessions
2. New Counting Session
3. Session History

Do NOT implement yet:

- Active Counting Session
- Count Validation
- Count Validation with Discrepancy
- Supervisor Review Required
- Close Session modal
- Closed Session Details
- Supervisor screens
- IT Administrator screens

Those belong to later implementation phases.

==================================================
2. ALLOWED FILES
==================================================

You may create or modify frontend files under:

apps/BackendWebFlask/templates/
apps/BackendWebFlask/static/

You may also extend the DEVELOPMENT-ONLY preview harness under:

apps/BackendWebFlask/devtools/

only to add preview routes and local demo context for the three screens created in this iteration.

Do NOT modify:

- app.py
- extensions.py
- controllers/
- clients/
- models/
- database logic
- authentication logic
- production Flask routes

The preview harness must remain completely independent from the production backend.

==================================================
3. CODE LANGUAGE
==================================================

All source code must remain in English.

This includes:

- filenames
- variables
- functions
- CSS classes
- IDs
- data attributes
- JavaScript identifiers
- comments when necessary

Use descriptive naming.

Examples:

countingSessions
selectedOperation
expectedInventory
captureStation
sessionStatus
instrumentReadiness

Do not use Spanish identifiers.

Visible interface text must remain prepared for Flask-Babel using:

{{ _("...") }}

Do not configure Flask-Babel.

==================================================
4. FIGMA REFERENCES
==================================================

Before writing code, inspect the approved Operator PNG references under:

apps/BackendWebFlask/ui_reference/

Find the current approved references corresponding to:

- Counting Sessions
- New Counting Session
- Session History

Study them carefully.

Match:

- layout
- spacing
- visual hierarchy
- tables
- filters
- cards
- status badges
- button placement
- field arrangement
- responsive behavior

Do not rely on memory when the Figma reference provides the visual answer.

Do not invent a different design.

==================================================
5. TEMPLATE STRUCTURE
==================================================

Use the existing Operator template organization.

Recommended resulting structure:

templates/
└── operator/
    └── sessions/
        ├── list.html
        ├── new.html
        └── history.html

If equivalent files already exist from the previous foundation work, reuse them rather than creating duplicate alternatives.

All three screens must extend the existing authenticated layout.

Use the existing Operator sidebar partial.

Do not duplicate the application shell.

==================================================
6. SHARED OPERATOR NAVIGATION
==================================================

Maintain the approved Operator sidebar:

HOME
- Dashboard

OPERATIONS
- Counting Sessions
- New Session
- Session History

ACCOUNT
- My Profile
- Sign Out

For each page, set the correct active navigation state:

Counting Sessions
→ Counting Sessions active

New Counting Session
→ New Session active

Session History
→ Session History active

Do not implement real navigation routes.

Use the same placeholder/integration pattern already established in the current frontend.

Do not invent Flask endpoint names.

==================================================
7. SHARED HEADER
==================================================

Reuse the current approved header.

Keep:

- breadcrumb
- notification icon
- language selector
- theme selector
- avatar
- current user name
- role
- user-menu affordance

Do not duplicate header markup inside individual pages.

Do not invent notification or user profile behavior.

==================================================
8. COUNTING SESSIONS PAGE
==================================================

Implement the approved:

Counting Sessions

screen.

Use the Figma reference as the exact visual guide.

Page purpose:

Show the Operator's current operational queue.

This page primarily contains sessions that still require operational attention.

Typical status concepts include:

- Active
- Pending Validation
- With Discrepancies
- Pending Review

Closed sessions belong primarily in Session History.

==================================================
9. COUNTING SESSIONS HEADER
==================================================

Include:

Page title:
Counting Sessions

Approved subtitle from the Figma reference.

Primary action if shown in Figma:

+ New Counting Session

Do not invent additional actions.

==================================================
10. COUNTING SESSIONS FILTERS
==================================================

Reproduce the approved search/filter area.

Expected concepts may include:

- Search Sessions
- Status
- Kit
- Operator or relevant filter if present in the approved Figma
- Date if present in the approved Figma

Use semantic:

<form>
<input>
<select>

controls where appropriate.

Filters do not need real backend functionality yet.

Do not implement fake database filtering.

If lightweight visual client-side filtering is already supported by shared frontend code, it may be reused, but do not expand scope unnecessarily.

==================================================
11. COUNTING SESSIONS TABLE
==================================================

Reproduce the approved table.

Use the actual columns shown in the Figma reference.

Expected concepts include:

Session / Procedure
Kit
Operator
Capture Station
Started
Status
Action

Use the Figma reference if column naming differs.

Do not invent columns.

Render repeated rows using Jinja.

Preferred:

{% for session in sessions %}

Do not duplicate static HTML rows unnecessarily.

==================================================
12. SESSION ROW DATA CONTRACT
==================================================

Use backend-friendly presentation data.

A session row should be able to consume concepts such as:

session_id
procedure_name
operating_room
kit_name
operator_name
capture_station_name
started_at
status_label
status_variant
action_label
action_url

Do not perform business-status inference in the template if the backend can provide presentation-ready values.

For example, avoid large conditional trees that decide whether the action should be:

Continue
Review
View Details

Prefer:

session.action_label
session.action_url

while preserving safe demo fallbacks for preview.

==================================================
13. STATUS BADGES
==================================================

Reuse the existing shared status-badge component/macro.

Maintain semantic status treatment:

Active
→ blue informational treatment

Pending Validation / Pending Review
→ amber warning/pending treatment

With Discrepancies
→ red discrepancy treatment

Closed
→ green success treatment

Cancelled
→ neutral treatment

Always include visible text.

Do not communicate status by color alone.

==================================================
14. COUNTING SESSIONS ACTIONS
==================================================

Reproduce the action labels shown in Figma.

Typical approved actions include:

Continue
Review
View Details

Do not invent:

Delete
Force Close
Approve
Supervisor Review

for the Operator list.

The real target URLs are backend integration points.

Use safe placeholders or context-provided action URLs.

==================================================
15. COUNTING SESSIONS PAGINATION
==================================================

If pagination is shown in the approved Figma:

reuse the shared pagination component.

The preview can show static pagination state.

Do not implement server-side pagination logic.

Backend integration should later provide:

current_page
total_pages
previous_url
next_url

or an equivalent agreed shape.

==================================================
16. NEW COUNTING SESSION PAGE
==================================================

Implement the approved:

New Counting Session

screen.

This page represents session setup before counting begins.

The approved workflow contains:

SECTION 1
Session Setup

SECTION 2
Expected Inventory Preview

SECTION 3
Instrument Readiness

Follow the Figma visual hierarchy exactly.

==================================================
17. SESSION SETUP
==================================================

The Session Setup section must support these approved concepts:

Operator
Operation / Procedure
Patient
Physician / Surgeon
Operating Room
Kit
Capture Station

Use the exact labels shown in the approved Figma reference.

The Operator field should visually represent the current authenticated Operator.

Operation / Procedure should be selectable.

Patient
Physician / Surgeon
Operating Room

are conceptually loaded from the selected operation and should appear read-only.

Kit and Capture Station are selectable operational inputs.

Do not add unrelated clinical fields.

Do not add diagnosis, medical history, or sensitive patient information beyond what is already approved in Figma.

==================================================
18. SESSION SETUP JINJA DATA
==================================================

Prepare the template for backend-provided data.

Expected concepts may include:

current_user

operations

selected_operation

available_kits

available_capture_stations

Each operation should be able to expose enough display information for:

operation_id
procedure_name
patient_name
physician_name
operating_room_name

Do not depend on actual SQLAlchemy model implementations yet.

Use presentation-friendly dictionaries/objects in the preview harness.

==================================================
19. OPERATION-DEPENDENT DISPLAY
==================================================

The approved UX concept is:

Select Operation / Procedure
→ display the associated:
  Patient
  Physician / Surgeon
  Operating Room

Because the real backend is not available yet:

implement this only as lightweight presentation-layer behavior if it can be done cleanly using data rendered into the HTML by Jinja.

A suitable approach is:

- each Operation option may include descriptive data-* attributes
- page JavaScript reads those attributes
- read-only fields update when the selection changes

Example concepts:

data-patient-name
data-physician-name
data-operating-room-name

Do not embed business queries or database logic in JavaScript.

Do not create a large hard-coded JavaScript object containing real application data.

The server will eventually render the operation data.

All JavaScript identifiers must remain English.

==================================================
20. NEW SESSION PAGE JAVASCRIPT
==================================================

If page-specific JavaScript is required, create a descriptive file such as:

static/js/pages/new-counting-session.js

or follow the existing JavaScript organization if a pages directory already exists.

Its responsibilities should be limited to presentation behavior such as:

- updating operation-dependent read-only display fields
- possibly updating visual readiness/preview data already present in the DOM

Do not implement:

- session creation API calls
- database operations
- authentication
- backend validation

==================================================
21. EXPECTED INVENTORY PREVIEW
==================================================

Implement the approved:

Expected Inventory Preview

section.

Use the Figma reference for its exact representation.

It should communicate the expected contents associated with the selected Kit.

Render expected inventory using Jinja-friendly repeated data.

Example conceptual fields:

instrument_family_name
expected_quantity

Use:

{% for item in expected_inventory %}

when appropriate.

Do not hard-code the final inventory into the production template.

Demo fallback data may be supplied by the preview harness.

==================================================
22. INSTRUMENT READINESS
==================================================

Implement the approved:

Instrument Readiness

section.

Represent the approved summary concepts, such as:

Expected
Available
Missing

and the overall readiness state:

Instrument Set Ready

or:

Instrument Set Incomplete

Use the exact wording shown in the approved Figma if it differs.

Use semantic visual status:

ready
→ success

incomplete
→ warning / danger as defined by the approved design

Do not add new operational rules.

==================================================
23. NEW SESSION ACTIONS
==================================================

Keep the approved page actions:

Cancel

Start Session

Follow the Figma placement.

If the Figma uses a sticky action footer:

implement it correctly inside the MAIN APPLICATION AREA only.

It must NOT extend underneath the sidebar.

The content must have sufficient bottom padding so the action area does not obscure the final section.

==================================================
24. NEW SESSION FORM
==================================================

Use one semantic form for session setup where practical.

Do not implement the real POST endpoint.

Do not invent a Flask route.

Use a safe preview-compatible action strategy.

Clearly document the expected backend integration:

Start Session
→ create WorkSession / session state
→ navigate to Active Counting Session

but do not implement that backend behavior.

==================================================
25. SESSION HISTORY PAGE
==================================================

Implement the approved:

Session History

screen.

This page represents completed historical sessions.

Use the Figma reference as the visual source of truth.

The page primarily contains:

Closed
Cancelled

sessions.

Do not mix the operational queue logic unnecessarily into this page.

==================================================
26. SESSION HISTORY FILTERS
==================================================

Reproduce the search/filter controls shown in Figma.

Expected concepts may include:

Search Sessions
Status
Kit
Operator
Date

Use the actual Figma reference for exact controls.

Do not invent filters.

==================================================
27. SESSION HISTORY TABLE
==================================================

Render rows through Jinja.

Expected concepts may include:

Session / Procedure
Kit
Operator
Started
Closed / Cancelled
Status
Action

Use exact approved columns.

Action should primarily be:

View Details

Do not invent edit actions for historical sessions.

==================================================
28. SESSION HISTORY STATUS
==================================================

Reuse shared badges.

Closed
→ green

Cancelled
→ neutral

Use icon/text/color consistently with the existing design system.

==================================================
29. SESSION HISTORY ACTION
==================================================

Use:

View Details

as shown in the approved Figma.

The target details page has not been implemented yet.

Therefore:

- accept an action_url from backend/demo context, or
- use a safe placeholder

Do not create the Session Details page in this iteration.

==================================================
30. BREADCRUMBS
==================================================

Implement breadcrumbs consistent with the current application.

Examples conceptually:

Operations / Counting Sessions

Operations / New Counting Session

Operations / Session History

Use the approved Figma wording if it differs.

Prepare breadcrumb labels for Flask-Babel.

==================================================
31. THEME SUPPORT
==================================================

All three new screens must work with the existing:

Light
Dark
System

theme implementation.

Do not introduce Light-only hard-coded colors.

Use existing semantic design tokens.

If new semantic tokens are genuinely needed, add them carefully to the existing token system rather than hard-coding colors inside page CSS.

==================================================
32. LANGUAGE SUPPORT
==================================================

Prepare all static visible strings with:

{{ _("...") }}

Do not configure Flask-Babel.

Do not create a JavaScript translation dictionary.

For user-visible strings needed by JavaScript, provide translated values from Jinja through data attributes where appropriate.

==================================================
33. REUSABLE CSS
==================================================

Reuse existing:

- cards
- forms
- tables
- badges
- buttons
- filters
- pagination

before creating page-specific CSS.

Only create page-specific styles for layout requirements that are truly unique.

Potential page-specific styles may belong under:

static/css/pages/

Avoid repeating visual rules that already exist in component CSS.

==================================================
34. ACCESSIBILITY
==================================================

Use semantic and accessible markup.

Ensure:

- form labels are associated with controls
- readonly fields are identifiable
- select controls remain keyboard usable
- focus styles remain visible
- table headers use <th>
- status includes text
- action buttons/links have meaningful accessible names

Do not use clickable generic <div> elements when a button or link is appropriate.

==================================================
35. RESPONSIVENESS
==================================================

Match the desktop Figma first.

Also preserve reasonable tablet-landscape usability.

At approximately 1024px width:

- sidebar must not overlap content
- filter bars may wrap cleanly
- tables must remain usable
- forms must not overflow
- action footer must remain inside main content
- cards should reflow when appropriate

Do not design a separate mobile app.

==================================================
36. DEMO PREVIEW CONTEXT
==================================================

Extend:

apps/BackendWebFlask/devtools/frontend_preview.py

ONLY to support previewing the new pages.

Add development-only routes such as:

/preview/operator/sessions

/preview/operator/sessions/new

/preview/operator/session-history

Use descriptive preview-only demo data matching the approved Figma examples where practical.

Do not import database models.

Do not initialize SQLAlchemy.

Do not connect to any database.

Do not implement real authentication.

==================================================
37. PREVIEW NAVIGATION
==================================================

For the DEVELOPMENT-ONLY preview harness, it is acceptable to make the Operator sidebar links navigate between the implemented preview pages if this can be done without changing production routing assumptions.

Do not hard-code preview URLs into reusable production templates if doing so would conflict with future backend routing.

Prefer preview context-provided URLs if necessary.

Document the approach.

==================================================
38. PREVIEW VALIDATION
==================================================

After implementation, run the existing preview harness and verify:

/preview/operator/dashboard

/preview/operator/sessions

/preview/operator/sessions/new

/preview/operator/session-history

All must render without Jinja errors.

Verify referenced CSS/JS files return successfully.

Check browser console or equivalent static verification for obvious JavaScript errors.

==================================================
39. VISUAL VALIDATION
==================================================

Compare each implemented page against its corresponding approved Figma PNG.

Check:

Counting Sessions:
- header
- filter bar
- table
- badges
- actions
- pagination

New Counting Session:
- session setup
- field layout
- expected inventory
- readiness
- Cancel / Start Session
- scrolling / action area

Session History:
- filters
- table
- historical status badges
- View Details actions
- pagination

==================================================
40. DATA CONTRACT DOCUMENTATION
==================================================

Update:

apps/BackendWebFlask/FRONTEND_INTEGRATION.md

Add the Jinja context expected by these new pages.

Document concepts such as:

sessions

operations

selected_operation

available_kits

available_capture_stations

expected_inventory

instrument_readiness

session_history

Document only frontend expectations.

Do not define database implementation.

==================================================
41. DO NOT INVENT BACKEND ROUTES
==================================================

Do not assume production endpoints such as:

operator.sessions
operator.new_session

unless they already exist.

They currently belong to backend implementation.

Use frontend integration placeholders cleanly.

==================================================
42. DO NOT IMPLEMENT BUSINESS LOGIC
==================================================

Do not implement:

- actual session creation
- database queries
- session state transitions
- kit availability calculations
- instrument reservation
- discrepancy detection
- YOLO
- WebSocket communication
- authentication

This iteration is the presentation layer only.

==================================================
43. DO NOT MODIFY APPROVED FOUNDATION
==================================================

Do not unnecessarily rewrite:

- base.html
- authenticated.html
- Operator sidebar
- header
- theme.js
- existing design tokens
- Operator Dashboard

Only modify shared files if a genuinely reusable component needed by these screens requires a small extension.

Do not regress the already approved:

Sign In
Operator Dashboard

==================================================
44. FINAL CHECKLIST
==================================================

COUNTING SESSIONS

□ Matches current approved Figma.
□ Uses authenticated layout.
□ Operator sidebar reused.
□ Correct active nav state.
□ Filters visually correct.
□ Sessions rendered with Jinja loop.
□ Status badge component reused.
□ Actions use backend-friendly labels/URLs.
□ No backend logic added.

NEW COUNTING SESSION

□ Matches Figma.
□ Session Setup exists.
□ Operator shown.
□ Operation / Procedure selectable.
□ Patient read-only.
□ Physician / Surgeon read-only.
□ Operating Room read-only.
□ Kit selectable.
□ Capture Station selectable.
□ Expected Inventory Preview exists.
□ Instrument Readiness exists.
□ Cancel exists.
□ Start Session exists.
□ Sticky action area, if used, does not overlap sidebar.
□ No real session creation is implemented.

SESSION HISTORY

□ Matches Figma.
□ Historical filters exist.
□ Table rendered using Jinja.
□ Closed status correct.
□ Cancelled status correct.
□ View Details present.
□ No edit operations added.

GLOBAL

□ Light works.
□ Dark works.
□ System works.
□ Static strings are translation-ready.
□ Source code remains English.
□ Existing Sign In is not broken.
□ Existing Operator Dashboard is not broken.
□ No backend-owned files modified.
□ Preview routes render successfully.

==================================================
COMPLETION REPORT
==================================================

When finished, report:

- files created
- files modified
- preview routes added
- Jinja context expected by each page
- JavaScript behavior added
- any shared components reused or extended
- any uncertainty found in the Figma references
- whether all preview pages rendered successfully
- whether Light/Dark/System were preserved
- confirmation that no backend-owned files were modified

Do not continue to Active Counting Session or Count Validation after completing this prompt.