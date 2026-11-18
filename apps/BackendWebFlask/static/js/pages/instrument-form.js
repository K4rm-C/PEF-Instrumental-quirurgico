(function () {
  "use strict";

  function t(key, values) {
    return window.pefT ? window.pefT(key, values) : key;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  var form = document.querySelector("[data-instrument-form]");
  if (!form) return;

  var familySelect = form.querySelector("[data-instrument-family]");
  var examplesHost = document.querySelector("[data-family-examples-host]");

  function selectedLabel(select) {
    if (!select || !select.selectedOptions || !select.selectedOptions[0]) return "";
    return select.selectedOptions[0].textContent || select.value || "";
  }

  function clearOverlays(host) {
    host.querySelectorAll(".family-examples__overlay").forEach(function (node) {
      node.remove();
    });
  }

  function renderFamilyExamples(examples) {
    if (!examplesHost) return;
    var list = examplesHost.querySelector("[data-family-examples-list]");
    if (!list) return;

    var emptyLabel = examplesHost.getAttribute("data-empty-label") || "";
    var viewLabel = examplesHost.getAttribute("data-view-label") || "";
    var closeLabel = examplesHost.getAttribute("data-close-label") || "";
    var items = Array.isArray(examples) ? examples.filter(function (item) {
      return item && item.image_url;
    }) : [];

    clearOverlays(examplesHost);
    list.innerHTML = "";

    if (!items.length) {
      var empty = document.createElement("li");
      empty.className = "family-examples__empty text-muted";
      empty.setAttribute("data-family-examples-empty", "");
      empty.textContent = emptyLabel;
      list.appendChild(empty);
      if (window.location.hash && window.location.hash.indexOf("family-example-view-") === 1) {
        history.replaceState(null, "", window.location.pathname + window.location.search + "#family-examples");
      }
      return;
    }

    items.forEach(function (example) {
      var id = String(example.id || "");
      var imageUrl = String(example.image_url || "");
      var text = example.example_text ? String(example.example_text) : "";
      var alt = text || viewLabel;

      var li = document.createElement("li");
      li.className = "family-examples__item";
      li.innerHTML =
        '<a class="family-examples__thumb" href="#family-example-view-' + escapeHtml(id) + '" title="' + escapeHtml(viewLabel) + '">' +
        '<img src="' + escapeHtml(imageUrl) + '" alt="' + escapeHtml(alt) + '" loading="lazy">' +
        "</a>" +
        (text
          ? '<div class="family-examples__meta"><span class="family-examples__text">' + escapeHtml(text) + "</span></div>"
          : "");
      list.appendChild(li);

      var overlay = document.createElement("div");
      overlay.className = "family-examples__overlay";
      overlay.id = "family-example-view-" + id;
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.setAttribute("aria-label", viewLabel);
      overlay.innerHTML =
        '<div class="family-examples__overlay-panel">' +
        '<img src="' + escapeHtml(imageUrl) + '" alt="' + escapeHtml(alt) + '">' +
        (text ? '<p class="family-examples__overlay-caption">' + escapeHtml(text) + "</p>" : "") +
        '<a class="btn btn--secondary" href="#family-examples">' + escapeHtml(closeLabel) + "</a>" +
        "</div>";
      examplesHost.appendChild(overlay);
    });
  }

  function loadFamilyExamples(familyId) {
    if (!examplesHost) return;
    var template = examplesHost.getAttribute("data-examples-url-template") || "";
    if (!template || !familyId) {
      renderFamilyExamples([]);
      return;
    }
    var url = template.replace("{family_id}", encodeURIComponent(familyId));
    fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (response) {
        if (!response.ok) throw new Error("preview_failed");
        return response.json();
      })
      .then(function (payload) {
        renderFamilyExamples((payload && payload.examples) || []);
      })
      .catch(function () {
        renderFamilyExamples([]);
      });
  }

  if (familySelect && examplesHost) {
    familySelect.addEventListener("change", function () {
      loadFamilyExamples(familySelect.value || "");
    });
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
