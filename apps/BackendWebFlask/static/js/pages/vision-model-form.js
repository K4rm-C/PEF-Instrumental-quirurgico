/*
  IT Administrator Vision Model form (New Vision Model / Edit Vision Model) — presentation-
  only Model Classes row insertion ("+ Add Class Mapping"), mirroring
  static/js/pages/kit-form.js's Kit Composition editor. Nothing here is persisted, submitted,
  or validated against real catalog data.
*/
(function () {
    "use strict";

    var rowsBody = document.getElementById("model-classes-rows");
    var addButton = document.getElementById("add-class-mapping-btn");
    var optionsScript = document.getElementById("model-class-family-options");

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

    addButton.addEventListener("click", function () {
        var removeLabel = rowsBody.getAttribute("data-remove-label") || "Remove";
        var row = document.createElement("tr");
        row.className = "js-model-class-row";
        row.innerHTML =
            '<td><input class="data-table__input" type="number" min="0" step="1" name="yolo_class_id[]" value="0"></td>' +
            '<td><div class="form-field__input-wrapper">' +
            '<select class="form-field__input form-field__select" name="instrument_family[]">' +
            buildOptionsMarkup() +
            "</select>" +
            '<span class="form-field__select-icon"></span>' +
            "</div></td>" +
            '<td class="data-table__action-column"><button type="button" class="link-danger js-model-class-remove-row"></button></td>';
        row.querySelector(".js-model-class-remove-row").textContent = removeLabel;
        rowsBody.appendChild(row);
    });

    rowsBody.addEventListener("click", function (event) {
        var removeButton = event.target.closest(".js-model-class-remove-row");
        if (!removeButton) {
            return;
        }
        var row = removeButton.closest(".js-model-class-row");
        if (row) {
            row.remove();
        }
    });
})();
