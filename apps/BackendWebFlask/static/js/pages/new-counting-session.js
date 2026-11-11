/*
  New Counting Session — presentation-only behavior.

  When the Operator changes the selected Operation / Procedure, the read-only Patient,
  Physician / Surgeon, and Operating Room fields are updated from data-* attributes already
  rendered by the server on each <option>. No business logic, API calls, or database access
  happen here — the server is the source of truth for this data; this script only reflects
  the already-rendered attributes of whichever option becomes selected.
*/
(function () {
    "use strict";

    function updateOperationDependentFields(operationSelect) {
        var selectedOption = operationSelect.options[operationSelect.selectedIndex];
        if (!selectedOption) {
            return;
        }

        var patientField = document.getElementById("session-setup-patient");
        var physicianField = document.getElementById("session-setup-physician");
        var operatingRoomField = document.getElementById("session-setup-operating-room");

        if (patientField) {
            patientField.value = selectedOption.getAttribute("data-patient-name") || "";
        }
        if (physicianField) {
            physicianField.value = selectedOption.getAttribute("data-physician-name") || "";
        }
        if (operatingRoomField) {
            operatingRoomField.value = selectedOption.getAttribute("data-operating-room-name") || "";
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        var operationSelect = document.getElementById("operation-select");
        if (!operationSelect) {
            return;
        }
        operationSelect.addEventListener("change", function () {
            updateOperationDependentFields(operationSelect);
        });
    });
})();
