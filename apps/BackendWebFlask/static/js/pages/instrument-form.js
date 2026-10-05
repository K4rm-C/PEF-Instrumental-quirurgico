(function () {
  "use strict";

  var form = document.querySelector("[data-instrument-form]");
  if (!form) return;

  function selectedLabel(select) {
    if (!select || !select.selectedOptions || !select.selectedOptions[0]) return "";
    return select.selectedOptions[0].textContent || select.value || "";
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();

    var mode = form.getAttribute("data-form-mode") || "create";
    var family = form.querySelector("[data-instrument-family]");
    var cycle = form.querySelector("[data-instrument-cycle]");
    var active = form.querySelector("[data-instrument-active]");
    var reason = form.querySelector("[data-instrument-reason]");
    var codeEl = form.querySelector("[data-instrument-code]");
    var code = codeEl ? codeEl.value : "";

    var lines = [];
    if (mode === "create") {
      lines.push("Create instrument <strong>" + (code || "(no code)") + "</strong>");
      lines.push("Family: " + selectedLabel(family));
      lines.push("Cycle status: " + selectedLabel(cycle));
      lines.push("Active status: " + selectedLabel(active));
    } else {
      var baseFamily = form.getAttribute("data-baseline-family") || "";
      var baseCycle = form.getAttribute("data-baseline-cycle") || "";
      var baseActive = form.getAttribute("data-baseline-active") || "";
      if (family && family.value !== baseFamily) {
        lines.push("Family changed → " + selectedLabel(family));
      }
      if (cycle && cycle.value !== baseCycle) {
        lines.push("Cycle status: " + baseCycle + " → " + selectedLabel(cycle));
      }
      if (active && active.value !== baseActive) {
        lines.push("Active status: " + baseActive + " → " + selectedLabel(active));
      }
      if (reason && reason.value.trim()) {
        lines.push("Change reason: " + reason.value.trim());
      }
      if (!lines.length) {
        lines.push("No status/family changes detected. Save anyway?");
      } else {
        lines.unshift("Instrument <strong>" + code + "</strong>");
      }
    }

    var html = "<p>Confirm this instrument save. The change will be audited.</p><div class=\"app-modal__audit\"><ul>";
    lines.forEach(function (line) {
      html += "<li>" + line + "</li>";
    });
    html += "</ul></div>";

    if (window.appConfirm) {
      window
        .appConfirm({
          title: mode === "edit" ? "Save instrument changes?" : "Create instrument?",
          html: html,
          okLabel: mode === "edit" ? "Save Changes" : "Save Instrument",
          cancelLabel: "Cancel",
        })
        .then(function (ok) {
          if (ok && window.appConfirmProceed) window.appConfirmProceed(form);
        });
      return;
    }
    if (window.appConfirmProceed) window.appConfirmProceed(form);
  });
})();
