/*
  V3 step wizard — presentation only (Supervisor New Session, Supervisor Confirm Close).

  Markup:
    <div|form data-v3-wizard>
      <ol class="v3-stepper"> <li data-wizard-indicator="1">...</li> ... </ol>
      <section class="card v3-wizard-panel" data-wizard-step="1" [data-wizard-key="session"]>
        <h2 tabindex="-1">...</h2> ...
        <button type="button" data-wizard-back>   <button type="button" data-wizard-next>
      </section>
      ...
    </div|form>

  - Shows one panel at a time and updates the numbered stepper (current / complete).
  - Continue validates the fields of the current panel with native constraint validation
    (reportValidity) before moving on. Hidden panels keep their values: they are only hidden.
  - If a submit is blocked by an invalid field inside a hidden panel, that panel is shown so the
    browser can point at the field.
  - Enter inside a field of a non-final step acts as Continue instead of submitting the form.
  - Panels with data-wizard-key are reachable through location.hash (#key), e.g. after a link
    that reloads the page. Nothing is stored and nothing is submitted by this script.
*/
(function () {
    "use strict";

    function stepOf(panel) {
        return parseInt(panel.getAttribute("data-wizard-step"), 10);
    }

    function initWizard(root) {
        var panels = Array.prototype.slice.call(root.querySelectorAll("[data-wizard-step]"));
        var indicators = Array.prototype.slice.call(root.querySelectorAll("[data-wizard-indicator]"));
        if (!panels.length) {
            return;
        }
        var lastStep = panels.length;
        var hasKeys = panels.some(function (panel) { return panel.hasAttribute("data-wizard-key"); });

        function panelFor(step) {
            return panels.filter(function (panel) { return stepOf(panel) === step; })[0] || null;
        }

        function currentStep() {
            var visible = panels.filter(function (panel) { return !panel.hidden; })[0];
            return visible ? stepOf(visible) : 1;
        }

        function showStep(step, options) {
            options = options || {};
            panels.forEach(function (panel) {
                panel.hidden = stepOf(panel) !== step;
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
            var panel = panelFor(step);
            var key = panel && panel.getAttribute("data-wizard-key");
            if (options.updateHash && hasKeys && window.history && window.history.replaceState) {
                window.history.replaceState(null, "", key ? "#" + key : window.location.pathname + window.location.search);
            }
            if (options.focus) {
                var heading = panel && panel.querySelector("h2");
                if (heading) {
                    heading.focus();
                }
                var content = document.getElementById("main-content");
                if (content) {
                    content.scrollTop = 0;
                }
            }
        }

        function fieldsOf(panel) {
            return Array.prototype.slice.call(panel.querySelectorAll("input, select, textarea"))
                .filter(function (field) { return field.willValidate; });
        }

        function validateStep(step) {
            var panel = panelFor(step);
            if (!panel) {
                return true;
            }
            var invalid = fieldsOf(panel).filter(function (field) { return !field.checkValidity(); })[0];
            if (invalid) {
                invalid.reportValidity();
                return false;
            }
            return true;
        }

        function goNext() {
            var step = currentStep();
            if (step >= lastStep || !validateStep(step)) {
                return;
            }
            showStep(step + 1, { focus: true, updateHash: true });
        }

        root.addEventListener("click", function (event) {
            if (event.target.closest("[data-wizard-next]")) {
                goNext();
            } else if (event.target.closest("[data-wizard-back]")) {
                showStep(Math.max(currentStep() - 1, 1), { focus: true, updateHash: true });
            }
        });

        // Native validation on submit: reveal the step that holds the first invalid field.
        var revealing = false;
        root.addEventListener("invalid", function (event) {
            if (revealing) {
                return;
            }
            var panel = event.target.closest("[data-wizard-step]");
            if (panel && panel.hidden) {
                revealing = true;
                showStep(stepOf(panel), { updateHash: true });
                window.setTimeout(function () { revealing = false; }, 0);
            }
        }, true);

        root.addEventListener("keydown", function (event) {
            if (event.key !== "Enter" || event.defaultPrevented) {
                return;
            }
            var target = event.target;
            if (!target.matches || !target.matches("input") || target.matches("[type='checkbox'], [type='radio'], [type='submit'], [type='button']")) {
                return;
            }
            if (currentStep() < lastStep) {
                event.preventDefault();
                goNext();
            }
        });

        var hashKey = (window.location.hash || "").replace(/^#/, "");
        var fromHash = hashKey && panels.filter(function (panel) {
            return panel.getAttribute("data-wizard-key") === hashKey;
        })[0];
        var initial = fromHash || panels.filter(function (panel) { return !panel.hidden; })[0] || panels[0];
        showStep(stepOf(initial));
    }

    Array.prototype.forEach.call(document.querySelectorAll("[data-v3-wizard]"), initWizard);
})();
