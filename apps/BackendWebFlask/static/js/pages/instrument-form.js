(function () {
  "use strict";

  function t(key, values) {
    return window.pefT ? window.pefT(key, values) : key;
  }

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
      lines.push(t("instrumentCreateLine", { code: "<strong>" + (code || t("instrumentNoCode")) + "</strong>" }));
      lines.push(t("instrumentFamilyLine", { value: selectedLabel(family) }));
      lines.push(t("instrumentCycleLine", { value: selectedLabel(cycle) }));
      lines.push(t("instrumentActiveLine", { value: selectedLabel(active) }));
    } else {
      var baseFamily = form.getAttribute("data-baseline-family") || "";
      var baseCycle = form.getAttribute("data-baseline-cycle") || "";
      var baseActive = form.getAttribute("data-baseline-active") || "";
      if (family && family.value !== baseFamily) {
        lines.push(t("instrumentFamilyChanged", { value: selectedLabel(family) }));
      }
      if (cycle && cycle.value !== baseCycle) {
        lines.push(t("instrumentCycleChanged", { before: baseCycle, after: selectedLabel(cycle) }));
      }
      if (active && active.value !== baseActive) {
        lines.push(t("instrumentActiveChanged", { before: baseActive, after: selectedLabel(active) }));
      }
      if (reason && reason.value.trim()) {
        lines.push(t("instrumentChangeReason", { value: reason.value.trim() }));
      }
      if (!lines.length) {
        lines.push(t("instrumentNoChanges"));
      } else {
        lines.unshift(t("instrumentHeading", { code: "<strong>" + code + "</strong>" }));
      }
    }

    var html = "<p>" + t("instrumentConfirmIntro") + "</p><div class=\"app-modal__audit\"><ul>";
    lines.forEach(function (line) {
      html += "<li>" + line + "</li>";
    });
    html += "</ul></div>";

    if (window.appConfirm) {
      window
        .appConfirm({
          title: mode === "edit" ? t("instrumentSaveTitle") : t("instrumentCreateTitle"),
          html: html,
          okLabel: mode === "edit" ? t("instrumentSaveChanges") : t("instrumentSaveButton"),
          cancelLabel: t("cancel"),
        })
        .then(function (ok) {
          if (ok && window.appConfirmProceed) window.appConfirmProceed(form);
        });
      return;
    }
    if (window.appConfirmProceed) window.appConfirmProceed(form);
  });
})();
