/*
  Operator V3 Human Validation — live recalculation for the demo form.

  - Validated Count changes update the row Difference (validated - expected), Status,
    row highlight and whether a Correction Reason is required.
  - Evidence-required rows (Suture Needle Set) stay "Evidence Verification Required" until
    the evidence checkbox is checked; a count change there is a count discrepancy as well.
  - The TOTAL row (validated, net difference, review cases) follows the inputs.
  The same rules are applied server-side (view_data._operator_v3_hv_rows) when the form is
  submitted, so this script only mirrors them. Nothing is stored in the browser.
*/
(function () {
    "use strict";

    var table = document.querySelector("[data-hv-table]");
    if (!table) {
        return;
    }

    var labels = table.dataset;
    var rows = Array.prototype.slice.call(table.querySelectorAll("[data-hv-row]"));
    var HIGHLIGHT = ["data-table__row--highlight-danger", "data-table__row--highlight-warning"];

    function parseCount(input) {
        var raw = input.value.trim();
        if (!/^\d+$/.test(raw)) {
            return null;
        }
        var value = parseInt(raw, 10);
        return value <= 99 ? value : null;
    }

    function signed(value) {
        return value > 0 ? "+" + value : String(value);
    }

    function evidenceCheckbox(row) {
        var detail = table.querySelector('[data-hv-evidence-row][data-key="' + row.dataset.key + '"]');
        return detail ? detail.querySelector("[data-hv-evidence]") : null;
    }

    // Returns {validated, difference, code} for one row and updates its cells.
    function syncRow(row) {
        var input = row.querySelector("[data-hv-count]");
        var expected = parseInt(row.dataset.expected, 10);
        var validated = parseCount(input);
        var valid = validated !== null;
        input.setAttribute("aria-invalid", valid ? "false" : "true");

        var difference = valid ? validated - expected : 0;
        var evidenceRequired = row.hasAttribute("data-evidence-required");
        var checkbox = evidenceCheckbox(row);
        var verified = evidenceRequired && checkbox && checkbox.checked;

        var code;
        var reasonText = "";
        if (!valid || difference !== 0) {
            code = "unresolved";
        } else if (evidenceRequired && !verified) {
            code = "evidence_verification_required";
            reasonText = labels.labelReasonEvidence;
        } else if (evidenceRequired) {
            code = "evidence_verified";
            reasonText = labels.labelReasonVerified;
        } else {
            code = "matched";
            reasonText = labels.labelReasonNone;
        }
        var ui = {
            matched: [labels.labelMatched, "success"],
            unresolved: [labels.labelUnresolved, "danger"],
            evidence_verification_required: [labels.labelEvidenceRequired, "warning"],
            evidence_verified: [labels.labelEvidenceVerified, "success"]
        }[code];
        var needsReview = code === "unresolved" || code === "evidence_verification_required";

        // Difference cell.
        var diffCell = row.querySelector("[data-hv-diff]");
        diffCell.querySelector("strong").textContent = valid ? signed(difference) : "—";
        diffCell.classList.toggle("text-danger", !valid || difference !== 0);
        diffCell.classList.toggle("text-success", valid && difference === 0);

        // Status badge (outline without dot while review is needed, filled when matched).
        var badge = row.querySelector("[data-hv-status]");
        badge.className = "status-badge status-badge--" + ui[1] + (needsReview ? " status-badge--outline" : "");
        badge.querySelector("[data-hv-status-label]").textContent = ui[0];
        badge.querySelector(".status-badge__dot").hidden = needsReview;

        // Row highlight.
        HIGHLIGHT.forEach(function (name) { row.classList.remove(name); });
        if (ui[1] === "danger") {
            row.classList.add(HIGHLIGHT[0]);
        } else if (needsReview) {
            row.classList.add(HIGHLIGHT[1]);
        }

        // Correction reason: a select (required) for count discrepancies, text otherwise.
        var reasonCell = row.querySelector("[data-hv-reason-cell]");
        var select = row.querySelector("[data-hv-reason]");
        var text = row.querySelector("[data-hv-reason-text]");
        var mismatch = code === "unresolved";
        select.hidden = !mismatch;
        select.disabled = !mismatch;
        select.required = mismatch;
        text.hidden = mismatch;
        text.textContent = reasonText;
        reasonCell.className = needsReview ? "text-" + ui[1] : "";

        return { validated: valid ? validated : 0, expected: expected, needsReview: needsReview };
    }

    function syncTotals(results) {
        var validated = 0;
        var expected = 0;
        var cases = 0;
        results.forEach(function (result) {
            validated += result.validated;
            expected += result.expected;
            cases += result.needsReview ? 1 : 0;
        });
        var difference = validated - expected;

        table.querySelector("[data-hv-total-validated]").textContent = String(validated);
        var diffCell = table.querySelector("[data-hv-total-diff]");
        diffCell.textContent = signed(difference);
        diffCell.classList.toggle("text-danger", difference !== 0);
        diffCell.classList.toggle("text-success", difference === 0);

        var casesCell = table.querySelector("[data-hv-total-cases]");
        var template = cases === 1 ? labels.labelReviewCase : labels.labelReviewCases;
        casesCell.textContent = cases ? template.replace("{count}", cases) : labels.labelAllMatched;
        casesCell.classList.toggle("text-danger", cases > 0);
        casesCell.classList.toggle("text-success", cases === 0);
    }

    function syncAll() {
        syncTotals(rows.map(syncRow));
    }

    table.addEventListener("input", function (event) {
        if (event.target.matches("[data-hv-count]")) {
            syncAll();
        }
    });
    table.addEventListener("change", function (event) {
        if (event.target.matches("[data-hv-count], [data-hv-evidence]")) {
            syncAll();
        }
    });
    syncAll();
})();
