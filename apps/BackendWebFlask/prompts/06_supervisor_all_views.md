# Complete Supervisor CDE / Quality Frontend

Implement ALL approved Supervisor CDE / Quality frontend views for the
“Surgical Instrument Traceability System”.

This iteration must complete the entire Supervisor presentation layer while reusing the already approved frontend architecture created for the Shared and Operator interfaces.

IMPORTANT:

The Shared and Operator frontend is already implemented and visually approved.

DO NOT rebuild, redesign, or regress:

- Shared Sign In
- Base layout
- Authenticated layout
- Theme system
- Shared components
- Operator Dashboard
- Operator Counting Sessions
- Operator New Counting Session
- Operator Active Counting Session
- Operator Count Validation
- Operator Session History
- Operator Closed Session Details
- Operator modals and workflow

Reuse the existing frontend architecture.

The approved Supervisor Figma PNG references located under:

apps/BackendWebFlask/ui_reference/

are the visual source of truth.

==================================================
1. SCOPE
==================================================

Implement the complete Supervisor CDE / Quality presentation layer.

Required approved screens:

1. Supervisor Dashboard
2. Supervisor Sessions
3. Supervisor Discrepancies
4. Supervisor Discrepancy Review
5. Supervisor Session Details
6. Supervisor Session History
7. Supervisor Reports
8. Supervisor Indicators
9. Supervisor Audit Log

Also implement the approved Supervisor modal / result states:

10. Approve Discrepancy Review modal
11. Request Operator Correction modal
12. Discrepancies — Post Approval state
13. Discrepancies — Post Correction state

Do NOT implement IT Administrator screens.

Do NOT implement backend workflow logic.

==================================================
2. ALLOWED FILES
==================================================

You may create or modify frontend files under:

apps/BackendWebFlask/templates/
apps/BackendWebFlask/static/

You may extend:

apps/BackendWebFlask/devtools/frontend_preview.py

ONLY for DEVELOPMENT-ONLY preview routes and demo context.

You may update:

apps/BackendWebFlask/FRONTEND_INTEGRATION.md

Do NOT modify:

- app.py
- extensions.py
- controllers/
- clients/
- models/
- database logic
- authentication logic
- production Flask routes

==================================================
3. FIRST STEP — INSPECT EXISTING FRONTEND
==================================================

Before writing code:

1. Inspect the existing frontend implementation.
2. Inspect the shared authenticated layout.
3. Inspect shared macros/components.
4. Inspect the theme system.
5. Inspect the Operator sidebar implementation as a structural reference.
6. Inspect the existing modal and feedback systems.
7. Inspect FRONTEND_INTEGRATION.md.

Do not recreate functionality that already exists.

Prefer extending reusable components over duplicating them.

==================================================
4. CODE LANGUAGE
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

Visible static UI text must remain translation-ready using:

{{ _("...") }}

Do not configure Flask-Babel.

Do not create JavaScript translation dictionaries.

==================================================
5. FIGMA REFERENCES
==================================================

Before implementing each screen, inspect its current approved Supervisor PNG reference under:

apps/BackendWebFlask/ui_reference/

Identify references corresponding to:

- Supervisor Dashboard
- Sessions
- Discrepancies
- Discrepancy Review
- Approval modal
- Request Correction modal
- Session Details
- Session History
- Reports
- Indicators
- Audit Log
- Post Approval Discrepancies
- Post Correction Discrepancies

Use the PNGs as the visual source of truth.

Do not redesign them.

If multiple versions exist, use the latest approved/current version and ignore OLD or obsolete references.

==================================================
6. SUPERVISOR SIDEBAR
==================================================

Implement or complete the Supervisor sidebar using the same structural patterns as the Operator sidebar.

Approved navigation:

HOME
- Dashboard

MONITORING
- Sessions
- Discrepancies
- Session History

REPORTS
- Reports
- Indicators

TRACEABILITY
- Audit Log

ACCOUNT
- My Profile
- Sign Out

Use the approved four-square grid Dashboard icon.

Do NOT use a house icon.

Do not invent additional navigation.

==================================================
7. ACTIVE NAVIGATION STATES
==================================================

Use the appropriate active navigation state:

Supervisor Dashboard
→ Dashboard

Sessions
→ Sessions

Session Details opened from monitoring
→ Sessions when appropriate

Discrepancies
→ Discrepancies

Discrepancy Review
→ Discrepancies

Post Approval
→ Discrepancies

Post Correction
→ Discrepancies

Session History
→ Session History

Reports
→ Reports

Indicators
→ Indicators

Audit Log
→ Audit Log

==================================================
8. SHARED HEADER
==================================================

Reuse the existing authenticated header.

Supervisor fallback/demo identity:

Sophia Turner

Role:

Supervisor CDE / Quality

Keep:

- breadcrumb
- notification icon
- language selector
- theme selector
- avatar
- user name
- role
- user-menu affordance

Do not invent notifications dropdown content.

Do not create My Profile.

==================================================
9. SUPERVISOR DASHBOARD
==================================================

Implement the approved Supervisor Dashboard.

Use the exact Figma reference.

Expected dashboard concepts include:

Sessions Today
Active Sessions
Pending Reviews
Open Discrepancies

Also reproduce approved sections such as:

Sessions by Day

Review Status

Sessions Requiring Attention

Do not invent additional metrics.

==================================================
10. DASHBOARD DATA
==================================================

Use backend-friendly Jinja context.

Expected conceptual data:

dashboard_stats
sessions_by_day
review_status
sessions_requiring_attention

Render repeated rows using Jinja loops.

Do not infer workflow business logic inside the template.

Use presentation-ready fields such as:

status_label
status_variant
action_label
action_url

==================================================
11. SUPERVISOR SESSIONS
==================================================

Implement the approved Supervisor Sessions screen.

This is a monitoring view.

Render its approved:

- page header
- filters
- table
- status badges
- actions
- pagination

Typical status concepts include:

Active
Pending Review
With Discrepancies
Closed

Use exact Figma wording.

==================================================
12. SUPERVISOR SESSION ACTIONS
==================================================

The Supervisor may see approved actions such as:

Review
View Details

Do NOT add Operator actions such as:

Continue
Edit Count
Validate Count

The Supervisor monitoring view must remain role appropriate.

==================================================
13. SUPERVISOR DISCREPANCIES
==================================================

Implement the approved Discrepancies screen.

Include its approved KPI summary cards, filters and table.

Expected summary concepts include:

Pending Reviews
Open Discrepancies
Reviewed Today

Use exact Figma values/labels for preview where practical.

==================================================
14. DISCREPANCY TABLE
==================================================

Render discrepancy records using Jinja.

Expected concepts may include:

session_id
procedure_name
kit_name
operator_name
instrument_name
expected_quantity
counted_quantity
difference
reason
status_label
status_variant
action_label
action_url

Use exact approved columns.

Do not hard-code production records.

==================================================
15. DISCREPANCY REVIEW
==================================================

Implement the approved canonical:

Supervisor Discrepancy Review

screen.

This must be ONE complete standalone review screen.

Approved section order:

1. Session Information
2. PRE / POST Procedure Comparison
3. Count Comparison
4. Operator Submission
5. Supervisor Review
6. Supervisor decision actions

Follow the Figma reference.

==================================================
16. SESSION INFORMATION
==================================================

Display approved context concepts such as:

Session ID
Procedure
Patient
Physician
Operating Room
Kit
Operator
Capture Station
Submitted
Status

Use presentation-ready Jinja context.

==================================================
17. PRE / POST PROCEDURE COMPARISON
==================================================

Implement the approved comparison.

Use the existing Operator comparison components/styles where appropriate rather than duplicating them.

Approved example concept:

PRE / Expected:
11

POST / Counted:
10

Difference:
-1

Use discrepancy semantic treatment.

==================================================
18. COUNT COMPARISON
==================================================

Render the instrument comparison using Jinja.

Expected columns:

Instrument
Expected
Counted
Difference
Status

Approved example rows include:

Kelly Clamp
Foerster Sponge Forceps
Mayo-Hegar Needle Holder
Mayo Scissors
Allis Tissue Forceps

The Mayo-Hegar row demonstrates the discrepancy.

Reuse shared table and badge patterns.

==================================================
19. SUPERVISOR COUNT RULE
==================================================

The Supervisor must NOT modify instrument counts.

Do not provide:

increment
decrement
Edit Count
count inputs

Expected / Counted / Difference are read-only.

==================================================
20. OPERATOR SUBMISSION
==================================================

Implement the approved read-only:

Operator Submission

section.

Expected fields:

Reason

Verification Notes

Use the approved example/demo content where practical.

These values are read-only for Supervisor.

==================================================
21. SUPERVISOR REVIEW SECTION
==================================================

Implement:

Supervisor Review

with:

Supervisor Notes

Use the approved textarea / field styling.

The Supervisor may enter review comments visually.

Do not persist them.

==================================================
22. REVIEW DECISION ACTIONS
==================================================

Keep the approved actions:

Request Correction

Approve Review

Use the approved visual hierarchy.

Request Correction:
warning / secondary

Approve Review:
primary teal

If the approved Figma uses the reusable page action bar, use it.

The action bar must remain inside the main application area and must never extend under the sidebar.

==================================================
23. APPROVE REVIEW MODAL
==================================================

Implement the approved reusable:

Approve Discrepancy Review?

modal.

Do NOT create a duplicate full-page background state in production templates.

Use the existing generic modal system.

Expected actions:

Cancel
Approve Review

Modal must:

- be centered
- use dimmed backdrop
- have accessible dialog semantics
- reuse generic modal JavaScript

==================================================
24. REQUEST CORRECTION MODAL
==================================================

Implement the approved:

Request Operator Correction?

modal.

Include the approved:

Reason / Instructions

field if present in Figma.

Actions:

Cancel
Request Correction

Reuse generic modal behavior.

Do not create a second modal system.

==================================================
25. FRONTEND REVIEW INTERACTIONS
==================================================

For DEVELOPMENT preview purposes only:

Approve Review
→ open approval modal

Cancel
→ close modal

Final Approve Review
→ navigate to Post Approval Discrepancies preview

Request Correction
→ open correction modal

Cancel
→ close modal

Final Request Correction
→ navigate to Post Correction Discrepancies preview

Do not implement actual workflow persistence.

==================================================
26. POST APPROVAL DISCREPANCIES
==================================================

Implement the approved Post Approval state.

This may reuse the standard Discrepancies template through presentation context if practical.

Avoid duplicating an entire page solely to represent changed demo data.

Approved result:

WS-026
→ Approved or Approved for Closure according to current Figma wording

Show the approved success feedback:

Review approved successfully.

Supporting text:

Session WS-026 has been approved and may proceed to closure.

Use the existing feedback-banner pattern.

==================================================
27. POST CORRECTION DISCREPANCIES
==================================================

Implement the approved Post Correction state.

Reuse the standard Discrepancies structure where practical.

Approved result:

WS-026
→ Correction Required

Show:

Correction request sent successfully.

Supporting text:

Session WS-026 has been returned to the Operator for correction.

Use amber/warning semantic feedback as approved.

==================================================
28. RESULT STATE ARCHITECTURE
==================================================

Prefer:

one discrepancies/list template

with context-controlled:

- rows
- metrics
- feedback state

rather than three duplicated complete page templates if the layouts are identical.

Keep the implementation maintainable.

==================================================
29. SUPERVISOR SESSION DETAILS
==================================================

Implement the approved:

Supervisor Session Details

screen.

This is read-only.

Expected approved sections may include:

Session Information
PRE / POST Procedure Status
Count Summary
Discrepancy Details
Corrections Log
Session Activity

Use exact Figma section names.

Do not invent editing controls.

==================================================
30. SESSION DETAILS CONTEXT
==================================================

Prepare presentation-ready context such as:

session
count_summary
discrepancy_details
correction_log
activity_log

Do not derive audit/business events in Jinja.

==================================================
31. SUPERVISOR SESSION HISTORY
==================================================

Implement the approved Session History screen.

It should contain historical sessions and:

View Details

actions.

Reuse filter/table/pagination components already built for Operator where appropriate.

Do not duplicate them unnecessarily.

==================================================
32. SUPERVISOR REPORTS
==================================================

Implement the approved Reports screen.

Reproduce the existing approved report cards/categories.

Possible approved concepts include:

Session Summary
Discrepancy Summary
Kit Activity
Operator Activity

Use exact Figma content.

Do not invent reports.

==================================================
33. REPORT ACTIONS
==================================================

Buttons such as:

View Report
Export

are presentation-only in this iteration.

Do not create report detail pages unless there is an approved Figma screen for them.

Do not implement file generation.

==================================================
34. SUPERVISOR INDICATORS
==================================================

Implement the approved Indicators screen.

Use the exact Figma KPI cards and charts.

Reuse dashboard/chart visual patterns already implemented where practical.

Do not introduce a chart framework unless already necessary and approved.

Prefer lightweight HTML/CSS/SVG using backend-provided values.

==================================================
35. INDICATOR DATA
==================================================

Prepare backend-friendly context for:

KPI values
sessions by day
discrepancies by reason
session status
discrepancies by kit

Do not calculate analytics in Jinja.

==================================================
36. AUDIT LOG
==================================================

Implement the approved:

Audit Log

screen.

This is read-only.

Render approved columns and filters using Jinja.

Expected concepts may include:

timestamp
user
role
event_type
resource
details

Use exact Figma columns.

Do NOT add:

Edit
Delete
Modify

==================================================
37. SHARED COMPONENT REUSE
==================================================

Before creating any new CSS or macro, check whether the existing frontend already provides an appropriate:

- card
- stat card
- table
- badge
- alert
- filter bar
- pagination
- modal
- form
- page action bar
- feedback banner
- session-details component

Extend shared components minimally when needed.

Do not create Supervisor-specific versions of generic components unless structurally necessary.

==================================================
38. THEME SUPPORT
==================================================

Every Supervisor screen must support:

Light
Dark
System

using existing semantic tokens.

Do not introduce Light-only hard-coded component colors.

==================================================
39. LANGUAGE SUPPORT
==================================================

All static visible text must use:

{{ _("...") }}

Do not configure Flask-Babel.

Do not create a client-side translation dictionary.

JavaScript-visible messages that eventually need translation should come from Jinja-rendered data attributes or equivalent mechanisms.

==================================================
40. RESPONSIVENESS
==================================================

Match desktop Figma first.

Maintain reasonable tablet-landscape usability.

Pay particular attention to:

- wide monitoring tables
- Discrepancy Review
- Session Details
- Reports
- Indicators
- Audit Log

Tables may use controlled horizontal scrolling when necessary.

Do not allow application-level horizontal overflow.

Sidebar must never overlap content.

==================================================
41. ACCESSIBILITY
==================================================

Maintain:

- semantic headings
- table headers
- visible focus states
- accessible modal semantics
- form labels
- status text plus color
- adequate contrast
- meaningful accessible button names

Supervisor personnel may not be highly technical.

Keep workflows predictable.

==================================================
42. PREVIEW ROUTES
==================================================

Extend the DEVELOPMENT preview harness with Supervisor routes.

Suggested:

/preview/supervisor/dashboard

/preview/supervisor/sessions

/preview/supervisor/discrepancies

/preview/supervisor/discrepancies/review

/preview/supervisor/discrepancies/post-approval

/preview/supervisor/discrepancies/post-correction

/preview/supervisor/sessions/details

/preview/supervisor/session-history

/preview/supervisor/reports

/preview/supervisor/indicators

/preview/supervisor/audit-log

These are preview-only.

Do not create production Flask routes.

==================================================
43. PREVIEW NAVIGATION
==================================================

For development preview, make the Supervisor sidebar usable through context-provided preview URLs, following the same pattern already established for Operator.

Do not hard-code preview-only URLs into production templates if that would interfere with backend integration.

==================================================
44. PREVIEW PRIMARY REVIEW FLOW
==================================================

Allow testing this frontend-only scenario:

Supervisor Dashboard
↓
Discrepancies
↓
Review WS-026
↓
Discrepancy Review
↓
Approve Review
↓
Approval Modal
↓
Approve Review
↓
Post Approval Discrepancies

==================================================
45. PREVIEW CORRECTION FLOW
==================================================

Also allow:

Supervisor Dashboard
↓
Discrepancies
↓
Review WS-026
↓
Discrepancy Review
↓
Request Correction
↓
Request Correction Modal
↓
Request Correction
↓
Post Correction Discrepancies

==================================================
46. ROLE ISOLATION
==================================================

Supervisor sidebar/templates must NOT expose:

Operator-only:
- New Session
- Continue
- Validate Count

Administrator-only:
- Instrument Families
- Instruments
- Kits
- Users
- Roles
- Configuration

Do not mix role navigation.

==================================================
47. DATA CONTRACT DOCUMENTATION
==================================================

Update:

apps/BackendWebFlask/FRONTEND_INTEGRATION.md

Document Supervisor context contracts for:

Dashboard
Sessions
Discrepancies
Discrepancy Review
Session Details
Session History
Reports
Indicators
Audit Log

Document modal integration points.

Clearly separate frontend simulation from backend requirements.

==================================================
48. DO NOT IMPLEMENT BACKEND
==================================================

Do not implement:

- SQLAlchemy queries
- discrepancy persistence
- approval persistence
- correction persistence
- authorization
- audit writes
- report generation
- database updates
- APIs

Only presentation-layer implementation is allowed.

==================================================
49. DO NOT REGRESS SHARED / OPERATOR FRONTEND
==================================================

Before finishing verify existing preview routes still render correctly.

At minimum confirm:

Shared Sign In

Operator Dashboard

Operator Counting Sessions

Operator New Counting Session

Operator Active Counting Session

Operator Validation

Operator Session History

Operator Closed Session Details

Do not redesign them.

==================================================
50. FINAL SUPERVISOR CHECKLIST
==================================================

DASHBOARD

□ Matches Figma.
□ Correct Supervisor sidebar.
□ KPI cards correct.
□ Sessions Requiring Attention correct.

SESSIONS

□ Monitoring table correct.
□ Review actions correct.
□ View Details correct.
□ No Operator actions.

DISCREPANCIES

□ Summary cards correct.
□ Discrepancy table correct.
□ Review / View Details correct.

DISCREPANCY REVIEW

□ Session context correct.
□ PRE / POST comparison correct.
□ Count Comparison correct.
□ Operator Submission read-only.
□ Supervisor Notes exists.
□ Request Correction exists.
□ Approve Review exists.

MODALS

□ Approval modal works.
□ Correction modal works.
□ Generic modal system reused.

POST STATES

□ Post Approval state correct.
□ Success feedback correct.
□ Post Correction state correct.
□ Warning feedback correct.

SESSION DETAILS

□ Read-only.
□ All approved sections present.

SESSION HISTORY

□ Matches Figma.
□ View Details available.

REPORTS

□ Matches approved Figma.
□ No invented report screens.

INDICATORS

□ Matches approved Figma.
□ Charts/KPIs presentation-only.

AUDIT LOG

□ Read-only.
□ No destructive actions.

GLOBAL

□ Light works.
□ Dark works.
□ System works.
□ Translation-ready.
□ Source code remains English.
□ Shared frontend not regressed.
□ Operator frontend not regressed.
□ No backend-owned files modified.

==================================================
51. COMPLETION REPORT
==================================================

When finished, report:

- files created
- files modified
- Supervisor templates implemented
- preview routes added
- shared components reused or extended
- new page-specific CSS/JS
- Jinja context contracts
- frontend-only interactions
- uncertainties found in Figma
- preview validation results
- Light/Dark/System validation
- Shared/Operator regression validation
- confirmation that no backend-owned files were modified

Do not continue to IT Administrator after completing this prompt.