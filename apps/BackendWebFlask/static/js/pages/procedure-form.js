/*
  IT Administrator Procedure form (New Procedure / Edit Procedure) — presentation-only row
  insertion for Associated Kits ("+ Add Kit") and Counting Phases ("+ Add Phase"), mirroring
  static/js/pages/kit-form.js's Kit Composition editor. Nothing here is persisted, submitted,
  or validated against real catalog data — the backend remains the source of truth for actual
  procedure/kit/phase associations.
*/
(function () {
    "use strict";

    function loadOptions(scriptId) {
        var script = document.getElementById(scriptId);
        if (!script) {
            return [];
        }
        try {
            return JSON.parse(script.textContent);
        } catch (error) {
            return [];
        }
    }

    function buildOptionsMarkup(options) {
        return options
            .map(function (option) {
                return '<option value="' + option.value + '">' + option.label + "</option>";
            })
            .join("");
    }

    function wireAddRemove(rowsBodyId, addButtonId, rowClass, removeClass, buildRow) {
        var rowsBody = document.getElementById(rowsBodyId);
        var addButton = document.getElementById(addButtonId);
        if (!rowsBody || !addButton) {
            return;
        }

        addButton.addEventListener("click", function () {
            rowsBody.insertAdjacentHTML("beforeend", buildRow());
        });

        rowsBody.addEventListener("click", function (event) {
            var removeButton = event.target.closest("." + removeClass);
            if (!removeButton) {
                return;
            }
            var row = removeButton.closest("." + rowClass);
            if (row) {
                row.remove();
            }
        });
    }

    var kitOptions = loadOptions("procedure-kit-options");
    var phaseOptions = loadOptions("procedure-phase-options");
    var removeLabel = (function () {
        var rowsBody = document.getElementById("associated-kits-rows");
        return rowsBody ? rowsBody.getAttribute("data-remove-label") || "Remove" : "Remove";
    })();

    wireAddRemove("associated-kits-rows", "add-kit-btn", "js-procedure-kit-row", "js-procedure-remove-row", function () {
        return (
            '<tr class="js-procedure-kit-row">' +
            '<td><div class="form-field__input-wrapper"><select class="form-field__input form-field__select" name="kit[]">' +
            buildOptionsMarkup(kitOptions) +
            "</select></div></td>" +
            '<td><input class="data-table__input" type="text" name="technique_label[]" value=""></td>' +
            '<td><div class="form-checkbox"><input type="checkbox" name="is_default[]"></div></td>' +
            '<td><div class="form-checkbox"><input type="checkbox" name="kit_active[]" checked></div></td>' +
            '<td class="data-table__action-column"><button type="button" class="link-danger js-procedure-remove-row">' +
            removeLabel +
            "</button></td>" +
            "</tr>"
        );
    });

    wireAddRemove("counting-phases-rows", "add-phase-btn", "js-procedure-phase-row", "js-procedure-remove-row", function () {
        return (
            '<tr class="js-procedure-phase-row">' +
            '<td><div class="form-field__input-wrapper"><select class="form-field__input form-field__select" name="phase[]">' +
            buildOptionsMarkup(phaseOptions) +
            "</select></div></td>" +
            '<td><div class="form-checkbox"><input type="checkbox" name="count_required[]" checked></div></td>' +
            '<td><input class="data-table__input" type="number" min="1" step="1" name="sort_order[]" value="1"></td>' +
            '<td><div class="form-checkbox"><input type="checkbox" name="phase_active[]" checked></div></td>' +
            "</tr>"
        );
    });
})();
