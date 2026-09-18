# Complete Remaining Operator CDE Views

Complete ALL remaining approved Operator CDE frontend views for the
“Surgical Instrument Traceability System”.

This iteration completes the Operator presentation layer.

IMPORTANT:

Several Operator screens are already implemented and visually approved.

DO NOT rebuild, redesign, or regress:

- Shared Sign In
- Operator Dashboard
- Counting Sessions
- New Counting Session
- Session History

Reuse the existing frontend architecture and components.

The approved Figma PNG references located under:

apps/BackendWebFlask/ui_reference/

are the visual source of truth.

==================================================
1. SCOPE
==================================================

Complete the remaining Operator CDE views required by the approved Figma workflow.

Implement:

1. Active Counting Session
2. Count Validation — No Discrepancy state
3. Count Validation — Discrepancy state
4. Close Counting Session modal/state
5. Supervisor Review Required modal/state
6. Closed Session Details

Also implement any frontend-only reusable pieces required by these views.

Do NOT implement Supervisor pages.

Do NOT implement IT Administrator pages.

Do NOT implement real backend workflow logic.

==================================================
2. ALLOWED FILES
==================================================

You may create or modify frontend files under:

apps/BackendWebFlask/templates/
apps/BackendWebFlask/static/

You may extend:

apps/BackendWebFlask/devtools/frontend_preview.py

only for DEVELOPMENT-ONLY preview routes and demo context.

You may update:

apps/BackendWebFlask/FRONTEND_INTEGRATION.md

Do NOT modify:

- app.py
- extensions.py
- controllers/
- clients/
- models/
- production routes
- database logic
- authentication logic

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
- comments

Visible interface strings must remain translation-ready using:

{{ _("...") }}

Do not configure Flask-Babel.

Do not create a JavaScript translation dictionary.

==================================================
4. FIGMA REFERENCES
==================================================

Before implementation, inspect the approved Operator PNG references under:

apps/BackendWebFlask/ui_reference/

Identify the current approved references corresponding to:

- Active Counting Session
- Count Validation
- Count Validation with Discrepancy
- Close Counting Session
- Supervisor Review Required
- Closed Session Details

Use those PNGs as the visual source of truth.

Do not redesign these screens.

Do not rely on an older Figma state if a newer approved PNG exists.

==================================================
5. EXISTING FOUNDATION
==================================================

Reuse the currently approved:

- base.html
- guest layout
- authenticated layout
- Operator sidebar
- top header
- breadcrumbs
- design tokens
- Light / Dark / System implementation
- cards
- tables
- forms
- buttons
- badges
- alerts
- pagination
- filters
- modal patterns
- flash message pattern

Do not duplicate the application shell.

Do not create an alternate Operator sidebar.

==================================================
6. RECOMMENDED TEMPLATE STRUCTURE
==================================================

Extend the existing Operator session structure.

Recommended:

templates/
└── operator/
    └── sessions/
        ├── list.html                  # already exists
        ├── new.html                   # already exists
        ├── history.html               # already exists
        ├── active.html
        ├── validation.html
        └── closed_details.html

Use reusable modal partials under the existing component structure.

For example:

templates/components/modals/
├── close_session.html
└── supervisor_review_required.html

If equivalent modal component files already exist, reuse them.

Do not duplicate complete background pages just to represent a modal.

==================================================
7. ACTIVE COUNTING SESSION
==================================================

Implement the approved:

Active Counting Session

screen.

This screen represents an Operator currently performing an instrument counting session.

It must include the approved session context.

Expected concepts include:

Session ID
Procedure / Operation
Patient
Physician / Surgeon
Operating Room
Kit
Capture Station
Operator
Session Status

Use the exact labels and arrangement shown in Figma.

==================================================
8. ACTIVE SESSION STATUS
==================================================

The session must visually show:

In Progress

using the existing semantic status system.

Do not invent additional session statuses.

==================================================
9. CAPTURE AREA
==================================================

Reproduce the approved Capture Area.

The approved conceptual text is:

Reserved for camera-assisted instrument counting.

This area represents future integration with the camera / YOLO workflow.

Do NOT implement:

- camera access
- video
- YOLO
- WebSocket
- object detection
- image uploads

It is a visual placeholder only in this frontend iteration.

==================================================
10. INSTRUMENT COUNT
==================================================

Implement the approved Instrument Count section.

Display instrument rows using Jinja.

Expected concepts include:

instrument_name
expected_quantity
counted_quantity
difference
status

Provide touch-friendly:

decrement
increment

controls where shown in Figma.

Use semantic buttons with accessible labels such as:

Decrease Kelly Clamp count
Increase Kelly Clamp count

==================================================
11. COUNTING JAVASCRIPT
==================================================

Lightweight frontend-only JavaScript may simulate increment/decrement behavior for preview purposes.

If implemented, create or reuse:

static/js/pages/counting-session.js

Responsibilities may include:

- increment counted quantity
- decrement counted quantity
- prevent negative values
- update visible difference
- update local visual match/discrepancy state

IMPORTANT:

This is PREVIEW / PRESENTATION behavior only.

Do not treat JavaScript as the business source of truth.

Do not:

- save to a database
- call APIs
- infer workflow authorization
- close a real session
- communicate with YOLO

Document that the backend will later validate and persist counts.

==================================================
12. CURRENT ALERTS
==================================================

Implement the approved:

Current Alerts

section.

Show alert content only according to the Figma reference/demo state.

Use the existing alert component.

Do not invent a full alert-management system.

==================================================
13. ACTIVE SESSION ACTIONS
==================================================

Keep only the approved actions:

Cancel Session
Save Progress
Validate Count

Do NOT add:

Start Session
Approve Review
Close Session

to the Active Counting Session.

If the approved Figma uses a page action footer, reuse the existing:

page_action_bar

It must remain inside the MAIN APPLICATION AREA.

It must never extend underneath the sidebar.

==================================================
14. ACTIVE SESSION BACKEND CONTRACT
==================================================

Prepare the template for presentation-ready backend data.

Expected conceptual context may include:

session
instrument_counts
current_alerts

The session object should support concepts such as:

session_id
procedure_name
patient_name
physician_name
operating_room
kit_name
capture_station_name
operator_name
status_label
status_variant

Each instrument count should support:

instrument_name
expected_quantity
counted_quantity
difference
status_label
status_variant

Document the exact expected frontend contract in FRONTEND_INTEGRATION.md.

==================================================
15. COUNT VALIDATION TEMPLATE
==================================================

Implement Count Validation.

Prefer ONE reusable:

validation.html

template capable of presenting both:

- No Discrepancy
- Discrepancy

states through Jinja context.

Do not duplicate entire validation pages unless the approved designs are structurally incompatible.

The context should control the state.

Example concept:

has_discrepancy

Do not embed backend business queries inside the template.

==================================================
16. VALIDATION — SESSION CONTEXT
==================================================

Keep the approved session context information.

Expected concepts include:

Session ID
Procedure
Patient
Physician
Operating Room
Kit
Capture Station
Operator

Match the approved Figma arrangement.

==================================================
17. PRE / POST PROCEDURE COMPARISON
==================================================

Implement the approved:

PRE / POST Procedure Comparison

section.

NO DISCREPANCY example:

Expected:
11

Counted:
11

Difference:
0

Display the approved success message:

All instruments accounted for.

DISCREPANCY example:

Expected:
11

Counted:
10

Difference:
-1

Use the approved discrepancy treatment.

==================================================
18. COUNT COMPARISON
==================================================

Render the Count Comparison using Jinja.

Expected columns include:

Instrument
Expected
Counted
Difference
Status

Use exact Figma wording if different.

Typical rows include:

Kelly Clamp
Foerster Sponge Forceps
Mayo-Hegar Needle Holder
Mayo Scissors
Allis Tissue Forceps

Do not hard-code production rows.

Use preview demo context.

==================================================
19. MATCH / DISCREPANCY STATUS
==================================================

Use shared badges/status treatment.

Match
→ success / green

Discrepancy
→ danger / red

Always include visible text and/or approved icon.

Do not rely solely on color.

==================================================
20. NO DISCREPANCY VALIDATION STATE
==================================================

When there is no discrepancy:

Display:

All instruments accounted for.

Actions:

Edit Count
Confirm Validation

Edit Count conceptually returns to Active Counting Session.

Confirm Validation conceptually opens:

Close Counting Session

Do not implement backend state changes.

==================================================
21. CLOSE COUNTING SESSION MODAL
==================================================

Implement the approved reusable modal:

Close Counting Session

Expected message should follow the approved Figma content.

Actions:

Keep Session Open
Close Session

Use a real modal component in the HTML.

Do not duplicate the entire validation page as a separate template.

Modal requirements:

- centered
- dimmed backdrop
- accessible dialog semantics
- focus management where practical
- Escape may close when appropriate
- primary and secondary actions clearly distinguished

==================================================
22. CLOSE MODAL FRONTEND BEHAVIOR
==================================================

For preview purposes:

Confirm Validation
→ opens Close Counting Session modal

Keep Session Open
→ closes modal

Close Session
→ may navigate in the DEVELOPMENT preview to Closed Session Details

Do not implement real session closure.

Production destination remains a backend integration point.

==================================================
23. DISCREPANCY VALIDATION STATE
==================================================

When a discrepancy exists:

Show the approved red discrepancy summary.

The Count Comparison must clearly highlight the affected instrument.

Use the approved example concept:

Mayo-Hegar Needle Holder

Expected:
2

Counted:
1

Difference:
-1

==================================================
24. DISCREPANCY REVIEW INFORMATION
==================================================

Implement the approved Operator discrepancy review section.

Expected concepts include:

Discrepancy Reason
Verification Notes

Use the exact Figma labels.

Reason may be represented as a select control if shown that way.

Verification Notes may use a textarea.

Prepare these fields for backend integration.

Do not save them to a database.

==================================================
25. DISCREPANCY VALIDATION ACTIONS
==================================================

Keep the approved actions:

Edit Count
Save Review
Confirm Validation

Do not add Supervisor-only decisions.

The Operator must not see:

Approve Review
Request Correction

==================================================
26. SUPERVISOR REVIEW REQUIRED MODAL
==================================================

When the Operator confirms validation while an unresolved discrepancy exists, implement the approved modal:

Supervisor Review Required

Show the approved message explaining that the unresolved discrepancy requires Supervisor review before closure.

Include compact context such as:

Session
Instrument
Expected
Counted

according to the approved Figma.

Actions:

Back to Validation
Submit for Review

==================================================
27. SUPERVISOR REVIEW MODAL BEHAVIOR
==================================================

For preview:

Confirm Validation
→ opens Supervisor Review Required modal

Back to Validation
→ closes modal

Submit for Review
→ may return to Counting Sessions preview

The Counting Sessions demo state may show:

Pending Review

for WS-026.

If a flash/banner is used, use the already approved feedback pattern:

Review submitted successfully.

Supporting text:

Session WS-026 has been sent to the Supervisor for review.

Do not implement real cross-role data persistence.

==================================================
28. PENDING REVIEW OPERATOR RULE
==================================================

Represent the approved presentation rule:

When a session is Pending Review:

Operator should not receive:

Continue
Close Session

Use:

View Details

or the exact approved action shown in Figma.

This is presentation/demo behavior only.

Backend will enforce the actual rule.

==================================================
29. CLOSED SESSION DETAILS
==================================================

Implement the approved:

Closed Session Details

screen.

This is a read-only view.

Expected sections include the approved Figma information such as:

Session Information

Pre/Post Procedure Status

Validated Count

Session Activity Log

and the approved success indication:

All Instruments Accounted For

Use exact Figma terminology where available.

==================================================
30. CLOSED SESSION INFORMATION
==================================================

Expected concepts include:

Session ID
Procedure
Patient
Physician
Operating Room
Kit
Operator
Capture Station
Started
Closed

Render using Jinja-friendly values.

Do not hard-code business values into the production template.

==================================================
31. CLOSED SESSION COUNT
==================================================

Render the validated count using Jinja.

Use the approved table structure.

The screen must remain read-only.

Do not add increment/decrement controls.

Do not add Edit Count.

==================================================
32. SESSION ACTIVITY LOG
==================================================

Render the approved Session Activity Log.

Expected presentation data may include:

timestamp
event_label
actor_name
details

Do not infer audit events in Jinja.

Receive presentation-ready activity data from context.

==================================================
33. CLOSED DETAILS ACTIONS
==================================================

Keep only approved actions such as:

Back to Sessions

Export Summary

if present in the approved Figma.

Export Summary does not require real file generation in this frontend iteration.

Do not invent another page.

==================================================
34. BREADCRUMBS
==================================================

Use breadcrumbs consistent with the approved Operator flow.

Examples conceptually:

Operations / Counting Sessions / WS-026

Operations / Counting Sessions / WS-026 / Validation

Operations / Session History / WS-026

Follow the Figma reference where wording differs.

All static breadcrumb text must remain translation-ready.

==================================================
35. MODAL JAVASCRIPT
==================================================

Reuse the generic modal implementation already created.

Do not write separate modal systems for:

Close Counting Session
Supervisor Review Required

Extend generic modal behavior only when genuinely necessary.

==================================================
36. FEEDBACK
==================================================

Reuse the existing feedback/flash style.

Expected frontend feedback concepts include:

Progress saved successfully.

Review saved successfully.

Review submitted successfully.

Session closed successfully.

Do not implement backend persistence behind these messages.

If preview interactions display them, clearly keep them development/demo-only.

==================================================
37. THEME SUPPORT
==================================================

All remaining Operator views must work correctly with:

Light
Dark
System

Reuse semantic CSS variables.

Do not introduce hard-coded Light-only backgrounds/text colors.

==================================================
38. LANGUAGE SUPPORT
==================================================

All static interface strings must use:

{{ _("...") }}

Do not configure Flask-Babel.

Do not implement JavaScript translation dictionaries.

Any JavaScript-visible message that must eventually translate should be supplied from the template through data attributes or equivalent server-rendered values.

==================================================
39. RESPONSIVENESS
==================================================

Match desktop Figma first.

Maintain reasonable usability around tablet-landscape widths.

Pay particular attention to:

- Active Counting Session two-column content
- Count Comparison tables
- validation sections
- action footers
- Closed Session Details

At narrower widths:

- columns may stack where appropriate
- tables may use controlled horizontal scrolling if necessary
- action buttons may wrap

The sidebar must not overlap main content.

==================================================
40. ACCESSIBILITY
==================================================

Maintain:

- semantic headings
- form labels
- accessible buttons
- keyboard focus
- modal dialog semantics
- meaningful aria-labels for +/- controls
- status text in addition to color
- readable contrast in Light/Dark modes

==================================================
41. PREVIEW ROUTES
==================================================

Extend the development preview harness with routes for the remaining Operator views.

Suggested routes:

/preview/operator/sessions/active

/preview/operator/sessions/validation

/preview/operator/sessions/validation-discrepancy

/preview/operator/sessions/closed

Use existing Operator preview navigation where practical.

Do not modify production routes.

==================================================
42. PREVIEW STATE — NO DISCREPANCY
==================================================

Provide demo context for validation with:

expected_total = 11
counted_total = 11
difference = 0
has_discrepancy = false

All instrument rows should visually match.

==================================================
43. PREVIEW STATE — DISCREPANCY
==================================================

Provide demo context for discrepancy validation with:

expected_total = 11
counted_total = 10
difference = -1
has_discrepancy = true

Use the approved Mayo-Hegar example.

==================================================
44. PREVIEW INTERACTIONS
==================================================

Where practical in the DEVELOPMENT preview only, allow testing this visual workflow:

New Counting Session
→ Active Counting Session

Active Counting Session
→ Validate Count

Validation No Discrepancy
→ Confirm Validation
→ Close Session modal
→ Closed Session Details

Validation Discrepancy
→ Confirm Validation
→ Supervisor Review Required modal
→ Submit for Review
→ Counting Sessions

Do NOT introduce production routes to accomplish this.

Use preview context URLs/data attributes.

==================================================
45. DO NOT REGRESS EXISTING OPERATOR PAGES
==================================================

Before finishing verify that these still render correctly:

Shared Sign In

Operator Dashboard

Counting Sessions

New Counting Session

Session History

Do not redesign or rewrite them unless a small reusable compatibility adjustment is absolutely necessary.

==================================================
46. FRONTEND INTEGRATION DOCUMENTATION
==================================================

Update:

apps/BackendWebFlask/FRONTEND_INTEGRATION.md

Document new expected context for:

Active Counting Session
Count Validation
Closed Session Details

Document modal integration points.

Clearly distinguish:

Frontend presentation behavior

from:

Backend-required workflow/state transitions.

==================================================
47. FINAL OPERATOR FRONTEND STRUCTURE
==================================================

At completion, the Operator presentation layer should cover the complete approved visual workflow:

Sign In
↓
Dashboard
↓
Counting Sessions
↓
New Counting Session
↓
Active Counting Session
↓
Count Validation

No Discrepancy:
↓
Close Counting Session
↓
Closed Session Details

Discrepancy:
↓
Supervisor Review Required
↓
Submit for Review
↓
Counting Sessions / Pending Review

And:

Session History
↓
Closed Session Details

==================================================
48. DO NOT IMPLEMENT SUPERVISOR
==================================================

Even though Submit for Review conceptually sends data to Supervisor:

Do NOT implement:

- Supervisor Dashboard
- Supervisor Sessions
- Supervisor Discrepancies
- Discrepancy Review
- Approve Review
- Request Correction

Those belong to the next role-specific prompt.

==================================================
49. DO NOT IMPLEMENT BACKEND
==================================================

Do not implement:

- WorkSession persistence
- SQLAlchemy queries
- API endpoints
- session state machines
- authorization
- audit persistence
- file exports
- camera
- YOLO
- Redis
- MongoDB
- WebSockets

Only presentation-layer code is in scope.

==================================================
50. VALIDATION
==================================================

Use the preview harness and verify all Operator routes render without:

- Jinja errors
- missing static assets
- JavaScript console errors

Verify Light and Dark modes.

Verify existing pages remain stable.

Compare all new views against the corresponding Figma PNG references.

==================================================
51. COMPLETION REPORT
==================================================

When finished, report:

- files created
- files modified
- preview routes added
- templates implemented
- modal components created/reused
- page-specific JavaScript added
- Jinja data contracts expected from backend
- frontend-only simulated interactions
- uncertainties found in Figma
- preview validation results
- theme validation results
- confirmation that existing Operator pages did not regress
- confirmation that no backend-owned files were modified

Do not continue to Supervisor or IT Administrator after completing this prompt.