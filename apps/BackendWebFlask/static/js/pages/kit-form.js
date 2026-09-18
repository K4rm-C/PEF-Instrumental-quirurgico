/*
  IT Administrator Kit form (New Kit / Edit Kit) — presentation-only Kit Composition editing
  (implementation prompt section 19): "+ Add Instrument Type" appends another composition
  row, each row's "Remove" removes it, and the Instrument Types / Total Expected Instruments
  summary tiles recompute from the rows currently on the page. Nothing here is persisted,
  submitted, or validated against real catalog data — the backend remains the source of truth
  for actual kit composition.
*/
(function () {
    "use strict";

    var rowsBody = document.getElementById("kit-composition-rows");
    var addButton = document.getElementById("add-instrument-type-btn");
    var optionsScript = document.getElementById("kit-instrument-family-options");

    if (!rowsBody || !addButton || !optionsScript) {
        return;
    }

    var familyOptions = [];
    try {
        familyOptions = JSON.parse(optionsScript.textContent);
    } catch (error) {
        familyOptions = [];
    }

    function buildOptionsMarkup() {
        return familyOptions
            .map(function (family) {
                return '<option value="' + family.value + '">' + family.label + "</option>";
            })
            .join("");
    }

    function updateSummary() {
        var rows = rowsBody.querySelectorAll(".js-kit-composition-row");
        var total = 0;
        rows.forEach(function (row) {
            var quantityInput = row.querySelector(".js-kit-quantity");
            var quantity = quantityInput ? parseInt(quantityInput.value, 10) : 0;
            total += isNaN(quantity) ? 0 : quantity;
        });

        var typesValue = document.querySelector("#kit-form .stat-card-grid--two .stat-card:nth-child(1) .stat-card__value");
        var totalValue = document.querySelector("#kit-form .stat-card-grid--two .stat-card:nth-child(2) .stat-card__value");
        if (typesValue) {
            typesValue.textContent = String(rows.length);
        }
        if (totalValue) {
            totalValue.textContent = String(total);
        }
    }

    function addRow() {
        var removeLabel = rowsBody.getAttribute("data-remove-label") || "Remove";
        var row = document.createElement("tr");
        row.className = "js-kit-composition-row";
        row.innerHTML =
            '<td><div class="form-field__input-wrapper">' +
            '<select class="form-field__input form-field__select js-kit-instrument-family" name="instrument_family[]">' +
            buildOptionsMarkup() +
            "</select>" +
            '<span class="form-field__select-icon"></span>' +
            "</div></td>" +
            '<td><input class="data-table__input js-kit-quantity" type="number" min="0" step="1" name="expected_quantity[]" value="1"></td>' +
            '<td class="data-table__action-column"><button type="button" class="link-danger js-kit-remove-row"></button></td>';
        row.querySelector(".js-kit-remove-row").textContent = removeLabel;
        rowsBody.appendChild(row);
        updateSummary();
    }

    addButton.addEventListener("click", addRow);

    rowsBody.addEventListener("click", function (event) {
        var removeButton = event.target.closest(".js-kit-remove-row");
        if (!removeButton) {
            return;
        }
        var row = removeButton.closest(".js-kit-composition-row");
        if (row) {
            row.remove();
            updateSummary();
        }
    });

    rowsBody.addEventListener("input", function (event) {
        if (event.target.classList.contains("js-kit-quantity")) {
            updateSummary();
        }
    });
})();
