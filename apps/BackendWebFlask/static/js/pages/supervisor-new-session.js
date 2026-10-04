/*
  Supervisor V3 New Counting Session wizard — presentation-only behavior.

  - Shows one of the three step panels of the single wizard <form> (values in hidden
    panels are kept because they stay in the same form), validating the current panel's
    fields with native constraint validation before moving forward.
  - Keeps ?step=N in the address bar (history.replaceState) so a reload reopens the step.
  - Adds / removes Surgical Team rows from the server-rendered <template>.
  - Refreshes the Step 3 review summary from the current field values.

  No API calls and no persistence: submitting posts the form to the server, which handles
  the demo redirect. The server remains the source of truth.
*/
(function () {
    "use strict";

    var form = document.querySelector("[data-v3-wizard]");
    if (!form) {
        return;
    }

    var panels = Array.prototype.slice.call(form.querySelectorAll("[data-wizard-step]"));
    var indicators = Array.prototype.slice.call(document.querySelectorAll("[data-wizard-indicator]"));
    var teamRows = form.querySelector("[data-team-rows]");
    var teamTemplate = form.querySelector("[data-team-row-template]");

    function currentStep() {
        var visible = panels.filter(function (panel) { return !panel.hidden; })[0];
        return visible ? parseInt(visible.getAttribute("data-wizard-step"), 10) : 1;
    }

    function panelFor(step) {
        return form.querySelector('[data-wizard-step="' + step + '"]');
    }

    function panelIsValid(step) {
        var fields = panelFor(step).querySelectorAll("input, select, textarea");
        for (var index = 0; index < fields.length; index += 1) {
            if (!fields[index].checkValidity()) {
                fields[index].reportValidity();
                return false;
            }
        }
        return true;
    }

    function fieldText(name) {
        var field = form.elements[name];
        if (!field) {
            return "";
        }
        if (field.tagName === "SELECT") {
            var option = field.options[field.selectedIndex];
            return option ? option.text : "";
        }
        return field.value;
    }

    function formatDateTime(value) {
        // datetime-local "YYYY-MM-DDTHH:MM" -> "YYYY-MM-DD HH:MM:00"
        if (!value) {
            return "";
        }
        var parts = value.split("T");
        var time = parts[1] || "";
        return parts[0] + (time ? " " + (time.length === 5 ? time + ":00" : time) : "");
    }

    function updateSummary() {
        form.querySelectorAll("[data-summary-for]").forEach(function (target) {
            var name = target.getAttribute("data-summary-for");
            var field = form.elements[name];
            var value;
            if (name === "scheduled_at") {
                value = formatDateTime(field ? field.value : "");
            } else if (target.hasAttribute("data-summary-raw") && field) {
                value = field.value;
            } else {
                value = fieldText(name);
            }
            target.textContent = value || "—";
        });
        var team = form.querySelector("[data-summary-team]");
        if (team && teamRows) {
            var count = teamRows.querySelectorAll("[data-team-row]").length;
            team.textContent = team.getAttribute("data-summary-team-label").replace("__COUNT__", count);
        }
    }

    function showStep(step) {
        panels.forEach(function (panel) {
            panel.hidden = parseInt(panel.getAttribute("data-wizard-step"), 10) !== step;
        });
        indicators.forEach(function (indicator) {
            var number = parseInt(indicator.getAttribute("data-wizard-indicator"), 10);
            indicator.classList.toggle("v3-stepper__step--current", number === step);
            indicator.classList.toggle("v3-stepper__step--complete", number < step);
            if (number === step) {
                indicator.setAttribute("aria-current", "step");
            } else {
                indicator.removeAttribute("aria-current");
            }
        });
        if (step === 3) {
            updateSummary();
        }
        if (window.history && window.history.replaceState) {
            var url = new URL(window.location.href);
            url.searchParams.set("step", String(step));
            window.history.replaceState(null, "", url.toString());
        }
        var heading = panelFor(step).querySelector("h2");
        if (heading) {
            heading.focus();
        }
    }

    form.addEventListener("click", function (event) {
        var next = event.target.closest("[data-wizard-next]");
        if (next) {
            var step = currentStep();
            if (panelIsValid(step)) {
                showStep(Math.min(step + 1, panels.length));
            }
            return;
        }

        var back = event.target.closest("[data-wizard-back]");
        if (back) {
            showStep(Math.max(currentStep() - 1, 1));
            return;
        }

        var remove = event.target.closest("[data-team-remove]");
        if (remove) {
            var row = remove.closest("[data-team-row]");
            if (row) {
                row.remove();
            }
            return;
        }

        if (event.target.closest("[data-team-add]") && teamTemplate && teamRows) {
            var fragment = teamTemplate.content.cloneNode(true);
            teamRows.appendChild(fragment);
            var rows = teamRows.querySelectorAll("[data-team-row]");
            var input = rows[rows.length - 1].querySelector("input");
            if (input) {
                input.focus();
            }
        }
    });

    form.addEventListener("submit", function (event) {
        // Fields in hidden panels skip native validation feedback, so check every panel and
        // open the first one that still needs attention.
        for (var step = 1; step <= panels.length; step += 1) {
            var fields = panelFor(step).querySelectorAll("input, select, textarea");
            for (var index = 0; index < fields.length; index += 1) {
                if (!fields[index].checkValidity()) {
                    event.preventDefault();
                    showStep(step);
                    fields[index].reportValidity();
                    return;
                }
            }
        }
    });

    updateSummary();
})();
