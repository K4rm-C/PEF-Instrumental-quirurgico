/*
  Operator V3 Assigned Session Confirmation — presentation-only behavior.

  - Three read-only steps inside one form: shows one step panel at a time and updates the
    numbered stepper (current / completed / remaining). Nothing is stored (no cookies, no
    localStorage); a reload returns to the server-rendered step.
  - Keeps "Start Session" disabled until the Instrument Readiness checkbox is checked. The
    server re-validates the checkbox on submit; this script never submits anything itself.
*/
(function () {
    "use strict";

    var form = document.querySelector("[data-v3-readiness-form]");
    if (!form) {
        return;
    }

    var check = form.querySelector("[data-v3-readiness-check]");
    var submit = form.querySelector("[data-v3-readiness-submit]");

    function syncReadiness() {
        if (check && submit) {
            submit.disabled = !check.checked;
        }
    }

    if (check) {
        check.addEventListener("change", syncReadiness);
    }
    syncReadiness();

    var panels = Array.prototype.slice.call(form.querySelectorAll("[data-confirm-step]"));
    var indicators = Array.prototype.slice.call(document.querySelectorAll("[data-confirm-indicator]"));
    if (!panels.length) {
        return;
    }

    function currentStep() {
        var visible = panels.filter(function (panel) { return !panel.hidden; })[0];
        return visible ? parseInt(visible.getAttribute("data-confirm-step"), 10) : 1;
    }

    function showStep(step) {
        panels.forEach(function (panel) {
            panel.hidden = parseInt(panel.getAttribute("data-confirm-step"), 10) !== step;
        });
        indicators.forEach(function (indicator) {
            var number = parseInt(indicator.getAttribute("data-confirm-indicator"), 10);
            indicator.classList.toggle("v3-stepper__step--current", number === step);
            indicator.classList.toggle("v3-stepper__step--complete", number < step);
            if (number === step) {
                indicator.setAttribute("aria-current", "step");
            } else {
                indicator.removeAttribute("aria-current");
            }
        });
        var heading = form.querySelector('[data-confirm-step="' + step + '"] h2');
        if (heading) {
            heading.focus();
        }
        var content = document.getElementById("main-content");
        if (content) {
            content.scrollTop = 0;
        }
    }

    form.addEventListener("click", function (event) {
        if (event.target.closest("[data-confirm-next]")) {
            showStep(Math.min(currentStep() + 1, panels.length));
        } else if (event.target.closest("[data-confirm-back]")) {
            showStep(Math.max(currentStep() - 1, 1));
        }
    });
})();
