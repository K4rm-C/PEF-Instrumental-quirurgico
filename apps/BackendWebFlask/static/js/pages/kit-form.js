/*
  Kit create/edit:
  - Staging pair (family + qty) + Add — only then enters composition
  - Green outline for added/modified rows
  - Removed section with Undo (still excluded from dropdown)
  - Save confirm overlay listing changes
*/
(function () {
  "use strict";

  function t(key, values) {
    return window.pefT ? window.pefT(key, values) : key;
  }

  var form = document.querySelector("[data-kit-form]");
  var rowsBody = document.getElementById("kit-composition-rows");
  var addButton = document.querySelector("[data-kit-add-btn]");
  var addFamily = document.querySelector("[data-kit-add-family]");
  var addQty = document.querySelector("[data-kit-add-qty]");
  var removedWrap = document.querySelector("[data-kit-removed]");
  var removedBody = document.querySelector("[data-kit-removed-rows]");
  var optionsScript = document.getElementById("kit-instrument-family-options");
  var orderScript = document.getElementById("kit-category-purpose-order");
  var baselineScript = document.getElementById("kit-baseline-composition");

  if (!form || !rowsBody || !addButton || !addFamily || !optionsScript) {
    return;
  }

  var familyOptions = [];
  try {
    familyOptions = JSON.parse(optionsScript.textContent) || [];
  } catch (_err) {
    familyOptions = [];
  }

  var purposeOrder = [
    "cutting",
    "dissection",
    "grasping",
    "hemostasis",
    "retraction",
    "suction",
    "suturing",
  ];
  if (orderScript) {
    try {
      var parsedOrder = JSON.parse(orderScript.textContent);
      if (Array.isArray(parsedOrder) && parsedOrder.length) {
        purposeOrder = parsedOrder;
      }
    } catch (_err) {
      /* keep default */
    }
  }

  var baseline = {};
  try {
    var baselineList = JSON.parse(baselineScript && baselineScript.textContent ? baselineScript.textContent : "[]") || [];
    baselineList.forEach(function (item) {
      baseline[String(item.instrument_family_value)] = {
        label: item.instrument_family_label,
        qty: parseInt(item.expected_quantity, 10) || 0,
      };
    });
  } catch (_err) {
    baseline = {};
  }

  var removedItems = {};
  var removeLabel = rowsBody.getAttribute("data-remove-label") || t("remove");
  var undoLabel = rowsBody.getAttribute("data-undo-label") || t("undo");
  var activeAvailableTemplate =
    rowsBody.getAttribute("data-active-available-template") ||
    "%(count)s active available of family";

  function purposeRank(code) {
    var index = purposeOrder.indexOf(code || "");
    return index === -1 ? purposeOrder.length : index;
  }

  function formatActiveAvailable(count) {
    return activeAvailableTemplate.replace("%(count)s", String(count == null ? 0 : count));
  }

  function findFamily(value) {
    for (var i = 0; i < familyOptions.length; i += 1) {
      if (String(familyOptions[i].value) === String(value)) {
        return familyOptions[i];
      }
    }
    return null;
  }

  function activeFamilyIds() {
    return Array.prototype.slice
      .call(rowsBody.querySelectorAll(".js-kit-composition-row"))
      .map(function (row) {
        return String(row.getAttribute("data-family-id") || "");
      })
      .filter(Boolean);
  }

  function blockedFamilyIds() {
    return activeFamilyIds().concat(Object.keys(removedItems));
  }

  function refreshAddOptions() {
    var blocked = blockedFamilyIds();
    var current = addFamily.value;
    addFamily.innerHTML = '<option value="">' + t("selectFamily") + "</option>";
    familyOptions.forEach(function (family) {
      if (blocked.indexOf(String(family.value)) !== -1) {
        return;
      }
      var opt = document.createElement("option");
      opt.value = family.value;
      opt.textContent = family.label;
      addFamily.appendChild(opt);
    });
    if (current && blocked.indexOf(String(current)) === -1) {
      addFamily.value = current;
    }
    addButton.disabled = addFamily.options.length <= 1;
  }

  function markRowState(row) {
    row.classList.remove("data-table__row--outline-success");
    var origin = row.getAttribute("data-row-origin") || "baseline";
    var familyId = String(row.getAttribute("data-family-id") || "");
    var qtyInput = row.querySelector(".js-kit-quantity");
    var qty = qtyInput ? parseInt(qtyInput.value, 10) || 0 : 0;
    if (origin === "added") {
      row.classList.add("data-table__row--outline-success");
      return;
    }
    var base = baseline[familyId];
    if (base && qty !== base.qty) {
      row.classList.add("data-table__row--outline-success");
    }
  }

  function refreshRowAvailability(row) {
    var cell = row.querySelector(".js-kit-active-available");
    if (!cell) return;
    var family = findFamily(row.getAttribute("data-family-id"));
    cell.textContent = formatActiveAvailable(family ? family.active_available_count || 0 : 0);
  }

  function categoryLabelForRow(row) {
    var family = findFamily(row.getAttribute("data-family-id"));
    if (family && family.category_label) return family.category_label;
    return row.getAttribute("data-category-code") || t("otherCategory");
  }

  function sortRowsByPurpose() {
    rowsBody.querySelectorAll(".js-kit-category-header").forEach(function (header) {
      header.remove();
    });
    var rows = Array.prototype.slice.call(rowsBody.querySelectorAll(".js-kit-composition-row"));
    rows.sort(function (a, b) {
      var rankA = parseInt(a.getAttribute("data-category-rank") || "999", 10);
      var rankB = parseInt(b.getAttribute("data-category-rank") || "999", 10);
      if (rankA !== rankB) return rankA - rankB;
      return String(a.getAttribute("data-family-label") || "").localeCompare(
        String(b.getAttribute("data-family-label") || "")
      );
    });
    var lastRank = null;
    rows.forEach(function (row) {
      var rank = parseInt(row.getAttribute("data-category-rank") || "999", 10);
      if (rank !== lastRank) {
        lastRank = rank;
        var header = document.createElement("tr");
        header.className = "js-kit-category-header";
        header.innerHTML = "<td colspan=\"4\"><strong>" + categoryLabelForRow(row) + "</strong></td>";
        rowsBody.appendChild(header);
      }
      rowsBody.appendChild(row);
      markRowState(row);
      refreshRowAvailability(row);
    });
  }

  function updateSummary() {
    var rows = rowsBody.querySelectorAll(".js-kit-composition-row");
    var total = 0;
    rows.forEach(function (row) {
      var quantityInput = row.querySelector(".js-kit-quantity");
      var quantity = quantityInput ? parseInt(quantityInput.value, 10) : 0;
      total += isNaN(quantity) ? 0 : quantity;
    });
    var typesValue = document.querySelector(
      "#kit-form .stat-card-grid--two .stat-card:nth-child(1) .stat-card__value"
    );
    var totalValue = document.querySelector(
      "#kit-form .stat-card-grid--two .stat-card:nth-child(2) .stat-card__value"
    );
    if (typesValue) typesValue.textContent = String(rows.length);
    if (totalValue) totalValue.textContent = String(total);
  }

  function renderRemoved() {
    if (!removedBody || !removedWrap) return;
    removedBody.innerHTML = "";
    var keys = Object.keys(removedItems);
    if (!keys.length) {
      removedWrap.hidden = true;
      return;
    }
    removedWrap.hidden = false;
    keys.forEach(function (id) {
      var item = removedItems[id];
      var tr = document.createElement("tr");
      tr.className = "data-table__row--outline-danger";
      tr.innerHTML =
        "<td>" +
        item.label +
        '</td><td class="data-table__numeric-column">' +
        item.qty +
        '</td><td class="data-table__action-column">' +
        '<button type="button" class="link-action js-kit-undo-row" data-undo-id="' +
        id +
        '">' +
        undoLabel +
        "</button></td>";
      removedBody.appendChild(tr);
    });
  }

  function createCompositionRow(family, qty, origin) {
    var row = document.createElement("tr");
    row.className = "js-kit-composition-row";
    row.setAttribute("data-family-id", family.value);
    row.setAttribute("data-family-label", family.label);
    row.setAttribute("data-baseline-qty", origin === "baseline" ? String(qty) : "");
    row.setAttribute("data-row-origin", origin);
    row.setAttribute("data-category-code", family.category_code || "");
    row.setAttribute(
      "data-category-rank",
      String(family.category_rank != null ? family.category_rank : purposeRank(family.category_code))
    );
    row.innerHTML =
      "<td><strong>" +
      family.label +
      '</strong><input type="hidden" name="instrument_family[]" value="' +
      family.value +
      '" class="js-kit-instrument-family"></td>' +
      '<td><input class="data-table__input js-kit-quantity" type="number" min="1" max="32767" step="1" name="expected_quantity[]" value="' +
      qty +
      '"></td>' +
      '<td class="js-kit-active-available"></td>' +
      '<td class="data-table__action-column"><button type="button" class="link-danger js-kit-remove-row">' +
      removeLabel +
      "</button></td>";
    return row;
  }

  function addFromStaging() {
    var familyId = addFamily.value;
    var qty = parseInt(addQty.value, 10);
    if (!familyId || isNaN(qty) || qty < 1) {
      return;
    }
    if (blockedFamilyIds().indexOf(String(familyId)) !== -1) {
      return;
    }
    var family = findFamily(familyId);
    if (!family) return;
    rowsBody.appendChild(createCompositionRow(family, qty, "added"));
    addFamily.value = "";
    addQty.value = "1";
    refreshAddOptions();
    sortRowsByPurpose();
    updateSummary();
  }

  function removeRow(row) {
    var familyId = String(row.getAttribute("data-family-id") || "");
    var label = row.getAttribute("data-family-label") || familyId;
    var qtyInput = row.querySelector(".js-kit-quantity");
    var qty = qtyInput ? parseInt(qtyInput.value, 10) || 1 : 1;
    var family = findFamily(familyId) || {
      value: familyId,
      label: label,
      category_code: row.getAttribute("data-category-code") || "",
      category_rank: parseInt(row.getAttribute("data-category-rank") || "999", 10),
    };
    removedItems[familyId] = {
      label: family.label || label,
      qty: qty,
      family: family,
      origin: row.getAttribute("data-row-origin") || "baseline",
    };
    row.remove();
    renderRemoved();
    refreshAddOptions();
    sortRowsByPurpose();
    updateSummary();
  }

  function undoRemoved(familyId) {
    var item = removedItems[familyId];
    if (!item) return;
    delete removedItems[familyId];
    var origin = item.origin === "baseline" ? "baseline" : "added";
    var row = createCompositionRow(item.family, item.qty, origin);
    if (origin === "baseline" && baseline[familyId]) {
      row.setAttribute("data-baseline-qty", String(baseline[familyId].qty));
    }
    rowsBody.appendChild(row);
    renderRemoved();
    refreshAddOptions();
    sortRowsByPurpose();
    updateSummary();
  }

  function buildChangeSummaryHtml() {
    var current = {};
    rowsBody.querySelectorAll(".js-kit-composition-row").forEach(function (row) {
      var id = String(row.getAttribute("data-family-id") || "");
      var qtyInput = row.querySelector(".js-kit-quantity");
      current[id] = {
        label: row.getAttribute("data-family-label") || id,
        qty: qtyInput ? parseInt(qtyInput.value, 10) || 0 : 0,
      };
    });

    var added = [];
    var modified = [];
    var removed = [];
    Object.keys(current).forEach(function (id) {
      if (!baseline[id]) {
        added.push(current[id].label + " × " + current[id].qty);
      } else if (baseline[id].qty !== current[id].qty) {
        modified.push(
          current[id].label + ": " + baseline[id].qty + " → " + current[id].qty
        );
      }
    });
    Object.keys(baseline).forEach(function (id) {
      if (!current[id]) {
        removed.push(baseline[id].label + " × " + baseline[id].qty);
      }
    });

    var nameInput = form.querySelector("[data-kit-name]");
    var statusSelect = form.querySelector("[data-kit-status]");
    var meta = [];
    if (nameInput && nameInput.defaultValue !== nameInput.value) {
      meta.push(t("kitNameChange", { before: nameInput.defaultValue, after: nameInput.value }));
    }
    if (statusSelect) {
      var baselineStatus = statusSelect.getAttribute("data-baseline-status") || statusSelect.defaultValue;
      if (baselineStatus !== statusSelect.value) {
        meta.push(t("kitStatusChange", { before: baselineStatus, after: statusSelect.value }));
      }
    }

    if (!added.length && !modified.length && !removed.length && !meta.length) {
      return "<p>" + t("kitNoChanges") + "</p>";
    }

    var html = "<p>" + t("kitReviewChanges") + "</p>";
    if (meta.length) {
      html += '<div class="app-modal__audit"><p class="app-modal__audit-title">' + t("kitSectionTitle") + "</p><ul>";
      meta.forEach(function (line) {
        html += "<li>" + line + "</li>";
      });
      html += "</ul></div>";
    }
    if (added.length) {
      html += '<div class="app-modal__audit"><p class="app-modal__audit-title">' + t("changesAdded") + "</p><ul>";
      added.forEach(function (line) {
        html += "<li>" + line + "</li>";
      });
      html += "</ul></div>";
    }
    if (modified.length) {
      html += '<div class="app-modal__audit"><p class="app-modal__audit-title">' + t("changesModified") + "</p><ul>";
      modified.forEach(function (line) {
        html += "<li>" + line + "</li>";
      });
      html += "</ul></div>";
    }
    if (removed.length) {
      html += '<div class="app-modal__audit"><p class="app-modal__audit-title">' + t("changesRemoved") + "</p><ul>";
      removed.forEach(function (line) {
        html += "<li>" + line + "</li>";
      });
      html += "</ul></div>";
    }
    return html;
  }

  addButton.addEventListener("click", addFromStaging);

  rowsBody.addEventListener("click", function (event) {
    var removeButton = event.target.closest(".js-kit-remove-row");
    if (!removeButton) return;
    var row = removeButton.closest(".js-kit-composition-row");
    if (row) removeRow(row);
  });

  rowsBody.addEventListener("input", function (event) {
    if (!event.target.classList.contains("js-kit-quantity")) return;
    var row = event.target.closest(".js-kit-composition-row");
    if (row) markRowState(row);
    updateSummary();
  });

  if (removedBody) {
    removedBody.addEventListener("click", function (event) {
      var undoBtn = event.target.closest(".js-kit-undo-row");
      if (!undoBtn) return;
      undoRemoved(undoBtn.getAttribute("data-undo-id"));
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var html = buildChangeSummaryHtml();
    if (window.appConfirm) {
      window
        .appConfirm({
          title: t("kitSaveTitle"),
          html: html,
          okLabel: t("kitSaveButton"),
          cancelLabel: t("cancel"),
        })
        .then(function (ok) {
          if (ok && window.appConfirmProceed) window.appConfirmProceed(form);
        });
      return;
    }
    if (window.appConfirmProceed) window.appConfirmProceed(form);
  });

  refreshAddOptions();
  sortRowsByPurpose();
  updateSummary();
  renderRemoved();
})();
