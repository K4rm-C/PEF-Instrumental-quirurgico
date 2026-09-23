# 12 — Global V2 Frontend Visual QA

This is the FINAL frontend task for the Surgical Instrument Traceability System.

The following V2 migrations are already complete:

- Shared / Public V2
- Operator V2
- Supervisor V2
- Administrator V2

This task is FRONTEND-ONLY.

Do NOT implement backend integration.
Do NOT modify database models.
Do NOT create migrations.
Do NOT implement pending POST persistence.
Do NOT add analytics queries.
Do NOT attempt to solve backend gaps documented in V2_IMPLEMENTATION_NOTES.md.

Those responsibilities belong to another team.

Proceed directly with visual QA and corrections.

--------------------------------------------------
CONTEXT EFFICIENCY
--------------------------------------------------

Do NOT perform another full-project audit.

Do NOT reread prompts 01–11.

Read only:

- V2_IMPLEMENTATION_NOTES.md
- ui_reference_v2/
- templates/
- static/css/
- static/js/ only where needed for existing frontend interactions
- routes only if needed to make a frontend screen reachable for QA

The approved PNGs inside:

ui_reference_v2/shared/
ui_reference_v2/operator/
ui_reference_v2/supervisor/
ui_reference_v2/administrator/

are the final visual source of truth.

Do not modify those reference images.

Do not explain your plan before working.

--------------------------------------------------
OBJECTIVE
--------------------------------------------------

Perform a final visual and frontend-consistency QA pass across the entire V2 interface.

Compare the implemented Flask/Jinja screens against their corresponding approved reference PNGs.

Fix only frontend discrepancies.

Do NOT redesign approved screens.

Do NOT add new screens unless an approved reference is currently missing from the implementation.

--------------------------------------------------
1. SHARED / PUBLIC QA
--------------------------------------------------

Verify:

- Public Landing Page
- Sign In
- shared app shell
- header
- breadcrumbs
- theme control
- language control
- user/profile area
- shared buttons
- cards
- badges
- tables
- forms
- modals
- alerts
- pagination

Check that:

- `/` renders the Landing Page
- Sign In matches the approved V2 reference
- no password recovery UI was added
- all visible application text is in English
- shared styles do not visually break role-specific screens

--------------------------------------------------
2. OPERATOR QA
--------------------------------------------------

Compare every approved Operator reference against its implementation.

Verify all 14 V2 screens:

- Dashboard
- Counting Sessions
- New Counting Session
- Capture
- AI Suggested Count
- Human Validation / Correction
- Validation Summary
- Discrepancy Escalation
- Awaiting Supervisor Review
- Correction Requested
- Ready to Close
- Close Session Confirmation
- Session History
- Closed Session Details

Check visual consistency of the approved WS-026 case.

Core values must remain consistent:

Expected = 11
AI Suggested = 9
Final Validated = 10

Allis Tissue Forceps:
AI 1 → Human 2
Human Correction

Mayo-Hegar Needle Holder:
Expected 2
AI 1
Validated 1
Difference -1

Do not visually imply that the final validated count becomes 11.

Check session statuses:

In Progress
Awaiting Review
Correction Required
Ready to Close
Closed

--------------------------------------------------
3. SUPERVISOR QA
--------------------------------------------------

Compare all approved Supervisor screens:

- Dashboard
- Sessions
- Discrepancies
- Review Detail
- Approve modal
- Request Correction modal
- Reject modal
- Session Details
- Session History
- Reports
- Indicators
- Audit Log

Confirm that Supervisor views use the same WS-026 values as Operator.

Make sure the UI clearly distinguishes:

- Expected
- AI Suggested
- Operator Validated
- Human Correction
- Discrepancy
- Supervisor Decision

IMPORTANT:

Supervisor approval resolves the discrepancy.

It does NOT change:

Expected = 11
Final Validated = 10

Reject must remain visually represented as an action, not as a fabricated persistent session status.

--------------------------------------------------
4. ADMINISTRATOR QA
--------------------------------------------------

Compare every approved Administrator reference.

Verify:

- Dashboard
- Instrument Families
- Instruments
- Kits
- Procedures
- Users
- Roles
- Vision Models
- Audit Log
- Configuration
- Operating Rooms
- Capture Stations
- all approved New/Edit forms
- all approved modals

Check terminology carefully.

Use:

Instrument Families

not:

Instrument Types

Users must use:

Assigned Roles

Role badges must display complete role names.

Roles must not introduce fabricated:

- Role Name
- Status

Capture Stations must not introduce:

- Station Code

Vision Models must not introduce unsupported:

- Model Name
- Confidence Threshold
- Description
- Class Name

Model Class mapping should remain:

YOLO Class ID
Instrument Family

--------------------------------------------------
5. GLOBAL VISUAL CONSISTENCY
--------------------------------------------------

Across all roles verify:

### Layout
- consistent sidebar width
- consistent app header height
- consistent content margins
- consistent breadcrumbs
- consistent maximum content width
- no accidental layout shifts

### Typography
- consistent page titles
- consistent section titles
- consistent body text
- consistent labels
- no unexplained font-size differences

### Components
- buttons use consistent sizes/styles
- badges are visually consistent
- cards use consistent radius/padding
- tables use consistent row/header styling
- filters use consistent controls
- forms use consistent spacing
- modals use consistent structure
- alerts use consistent hierarchy

### States
Use the same visual styling for equivalent states across Operator and Supervisor.

For example:

In Progress
Awaiting Review
Correction Required
Ready to Close
Closed

Do not allow the same status to use unrelated colors/styles across roles.

--------------------------------------------------
6. OVERFLOW / FRAME QA
--------------------------------------------------

Check every approved reference for common visual regressions:

- content outside viewport
- horizontal overflow
- cards extending beyond frame
- tables wider than container
- cropped text
- overlapping sections
- hidden buttons
- modal overflow
- incorrect z-index
- broken dropdown positioning
- sections covering other sections
- excessive empty space
- inconsistent vertical alignment

Pay special attention to:

- Administrator Dashboard
- Procedure forms
- Vision Model forms
- Supervisor Review Detail
- long Audit Log tables
- Operator session detail views

Do not reduce global header/sidebar dimensions merely to make content fit.

Fix the local layout instead.

--------------------------------------------------
7. REFERENCE FIDELITY
--------------------------------------------------

The V2 PNGs are approved designs.

For each implemented screen verify:

- major content exists
- correct section ordering
- correct labels
- correct buttons
- correct tables
- correct card structure
- correct statuses
- correct modal content
- approximate spacing and proportions
- no obvious elements from V1 remain where V2 changed them

Do not obsess over microscopic pixel differences.

Prioritize visible structural differences and usability problems.

--------------------------------------------------
8. REMOVE FRONTEND ARTIFACTS
--------------------------------------------------

Search the V2 templates for obvious unfinished visual artifacts such as:

- "..."
- TODO
- TEMP
- Temporary Demo Data
- placeholder labels
- duplicate "+"
- malformed button text
- debug text
- broken icon placeholders
- accidental empty badges
- duplicate breadcrumbs

Only remove them when they are not intentionally part of the approved reference.

Do not remove legitimate presentation fallback data used to render the approved UI.

--------------------------------------------------
9. NAVIGATION QA
--------------------------------------------------

Verify frontend navigation links and actions point to the correct existing V2 screens.

Check sidebars for all three roles.

Administrator navigation:

HOME
- Dashboard

CATALOGS
- Instrument Families
- Instruments
- Kits
- Procedures

ADMINISTRATION
- Users
- Roles

SYSTEM
- Vision Models
- Audit Log
- Configuration

ACCOUNT
- My Profile
- Sign Out

Verify equivalent approved navigation for Operator and Supervisor.

Do not implement missing backend POST behavior.

Only correct frontend links/routes required for navigation between implemented views.

--------------------------------------------------
10. FRONTEND INTERACTION QA
--------------------------------------------------

Verify existing frontend-only interactions where available:

- modal open/close
- dropdowns
- tabs if present
- filters visually opening correctly
- form dynamic rows
- Procedure Add Kit / Add Phase UI
- Vision Model class mapping rows
- theme selector
- language selector
- navigation

Do not create frontend simulations of backend actions.

Buttons whose real operation requires unfinished backend integration may remain non-persisting as already documented.

--------------------------------------------------
11. VALIDATION STRATEGY
--------------------------------------------------

Be efficient.

Do NOT provision PostgreSQL.

Do NOT install large new toolchains merely for QA.

Use the best tools already available in the environment.

Prefer:

1. template/Jinja render checks
2. static asset checks
3. existing route checks
4. browser/headless rendering if already available
5. screenshots only where useful
6. visual comparison against approved PNG references

If full authenticated HTTP rendering is blocked by unavailable PostgreSQL, use the existing direct-render/fallback approach already established in previous V2 prompts.

Do not repeatedly validate unchanged backend functionality.

--------------------------------------------------
12. REGRESSION CHECK
--------------------------------------------------

After corrections, verify at minimum that representative screens from each area still render:

Shared:
- Landing
- Sign In

Operator:
- Dashboard
- Human Validation
- Closed Session Details

Supervisor:
- Dashboard
- Review Detail
- Indicators

Administrator:
- Dashboard
- Procedures
- Vision Models
- Configuration

If a shared CSS change is made, confirm it does not introduce visible regressions in another role.

--------------------------------------------------
13. DOCUMENTATION
--------------------------------------------------

Append a concise final section to:

V2_IMPLEMENTATION_NOTES.md

Title:

Frontend V2 — Final Visual QA

Document only:

- visual inconsistencies corrected
- files changed
- validation performed
- any remaining issues that are strictly backend responsibilities

Clearly state that backend integration items are OUT OF SCOPE for this frontend task.

Do not write a long development diary.

--------------------------------------------------
14. IMPORTANT BOUNDARY
--------------------------------------------------

This team's work ends at the frontend.

Do NOT attempt to fix documented backend gaps such as:

- database persistence
- CRUD POST handlers
- WorkSession database integration
- AI pipeline integration
- camera pipeline
- discrepancy persistence
- Supervisor decision persistence
- Vision Model activation service
- Audit Log database queries
- analytics queries
- database schema changes

If encountered, leave them unchanged and documented.

The goal is:

A visually complete, internally consistent, navigable V2 frontend ready for backend integration by the responsible team.

--------------------------------------------------
FINAL RESPONSE
--------------------------------------------------

Keep the final response short.

Return only:

1. Visual QA result
2. Frontend issues corrected
3. Files modified
4. Validation performed
5. Remaining backend-only items, if relevant

Do not provide another full-project audit.
Do not repeat the prompt.