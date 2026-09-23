# 09 — Operator V2 Migration

Implement the approved V2 Operator UI in:

apps/BackendWebFlask/

Proceed directly with implementation.

Do NOT perform another full-project audit.
Do NOT reread prompts 01–08 unless a specific implementation issue requires it.
Do NOT spend tokens explaining your plan before editing files.

Use only the context needed for this task:

- ui_reference_v2/operator/
- ui_reference_v2/SCREEN_MANIFEST_V2.md
- V2_IMPLEMENTATION_NOTES.md
- controllers/routes.py and Operator-related controllers/helpers
- templates/operator/
- templates/components/ and templates/partials/ only when reused
- static/css/pages/ and shared component CSS only when necessary
- static/js/ only for Operator interactions that already exist or are required

The approved PNG files in ui_reference_v2/operator/ are the visual source of truth.

Do not modify the PNG files.
Do not display the reference PNGs directly in the application.

--------------------------------------------------
SCOPE
--------------------------------------------------

Migrate ONLY the Operator role.

Do not modify Supervisor or Administrator pages in this prompt.

Do not rebuild the shared V2 foundation completed in Prompt 08.

Reuse the existing:
- header
- sidebar
- breadcrumbs
- cards
- tables
- forms
- alerts
- badges
- modal patterns
- theme/language controls

Extend existing components instead of creating duplicates.

--------------------------------------------------
APPROVED OPERATOR REFERENCES
--------------------------------------------------

Implement/update these screens:

1. operator-dashboard.png
2. counting-sessions.png
3. new-counting-session.png
4. active-session-capture.png
5. active-session-ai-detection.png
6. human-validation-correction.png
7. count-validation-summary.png
8. discrepancy-escalation.png
9. operator-awaiting-supervisor-review.png
10. operator-correction-requested.png
11. operator-ready-to-close.png
12. close-session-confirmation.png
13. session-history.png
14. closed-session-details.png

Use the exact filenames present in ui_reference_v2/operator/ if any name differs slightly.

--------------------------------------------------
IMPLEMENTATION STRATEGY
--------------------------------------------------

Preserve the existing Flask/Jinja MVC architecture.

Existing Operator templates should be UPDATED when possible.

Create new templates only for V2 states that do not currently have a clear equivalent.

These V2 screens represent states/stages of the existing WorkSession workflow.

Do NOT create new database entities simply because V2 has more screens.

Use existing WorkSession, count, discrepancy, evidence, audit and related domain models.

Do NOT invent database fields.

--------------------------------------------------
OPERATOR WORKFLOW TO REPRESENT
--------------------------------------------------

The V2 visual workflow is:

Dashboard
→ Counting Sessions
→ New Counting Session
→ Capture
→ AI Detection
→ Human Validation
→ Validation Summary

If no unresolved discrepancy:
→ Ready to Close
→ Close Confirmation
→ Closed Session Details

If discrepancy exists:
→ Discrepancy Escalation
→ Awaiting Supervisor Review

If Supervisor requests correction:
→ Correction Requested
→ Human Validation / recount
→ Validation Summary
→ Supervisor Review again

If Supervisor approves resolution:
→ Ready to Close
→ Close Confirmation
→ Closed Session Details

Do not implement Supervisor UI here.
Only render the Operator side of those states.

--------------------------------------------------
REFERENCE CASE — WS-026
--------------------------------------------------

Where presentation/example data is required, preserve the approved WS-026 example consistently:

Session:
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

Expected Inventory:
Kelly Clamp = 4
Foerster Sponge Forceps = 2
Mayo-Hegar Needle Holder = 2
Mayo Scissors = 1
Allis Tissue Forceps = 2

Total Expected = 11

AI:
Kelly Clamp = 4
Foerster Sponge Forceps = 2
Mayo-Hegar Needle Holder = 1
Mayo Scissors = 1
Allis Tissue Forceps = 1

Total AI Detected = 9

Human Validation:
Kelly Clamp = 4
Foerster Sponge Forceps = 2
Mayo-Hegar Needle Holder = 1
Mayo Scissors = 1
Allis Tissue Forceps = 2

Final Validated = 10

Human Correction:
Allis Tissue Forceps
AI 1 → Human 2

Remaining Discrepancy:
Mayo-Hegar Needle Holder
Expected 2
AI 1
Validated 1
Difference -1

Do not overwrite the original AI result after human correction.

--------------------------------------------------
SESSION STATUS TERMINOLOGY
--------------------------------------------------

Use these session status labels consistently where applicable:

In Progress
Awaiting Review
Correction Required
Ready to Close
Closed

Do not introduce additional status names only for visual convenience.

--------------------------------------------------
KEY SCREEN REQUIREMENTS
--------------------------------------------------

Dashboard:
Match operator-dashboard.png.

Counting Sessions:
Match list, filters, statuses and actions shown in the reference.

New Counting Session:
Preserve the frozen expected-inventory preview and session setup shown in V2.

Capture:
Show image capture area and Expected Inventory.

Reuse existing capture/upload behavior.
Do not replace working upload logic with simulation.

AI Detection:
Preserve:
Expected
AI Detected
Difference
Confidence
Result

AI values remain suggestions requiring human validation.

Human Validation:
Allow confirmation/correction using existing backend behavior.

A confirmation is not the same as a human correction.

Validation Summary:
Show:
Expected
AI Detected
Validated
Difference
Status

Preserve the human correction record separately from the unresolved discrepancy.

Discrepancy Escalation:
Show the unresolved discrepancy and audit/evidence context.
Operator cannot close the session while unresolved.

Awaiting Supervisor Review:
Read-only waiting state.

Correction Requested:
Show Supervisor instructions and captured evidence.
Provide the action to return to recount/validation.

Ready to Close:
Only render when discrepancies have been resolved/approved.

Close Session Confirmation:
Use the approved V2 modal.
Do not remove the existing backend close-session protections.

Closed Session Details:
Show the traceability chain:
Expected → AI → Human → Supervisor → Final Validated

Preserve historical:
- AI result
- human corrections
- supervisor resolution
- final validated count

--------------------------------------------------
ROUTES
--------------------------------------------------

Reuse existing Operator routes where practical.

Add narrowly scoped routes only when a distinct V2 state needs its own renderable page.

Prefer predictable session routes such as:

/operator/sessions/<id>/capture
/operator/sessions/<id>/ai-detection
/operator/sessions/<id>/validation
/operator/sessions/<id>/validation-summary
/operator/sessions/<id>/discrepancy
/operator/sessions/<id>/awaiting-review
/operator/sessions/<id>/correction
/operator/sessions/<id>/ready-to-close

These are guidance, not a requirement to duplicate routes if the current controller architecture supports the states more cleanly another way.

Do not break existing endpoint names unnecessarily.

--------------------------------------------------
CSS / JS
--------------------------------------------------

Reuse V2 shared styles from Prompt 08.

Only add Operator-specific CSS when the reference cannot be achieved using existing components.

Avoid duplicated CSS.

Preserve existing JavaScript functionality.

Do not build fake client-side state to replace server state.

--------------------------------------------------
VALIDATION
--------------------------------------------------

Do not spend time trying to provision a PostgreSQL database that is not available in this environment.

Perform the most efficient validation available:

- Jinja/template syntax
- Flask route/import checks
- static asset resolution
- isolated template renders where possible
- visual rendering/screenshots where possible without DB
- existing non-DB tests

Confirm that no Supervisor/Admin templates were unintentionally changed.

--------------------------------------------------
DOCUMENTATION
--------------------------------------------------

Append a concise Operator V2 section to:

V2_IMPLEMENTATION_NOTES.md

Include only:
- new templates/routes created
- major templates updated
- any backend integration still pending
- validation performed

Do not write a long implementation narrative.

--------------------------------------------------
FINAL RESPONSE
--------------------------------------------------

Keep the final response concise.

Report only:

1. Implemented Operator V2 screens
2. Files created/modified
3. Validation result
4. Real blockers or pending backend integration, if any

Do not repeat the prompt or provide a long audit summary.