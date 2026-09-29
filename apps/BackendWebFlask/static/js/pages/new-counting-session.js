/*
  New Counting Session — the server computes which kits, capture stations and counting phases
  are valid for the selected operation (and the expected-inventory preview for the selected
  kit). Changing the Operation or Kit reloads the page with the current selection as query
  parameters so those server-side lists refresh; the POST is validated again server-side.
*/
(function () {
    "use strict";

    function reloadWithSelection(form, changedName) {
        var params = new URLSearchParams();
        ["operation_id", "kit_id", "capture_station_id", "counting_phase"].forEach(function (name) {
            var field = form.elements[name];
            // a new operation invalidates the dependent selections
            if (field && field.value && (changedName !== "operation_id" || name === "operation_id")) {
                params.set(name, field.value);
            }
        });
        window.location.search = params.toString();
    }

    document.addEventListener("DOMContentLoaded", function () {
        var form = document.getElementById("new-counting-session-form");
        if (!form) {
            return;
        }
        ["operation_id", "kit_id"].forEach(function (name) {
            var select = form.elements[name];
            if (select) {
                select.addEventListener("change", function () {
                    reloadWithSelection(form, name);
                });
            }
        });
    });
})();
