(function () {
  "use strict";

  function t(key, values) {
    return window.pefT ? window.pefT(key, values) : key;
  }

  var form = document.querySelector("[data-inventory-edit-form]");
  var tbody = document.querySelector("[data-inventory-rows]");
  var addFamily = document.querySelector("[data-inventory-add-family]");
  var addQty = document.querySelector("[data-inventory-add-qty]");
  var addBtn = document.querySelector("[data-add-row]");
  var removedWrap = document.querySelector("[data-inventory-removed]");
  var removedBody = document.querySelector("[data-inventory-removed-rows]");
  var familyDataEl = document.getElementById("inventory-family-options");
  var baselineEl = document.getElementById("inventory-baseline");
  var physicianHost = document.querySelector("[data-physician-rows]");
  var physicianTemplate = document.getElementById("physician-row-template");
  var physicianDataEl = document.getElementById("physician-options-data");

  var families = [];
  try {
    families = JSON.parse(familyDataEl && familyDataEl.textContent ? familyDataEl.textContent : "[]") || [];
  } catch (_err) {
    families = [];
  }

  var baseline = {};
  try {
    var baselineList = JSON.parse(baselineEl && baselineEl.textContent ? baselineEl.textContent : "[]") || [];
    baselineList.forEach(function (item) {
      baseline[String(item.family_id)] = {
        label: item.family_name,
        qty: parseInt(item.expected_quantity, 10) || 0,
      };
    });
  } catch (_err) {
    baseline = {};
  }

  var removedItems = {};
  var physicianOptions = [];
  try {
    physicianOptions =
      JSON.parse(physicianDataEl && physicianDataEl.textContent ? physicianDataEl.textContent : "[]") || [];
  } catch (_err) {
    physicianOptions = [];
  }

  function findFamily(id) {
    for (var i = 0; i < families.length; i += 1) {
      if (String(families[i].id) === String(id)) return families[i];
    }
    return null;
  }

  function activeIds() {
    if (!tbody) return [];
    return Array.prototype.slice
      .call(tbody.querySelectorAll("[data-inventory-row]"))
      .map(function (row) {
        return String(row.getAttribute("data-family-id") || "");
      })
      .filter(Boolean);
  }

  function blockedIds() {
    return activeIds().concat(Object.keys(removedItems));
  }

  function refreshAddOptions() {
    if (!addFamily) return;
    var blocked = blockedIds();
    var current = addFamily.value;
    addFamily.innerHTML = '<option value="">' + t("selectFamily") + "</option>";
    families.forEach(function (family) {
      if (blocked.indexOf(String(family.id)) !== -1) return;
      var opt = document.createElement("option");
      opt.value = family.id;
      opt.textContent = family.name;
      addFamily.appendChild(opt);
    });
    if (current && blocked.indexOf(String(current)) === -1) addFamily.value = current;
    if (addBtn) addBtn.disabled = addFamily.options.length <= 1;
  }

  function markRowState(row) {
    row.classList.remove("data-table__row--outline-success");
    var origin = row.getAttribute("data-row-origin") || "baseline";
    var familyId = String(row.getAttribute("data-family-id") || "");
    var qtyInput = row.querySelector("[data-qty-input]");
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

  function renderCategoryHeaders() {
    if (!tbody) return;
    tbody.querySelectorAll("[data-category-header]").forEach(function (row) {
      row.remove();
    });
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("[data-inventory-row]"));
    rows.sort(function (a, b) {
      var ra = parseInt(a.getAttribute("data-category-rank") || "999", 10);
      var rb = parseInt(b.getAttribute("data-category-rank") || "999", 10);
      if (ra !== rb) return ra - rb;
      return String(a.getAttribute("data-family-label") || "").localeCompare(
        String(b.getAttribute("data-family-label") || "")
      );
    });
    var last = null;
    rows.forEach(function (row) {
      tbody.appendChild(row);
      var cat = row.getAttribute("data-category-label") || t("otherCategory");
      if (cat !== last) {
        last = cat;
        var header = document.createElement("tr");
        header.setAttribute("data-category-header", "1");
        header.innerHTML =
          '<td colspan="4"><strong class="inventory-type-heading">' + cat + "</strong></td>";
        tbody.insertBefore(header, row);
      }
      markRowState(row);
    });
  }

  function createRow(family, qty, origin) {
    var row = document.createElement("tr");
    row.setAttribute("data-inventory-row", "1");
    row.setAttribute("data-family-id", family.id);
    row.setAttribute("data-family-label", family.name);
    row.setAttribute("data-category-label", family.category_label || t("otherCategory"));
    row.setAttribute(
      "data-category-rank",
      String(family.category_rank != null ? family.category_rank : 999)
    );
    row.setAttribute("data-baseline-qty", origin === "baseline" ? String(qty) : "");
    row.setAttribute("data-row-origin", origin);
    row.innerHTML =
      "<td><strong>" +
      family.name +
      '</strong><input type="hidden" name="family_id[]" value="' +
      family.id +
      '" data-family-id-input></td>' +
      '<td class="data-table__numeric-column"><input class="form-field__input form-field__input--plain" type="number" min="1" name="expected_quantity[]" value="' +
      qty +
      '" required data-qty-input></td>' +
      "<td>" +
      (origin === "added" ? t("newRow") : "—") +
      '</td><td><button type="button" class="btn btn--secondary btn--small" data-remove-row>' +
      t("remove") +
      "</button></td>";
    return row;
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
        '</td><td><button type="button" class="btn btn--secondary btn--small" data-undo-id="' +
        id +
        '">' +
        t("undo") +
        "</button></td>";
      removedBody.appendChild(tr);
    });
  }

  function addFromStaging() {
    if (!tbody || !addFamily) return;
    var familyId = addFamily.value;
    var qty = parseInt(addQty && addQty.value, 10);
    if (!familyId || isNaN(qty) || qty < 1) return;
    if (blockedIds().indexOf(String(familyId)) !== -1) return;
    var family = findFamily(familyId);
    if (!family) return;
    tbody.appendChild(createRow(family, qty, "added"));
    addFamily.value = "";
    if (addQty) addQty.value = "1";
    refreshAddOptions();
    renderCategoryHeaders();
  }

  function removeRow(row) {
    var familyId = String(row.getAttribute("data-family-id") || "");
    var label = row.getAttribute("data-family-label") || familyId;
    var qtyInput = row.querySelector("[data-qty-input]");
    var qty = qtyInput ? parseInt(qtyInput.value, 10) || 1 : 1;
    var family = findFamily(familyId) || {
      id: familyId,
      name: label,
      category_label: row.getAttribute("data-category-label") || t("otherCategory"),
      category_rank: parseInt(row.getAttribute("data-category-rank") || "999", 10),
    };
    removedItems[familyId] = {
      label: family.name || label,
      qty: qty,
      family: family,
      origin: row.getAttribute("data-row-origin") || "baseline",
    };
    row.remove();
    renderRemoved();
    refreshAddOptions();
    renderCategoryHeaders();
  }

  function undoRemoved(familyId) {
    var item = removedItems[familyId];
    if (!item || !tbody) return;
    delete removedItems[familyId];
    var origin = item.origin === "baseline" ? "baseline" : "added";
    var row = createRow(item.family, item.qty, origin);
    if (origin === "baseline" && baseline[familyId]) {
      row.setAttribute("data-baseline-qty", String(baseline[familyId].qty));
    }
    tbody.appendChild(row);
    renderRemoved();
    refreshAddOptions();
    renderCategoryHeaders();
  }

  function buildChangeSummaryHtml() {
    var current = {};
    if (tbody) {
      tbody.querySelectorAll("[data-inventory-row]").forEach(function (row) {
        var id = String(row.getAttribute("data-family-id") || "");
        var qtyInput = row.querySelector("[data-qty-input]");
        current[id] = {
          label: row.getAttribute("data-family-label") || id,
          qty: qtyInput ? parseInt(qtyInput.value, 10) || 0 : 0,
        };
      });
    }
    var added = [];
    var modified = [];
    var removed = [];
    Object.keys(current).forEach(function (id) {
      if (!baseline[id]) added.push(current[id].label + " × " + current[id].qty);
      else if (baseline[id].qty !== current[id].qty) {
        modified.push(current[id].label + ": " + baseline[id].qty + " → " + current[id].qty);
      }
    });
    Object.keys(baseline).forEach(function (id) {
      if (!current[id]) removed.push(baseline[id].label + " × " + baseline[id].qty);
    });
    if (!added.length && !modified.length && !removed.length) {
      return "<p>" + t("inventoryNoChanges") + "</p>";
    }
    var html = "<p>" + t("inventoryReviewChanges") + "</p>";
    function block(title, lines) {
      if (!lines.length) return "";
      var out = '<div class="app-modal__audit"><p class="app-modal__audit-title">' + title + "</p><ul>";
      lines.forEach(function (line) {
        out += "<li>" + line + "</li>";
      });
      return out + "</ul></div>";
    }
    html += block(t("changesAdded"), added) + block(t("changesModified"), modified) + block(t("changesRemoved"), removed);
    return html;
  }

  if (addBtn) addBtn.addEventListener("click", addFromStaging);

  if (tbody) {
    tbody.addEventListener("click", function (event) {
      var remove = event.target.closest("[data-remove-row]");
      if (!remove) return;
      var row = remove.closest("[data-inventory-row]");
      if (row) removeRow(row);
    });
    tbody.addEventListener("input", function (event) {
      if (!event.target.matches("[data-qty-input]")) return;
      var row = event.target.closest("[data-inventory-row]");
      if (row) markRowState(row);
    });
  }

  if (removedBody) {
    removedBody.addEventListener("click", function (event) {
      var undo = event.target.closest("[data-undo-id]");
      if (!undo) return;
      undoRemoved(undo.getAttribute("data-undo-id"));
    });
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (window.appConfirm) {
        window
          .appConfirm({
            title: t("inventorySaveTitle"),
            html: buildChangeSummaryHtml(),
            okLabel: t("save"),
            cancelLabel: t("cancel"),
          })
          .then(function (ok) {
            if (ok && window.appConfirmProceed) window.appConfirmProceed(form);
          });
        return;
      }
      if (window.appConfirmProceed) window.appConfirmProceed(form);
    });
  }

  refreshAddOptions();
  renderCategoryHeaders();
  renderRemoved();

  function selectedPhysicianIds(exceptHidden) {
    if (!physicianHost) return new Set();
    return new Set(
      Array.prototype.slice
        .call(physicianHost.querySelectorAll("[data-physician-id]"))
        .filter(function (el) {
          return el !== exceptHidden;
        })
        .map(function (el) {
          return el.value;
        })
        .filter(Boolean)
    );
  }

  function findPhysician(id) {
    for (var i = 0; i < physicianOptions.length; i += 1) {
      if (String(physicianOptions[i].id) === String(id)) return physicianOptions[i];
    }
    return null;
  }

  function renderMenu(row) {
    var input = row.querySelector("[data-physician-typeahead]");
    var hidden = row.querySelector("[data-physician-id]");
    var menu = row.querySelector("[data-physician-menu]");
    if (!input || !hidden || !menu) return;
    var query = (input.value || "").trim().toLowerCase();
    var taken = selectedPhysicianIds(hidden);
    var matches = physicianOptions.filter(function (p) {
      if (taken.has(String(p.id))) return false;
      if (!query) return true;
      return String(p.name || "")
        .toLowerCase()
        .includes(query);
    });
    menu.innerHTML = "";
    matches.slice(0, 12).forEach(function (p, index) {
      var li = document.createElement("li");
      li.textContent = p.name;
      li.dataset.id = p.id;
      if (index === 0) li.classList.add("is-active");
      li.addEventListener("mousedown", function (event) {
        event.preventDefault();
        hidden.value = p.id;
        input.value = p.name;
        menu.hidden = true;
      });
      menu.appendChild(li);
    });
    menu.hidden = false;
  }

  function bindPhysicianRow(row) {
    var input = row.querySelector("[data-physician-typeahead]");
    var hidden = row.querySelector("[data-physician-id]");
    var menu = row.querySelector("[data-physician-menu]");
    if (!input || !hidden || !menu) return;
    input.addEventListener("focus", function () {
      renderMenu(row);
    });
    input.addEventListener("input", function () {
      hidden.value = "";
      renderMenu(row);
    });
    input.addEventListener("blur", function () {
      setTimeout(function () {
        menu.hidden = true;
        if (hidden.value) {
          var p = findPhysician(hidden.value);
          if (p) input.value = p.name;
        } else {
          input.value = "";
        }
      }, 120);
    });
    row.querySelector("[data-physician-remove]")?.addEventListener("click", function () {
      var rows = physicianHost.querySelectorAll("[data-physician-row]");
      if (rows.length <= 1) return;
      row.remove();
    });
  }

  function addPhysicianRow() {
    if (!physicianHost || !physicianTemplate) return;
    var node = physicianTemplate.content.firstElementChild.cloneNode(true);
    physicianHost.appendChild(node);
    bindPhysicianRow(node);
  }

  document.querySelector("[data-physician-add]")?.addEventListener("click", addPhysicianRow);
  if (physicianHost) {
    if (!physicianHost.children.length) addPhysicianRow();
    else physicianHost.querySelectorAll("[data-physician-row]").forEach(bindPhysicianRow);
  }
})();
