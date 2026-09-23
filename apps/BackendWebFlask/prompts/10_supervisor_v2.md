# 10 — Supervisor V2 Migration

Continue the V2 frontend migration in:

apps/BackendWebFlask/

Shared V2 and Operator V2 are already complete.

Proceed directly with implementation.

IMPORTANT FOR CONTEXT EFFICIENCY:
- Do NOT perform another full-project audit.
- Do NOT reread prompts 01–09 unless a specific issue requires it.
- Do NOT re-audit Shared or Operator.
- Do NOT explain your implementation plan before editing files.
- Inspect only Supervisor-related files and shared components that are directly required.
- Keep the final response concise.

Read only the minimum context needed:

- V2_IMPLEMENTATION_NOTES.md
- ui_reference_v2/SCREEN_MANIFEST_V2.md
- ui_reference_v2/supervisor/
- templates/supervisor/
- Supervisor-related routes/controllers/view-data helpers
- directly reused templates/components/partials
- relevant shared CSS and Supervisor-specific CSS/JS

The approved PNG files inside:

ui_reference_v2/supervisor/

are the visual source of truth.

Do not modify those PNG files.
Do not display reference PNGs directly in the application.

The project is being modified directly without Git.
Do not stop for branch, commit, repository state, or Git confirmation.

--------------------------------------------------
SCOPE
--------------------------------------------------

Migrate ONLY the Supervisor role to V2.

Do not modify Administrator screens.

Do not redesign Operator V2.

A very small shared/Operator compatibility fix is allowed only if strictly required by the Supervisor implementation.

Reuse the V2 shared foundation already implemented:

- header
- sidebar
- breadcrumbs
- cards
- KPI cards
- tables
- filters
- forms
- badges
- alerts
- modals
- pagination
- language/theme controls

Extend existing components instead of duplicating them.

--------------------------------------------------
APPROVED SUPERVISOR SCREENS
--------------------------------------------------

Implement/update all approved screens present in:

ui_reference_v2/supervisor/

These should correspond to the following V2 views:

1. Supervisor Dashboard
2. Sessions
3. Discrepancies
4. Review Detail
5. Approve Review modal
6. Request Correction modal
7. Reject Review modal
8. Session Details
9. Session History
10. Reports
11. Indicators
12. Audit Log

Use the exact PNG filenames present in the folder if they differ from these descriptive names.

Existing Supervisor templates should be UPDATED whenever practical.

Create new templates/components only when a V2 screen has no existing equivalent.

--------------------------------------------------
SUPERVISOR WORKFLOW
--------------------------------------------------

The Supervisor workflow must represent:

Dashboard
→ Sessions / Discrepancies
→ Review Detail

From Review Detail the Supervisor can:

A) Approve
B) Request Correction
C) Reject

These are actions, not three unrelated workflows.

After Request Correction:
→ session returns to Operator as Correction Required

After the Operator corrects/resubmits:
→ Supervisor can review again

After a discrepancy is resolved/approved:
→ the Operator may eventually receive Ready to Close

Do not implement or redesign the Operator screens here.

--------------------------------------------------
REFERENCE CASE — WS-026
--------------------------------------------------

Use the approved WS-026 case consistently wherever presentation/example data is required.

Session ID:
WS-026

Procedure:
Appendectomy - OR-3

Patient:
Maria García

Physician:
Dr. James Wilson

Operating Room:
OR-3

Kit:
Delivery Kit

Capture Station:
CDE-01

Operator:
Alex Morgan

Supervisor:
Sophia Turner

Counting Phase:
Pre-Procedure

Expected inventory:

Kelly Clamp = 4
Foerster Sponge Forceps = 2
Mayo-Hegar Needle Holder = 2
Mayo Scissors = 1
Allis Tissue Forceps = 2

Total Expected = 11

AI Suggested Count:

Kelly Clamp = 4
Foerster Sponge Forceps = 2
Mayo-Hegar Needle Holder = 1
Mayo Scissors = 1
Allis Tissue Forceps = 1

Total AI = 9

Operator Validated:

Kelly Clamp = 4
Foerster Sponge Forceps = 2
Mayo-Hegar Needle Holder = 1
Mayo Scissors = 1
Allis Tissue Forceps = 2

Total Validated = 10

Human Correction:

Allis Tissue Forceps
AI = 1
Human = 2

Reason:
Second instrument partially occluded.

Remaining discrepancy:

Mayo-Hegar Needle Holder

Expected = 2
AI = 1
Operator Validated = 1
Difference = -1

This remaining discrepancy is the one reviewed by the Supervisor.

IMPORTANT:

If the Supervisor approves/resolves the missing Mayo-Hegar discrepancy, that does NOT mean the physical final validated count becomes 11.

The final validated count remains:

10

Expected remains:

11

The discrepancy is resolved through Supervisor review, not by fabricating a matching instrument count.

Preserve:

Expected = 11
Final Validated = 10

--------------------------------------------------
STATUS TERMINOLOGY
--------------------------------------------------

Use these Work Session statuses consistently where applicable:

In Progress
Awaiting Review
Correction Required
Ready to Close
Closed

For discrepancies, use existing backend statuses when available.

Conceptually V2 may display states such as:

Open
Under Review
Correction Required
Resolved
Approved

Do not invent persistent database values only for display purposes.

IMPORTANT:

"Reject" is a Supervisor ACTION.

Do not automatically create a persistent "Rejected" discrepancy/session status unless the existing backend model explicitly supports it.

--------------------------------------------------
REVIEW DETAIL
--------------------------------------------------

The Review Detail screen is the central Supervisor decision screen.

Match the approved V2 reference closely.

It should clearly expose available information such as:

- session context
- procedure
- patient
- operator
- counting phase
- expected count
- AI suggested count
- human validated count
- difference
- human correction history
- discrepancy information
- evidence
- traceability/audit information where represented in the reference

The Supervisor must be able to clearly distinguish:

AI suggestion
vs.
Human validation
vs.
Human correction
vs.
Supervisor decision

Do not overwrite historical AI values.

Do not treat AI output as the final authoritative count.

--------------------------------------------------
APPROVE ACTION
--------------------------------------------------

Implement/update the approved Approve modal.

The action represents Supervisor acceptance/resolution of the discrepancy.

Preserve existing backend behavior if already implemented.

Do not modify the validated instrument count merely because the discrepancy is approved.

For WS-026:

Expected = 11
Validated = 10

remains true after approval.

The resolution allows the workflow to progress; it does not falsify inventory history.

--------------------------------------------------
REQUEST CORRECTION ACTION
--------------------------------------------------

Implement/update the approved Request Correction modal.

The Supervisor should be able to provide correction instructions/comments as supported by the existing backend.

The resulting workflow should represent:

Awaiting Review
→ Correction Required

The Operator can then review the request and perform another validation/recount using the Operator V2 flow already implemented.

Do not create fake frontend-only persistence.

--------------------------------------------------
REJECT ACTION
--------------------------------------------------

Implement the approved Reject modal if it does not already exist.

Reject must remain a Supervisor action.

Reuse existing persistence/business logic where possible.

Do not invent new DB status fields.

If the current backend does not fully support the intended rejection persistence, implement the visual/action integration as far as safely possible and document the backend limitation instead of inventing schema.

--------------------------------------------------
SESSION DETAILS / HISTORY
--------------------------------------------------

Update Supervisor Session Details and Session History to match V2.

Preserve chronological traceability where supported:

Expected inventory
→ AI suggestion
→ Human validation
→ Human correction
→ Discrepancy
→ Supervisor review
→ Resolution

Historical data should remain distinguishable rather than being overwritten.

--------------------------------------------------
REPORTS
--------------------------------------------------

Match the approved V2 Reports screen.

Use:

View Report

where shown in the reference.

Do not add export functionality unless the current backend already implements it.

Do not invent report-generation services.

--------------------------------------------------
INDICATORS
--------------------------------------------------

Match the approved V2 Indicators screen.

Use the approved presentation values where fallback/demo presentation data is necessary:

Total Sessions = 156
Total Discrepancies = 8
AI-Human Agreement = 94.2%
Human Corrections = 12
Average Resolution Time = 18 min

Charts shown in the V2 reference include concepts such as:

- Sessions by Day
- Discrepancies by Instrument
- Discrepancies by Family
- Discrepancies by Type

Use existing chart infrastructure if available.

Do not add a new chart library if the project already has one.

Do not invent new backend analytics queries beyond what is already supported solely to make demo visuals work.

Use safe presentation fallback data if necessary and document that integration remains pending.

--------------------------------------------------
AUDIT LOG
--------------------------------------------------

Match the approved Supervisor Audit Log reference.

Expected event examples include:

VISION_RESULT
HUMAN_CORRECTION
CREATE_DISCREPANCY
SUBMIT_FOR_REVIEW
SUPERVISOR_REVIEW
APPROVE_DISCREPANCY

Example actors:

Alex Morgan — Operator actions
Sophia Turner — Supervisor actions

Use existing audit models/data where available.

Do not create fake database schema.

--------------------------------------------------
ROUTES
--------------------------------------------------

Reuse existing Supervisor routes and endpoint names wherever practical.

Add narrowly scoped routes only when required for a distinct V2 view or action.

Do not reorganize the entire controller layer.

Do not change Operator routes created during Prompt 09 unless absolutely necessary.

--------------------------------------------------
BACKEND / DATABASE RULES
--------------------------------------------------

Preserve existing Flask MVC and PostgreSQL integration.

Do NOT:

- invent database fields
- invent ORM relationships
- create schema migrations solely to satisfy the mockup
- replace backend state with JavaScript-only simulation
- overwrite AI results after human/supervisor actions

If a V2 field has no backend persistence yet:

1. preserve the visual representation where practical,
2. use existing data/fallback presentation mechanisms,
3. document the missing persistence in V2_IMPLEMENTATION_NOTES.md.

Do not block the entire Supervisor migration because one detail lacks backend support.

--------------------------------------------------
CSS / JS
--------------------------------------------------

Reuse the shared V2 styling already implemented.

Only add Supervisor-specific CSS when necessary.

Avoid duplicating:

- cards
- tables
- status badges
- modals
- alerts
- buttons
- filter controls

Preserve existing JavaScript behavior.

Do not create frontend-only workflow state as a replacement for backend state.

--------------------------------------------------
VALIDATION
--------------------------------------------------

Do NOT spend time provisioning PostgreSQL if it is unavailable in the current environment.

Perform efficient validation only:

- Jinja/template syntax
- Flask imports/routes
- static asset resolution
- isolated template rendering
- HTTP smoke tests where possible
- screenshots/headless rendering where practical
- comparison against Supervisor V2 reference PNGs
- existing relevant non-DB tests

Verify that:

- Shared V2 remains intact
- Operator V2 remains intact
- Administrator V1 has not been unintentionally modified

Do not repeatedly screenshot/test pages that were not touched.

--------------------------------------------------
DOCUMENTATION
--------------------------------------------------

Append a concise:

Supervisor V2

section to:

V2_IMPLEMENTATION_NOTES.md

Document only:

- templates created
- templates updated
- routes/actions added or changed
- important backend integration limitations
- validation performed

Do not produce a long development diary.

--------------------------------------------------
FINAL RESPONSE
--------------------------------------------------

Keep your response concise.

Return only:

1. Supervisor V2 screens implemented
2. Files created/modified
3. Validation result
4. Real blockers / pending backend integration

Do not repeat the prompt.
Do not provide another full-project audit.