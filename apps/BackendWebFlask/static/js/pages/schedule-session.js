(() => {
  const form = document.querySelector("[data-schedule-form]");
  if (!form) return;
  const t = (key) => (window.pefT ? window.pefT(key) : key);

  const stockUrl = form.dataset.stockRecheckUrl || "";
  const kitLinesUrl = form.dataset.kitLinesUrl || "";
  const selectKitLabel = form.dataset.selectKitLabel || "Select kit";
  const selectKitEmpty = form.dataset.selectKitEmpty || "Select a kit to load composition.";
  const otherLabel = form.dataset.otherLabel || "Other";
  const tbody = form.querySelector("[data-stock-tbody]");
  const privacyModal = document.getElementById("privacy-continue-modal");
  const noticeSelect = document.getElementById("privacy-notice-version");
  const confirmCb = document.getElementById("privacy-confirm-checkbox");
  const modelCb = document.getElementById("purpose-model-improvement");
  const hiddenNotice = document.getElementById("privacy-notice-version-hidden");
  const hiddenConfirm = document.getElementById("confirm-privacy-hidden");
  const hiddenConfirmCb = document.getElementById("privacy-confirm-checkbox-hidden");
  const hiddenModel = document.getElementById("purpose-model-improvement-hidden");
  const physicianHost = document.querySelector("[data-physician-rows]");
  const physicianTemplate = document.getElementById("physician-row-template");
  const physicianDataEl = document.getElementById("physician-options-data");
  const procedureKitsEl = document.getElementById("procedure-kits-data");
  const procedureSelect = form.querySelector("[data-procedure-select]");
  const kitSelect = form.querySelector("[data-kit-select]");
  let physicianOptions = [];
  let procedureKits = {};
  try {
    physicianOptions = JSON.parse(physicianDataEl?.textContent || "[]") || [];
  } catch (_err) {
    physicianOptions = [];
  }
  try {
    procedureKits = JSON.parse(procedureKitsEl?.textContent || "{}") || {};
  } catch (_err) {
    procedureKits = {};
  }

  function applySemaphore(badge, value) {
    if (!badge) return;
    const sem = String(value || "OK").toUpperCase();
    badge.textContent = sem;
    badge.className =
      "semaphore-badge semaphore-badge--" +
      (sem === "OK" ? "ok" : sem === "WARN" ? "warn" : "block");
  }

  function bindQtyInputs(root) {
    (root || form).querySelectorAll("[data-qty-input]").forEach((input) => {
      input.addEventListener("change", recheckStock);
      input.addEventListener("blur", recheckStock);
    });
  }

  async function recheckStock() {
    if (!stockUrl || !tbody) return;
    const familyIds = [...form.querySelectorAll('input[name="family_id[]"]')].map((el) => el.value);
    const qtys = [...form.querySelectorAll('input[name="expected_quantity[]"]')].map((el) => el.value);
    const body = new URLSearchParams();
    familyIds.forEach((id) => body.append("family_id[]", id));
    qtys.forEach((q) => body.append("expected_quantity[]", q));
    try {
      const res = await fetch(stockUrl, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
        body: body.toString(),
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      const byId = {};
      (data.lines || []).forEach((line) => {
        byId[String(line.family_id)] = line;
      });
      tbody.querySelectorAll("[data-stock-row]").forEach((row) => {
        const line = byId[row.dataset.familyId];
        if (!line) return;
        const label = row.querySelector("[data-stock-label]");
        if (label) label.textContent = `${line.stock_available} / ${line.stock_total}`;
        applySemaphore(row.querySelector("[data-semaphore-badge]"), line.semaphore);
      });
    } catch (_err) {
      /* ignore transient recheck errors */
    }
  }

  function kitsForProcedure(procedureId) {
    if (!procedureId) return [];
    return Array.isArray(procedureKits[procedureId]) ? procedureKits[procedureId] : [];
  }

  function rebuildKitOptions(procedureId, preferredKitId) {
    if (!kitSelect) return "";
    const kits = kitsForProcedure(procedureId);
    const previous = preferredKitId || kitSelect.value || "";
    kitSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = selectKitLabel;
    kitSelect.appendChild(placeholder);

    let selected = "";
    if (procedureId && kits.length) {
      selected = previous;
      if (!kits.some((kit) => String(kit.id) === String(selected))) {
        const defaults = kits.find((kit) => kit.is_default);
        selected = defaults ? defaults.id : kits[0]?.id || "";
      }
      kits.forEach((kit) => {
        const opt = document.createElement("option");
        opt.value = kit.id;
        opt.textContent = kit.name;
        if (String(kit.id) === String(selected)) opt.selected = true;
        kitSelect.appendChild(opt);
      });
    }
    if (!selected) placeholder.selected = true;
    kitSelect.disabled = !procedureId;
    return selected || "";
  }

  function renderKitLines(lines) {
    if (!tbody) return;
    tbody.replaceChildren();
    if (!lines || !lines.length) {
      const empty = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.textContent = selectKitEmpty;
      empty.appendChild(cell);
      tbody.appendChild(empty);
      return;
    }
    let currentCat = "";
    lines.forEach((line) => {
      const cat = line.category_label || line.category_code || otherLabel;
      if (cat !== currentCat) {
        currentCat = cat;
        const heading = document.createElement("tr");
        heading.className = "inventory-type-row";
        const hCell = document.createElement("td");
        hCell.colSpan = 4;
        const strong = document.createElement("strong");
        strong.className = "inventory-type-heading";
        strong.textContent = cat;
        hCell.appendChild(strong);
        heading.appendChild(hCell);
        tbody.appendChild(heading);
      }
      const sem = String(line.semaphore || "OK").toUpperCase();
      const semClass = sem === "OK" ? "ok" : sem === "WARN" ? "warn" : "block";
      const row = document.createElement("tr");
      row.dataset.stockRow = "";
      row.dataset.familyId = line.family_id;
      row.dataset.stockAvailable = String(line.stock_available ?? "");
      row.dataset.stockTotal = String(line.stock_total ?? "");

      const nameTd = document.createElement("td");
      nameTd.appendChild(document.createTextNode(line.family_name || ""));
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "family_id[]";
      hidden.value = line.family_id;
      nameTd.appendChild(hidden);

      const qtyTd = document.createElement("td");
      qtyTd.className = "data-table__numeric-column";
      const qty = document.createElement("input");
      qty.className = "form-field__input form-field__input--plain";
      qty.type = "number";
      qty.min = "1";
      qty.name = "expected_quantity[]";
      qty.value = String(line.expected_quantity ?? 1);
      qty.required = true;
      qty.dataset.qtyInput = "";
      qtyTd.appendChild(qty);

      const stockTd = document.createElement("td");
      stockTd.dataset.stockLabel = "";
      stockTd.textContent = `${line.stock_available} / ${line.stock_total}`;

      const semTd = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `semaphore-badge semaphore-badge--${semClass}`;
      badge.dataset.semaphoreBadge = "";
      badge.textContent = line.semaphore || sem;
      semTd.appendChild(badge);

      row.append(nameTd, qtyTd, stockTd, semTd);
      tbody.appendChild(row);
    });
    bindQtyInputs(tbody);
  }

  async function loadKitLines(kitId) {
    if (!kitLinesUrl || !tbody) return;
    if (!kitId) {
      renderKitLines([]);
      return;
    }
    try {
      const url = new URL(kitLinesUrl, window.location.origin);
      url.searchParams.set("kit_id", kitId);
      const res = await fetch(url.toString(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      renderKitLines(data.lines || []);
    } catch (_err) {
      /* keep previous table on transient errors */
    }
  }

  async function onProcedureChange() {
    const procedureId = procedureSelect?.value || "";
    const kitId = rebuildKitOptions(procedureId, "");
    await loadKitLines(kitId);
  }

  procedureSelect?.addEventListener("change", () => {
    onProcedureChange();
  });

  kitSelect?.addEventListener("change", () => {
    loadKitLines(kitSelect.value || "");
  });

  bindQtyInputs(form);

  function openPrivacyModal() {
    if (!privacyModal) return;
    privacyModal.classList.add("is-open");
    privacyModal.setAttribute("aria-hidden", "false");
  }

  function closePrivacyModal() {
    if (!privacyModal) return;
    privacyModal.classList.remove("is-open");
    privacyModal.setAttribute("aria-hidden", "true");
  }

  function syncHiddenPrivacy(manualPath) {
    const noticeId = manualPath ? "" : (noticeSelect?.value || "");
    const confirmed = !manualPath && !!confirmCb?.checked;
    if (hiddenNotice) hiddenNotice.value = noticeId;
    if (hiddenConfirm) hiddenConfirm.value = confirmed ? "1" : "";
    if (hiddenConfirmCb) hiddenConfirmCb.value = confirmed ? "1" : "";
    if (hiddenModel) hiddenModel.value = modelCb?.checked ? "1" : "";
  }

  function submitAfterPrivacy(manualPath) {
    if (!manualPath) {
      if (!noticeSelect?.value) {
        if (window.appConfirm) {
          window.appConfirm({
            title: t("privacyNoticeRequiredTitle"),
            message: t("privacyNoticeRequiredMessage"),
            okLabel: t("ok"),
            cancelLabel: t("close"),
          });
        }
        return;
      }
      if (!confirmCb?.checked) {
        if (window.appConfirm) {
          window.appConfirm({
            title: t("privacyConfirmationRequiredTitle"),
            message: t("privacyConfirmationRequiredMessage"),
            okLabel: t("ok"),
            cancelLabel: t("close"),
          });
        }
        return;
      }
    }
    syncHiddenPrivacy(manualPath);
    closePrivacyModal();
    if (window.appConfirmProceed) window.appConfirmProceed(form);
    else HTMLFormElement.prototype.submit.call(form);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    // Disabled controls are omitted from FormData; re-enable if a kit was chosen.
    if (kitSelect?.disabled && kitSelect.value) kitSelect.disabled = false;
    openPrivacyModal();
  });

  privacyModal?.querySelector("[data-privacy-modal-cancel]")?.addEventListener("click", () => {
    closePrivacyModal();
  });
  privacyModal?.querySelector("[data-privacy-modal-manual]")?.addEventListener("click", () => {
    submitAfterPrivacy(true);
  });
  privacyModal?.querySelector("[data-privacy-modal-with-notice]")?.addEventListener("click", () => {
    submitAfterPrivacy(false);
  });
  privacyModal?.addEventListener("click", (event) => {
    if (event.target === privacyModal) closePrivacyModal();
  });

  function selectedPhysicianIds(exceptHidden) {
    return new Set(
      [...physicianHost.querySelectorAll("[data-physician-id]")]
        .filter((el) => el !== exceptHidden)
        .map((el) => el.value)
        .filter(Boolean)
    );
  }

  function findPhysician(id) {
    return physicianOptions.find((p) => String(p.id) === String(id)) || null;
  }

  function closeMenus(except) {
    physicianHost.querySelectorAll("[data-physician-menu]").forEach((menu) => {
      if (menu !== except) menu.hidden = true;
    });
  }

  function renderMenu(row) {
    const input = row.querySelector("[data-physician-typeahead]");
    const hidden = row.querySelector("[data-physician-id]");
    const menu = row.querySelector("[data-physician-menu]");
    if (!input || !hidden || !menu) return;
    const query = (input.value || "").trim().toLowerCase();
    const taken = selectedPhysicianIds(hidden);
    const matches = physicianOptions.filter((p) => {
      if (taken.has(String(p.id))) return false;
      if (!query) return true;
      return String(p.name || "").toLowerCase().includes(query);
    });
    menu.innerHTML = "";
    if (!matches.length) {
      const empty = document.createElement("li");
      empty.textContent = t("noMatchingPhysicians");
      empty.style.opacity = "0.7";
      empty.style.cursor = "default";
      menu.appendChild(empty);
      menu.hidden = false;
      return;
    }
    matches.slice(0, 12).forEach((p, index) => {
      const li = document.createElement("li");
      li.textContent = p.name;
      li.dataset.id = p.id;
      if (index === 0) li.classList.add("is-active");
      li.addEventListener("mousedown", (event) => {
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
    const input = row.querySelector("[data-physician-typeahead]");
    const hidden = row.querySelector("[data-physician-id]");
    const menu = row.querySelector("[data-physician-menu]");
    if (!input || !hidden || !menu) return;

    input.addEventListener("focus", () => {
      closeMenus(menu);
      renderMenu(row);
    });
    input.addEventListener("input", () => {
      hidden.value = "";
      closeMenus(menu);
      renderMenu(row);
    });
    input.addEventListener("blur", () => {
      setTimeout(() => {
        menu.hidden = true;
        if (hidden.value) {
          const p = findPhysician(hidden.value);
          if (p) input.value = p.name;
        } else {
          input.value = "";
        }
      }, 120);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        menu.hidden = true;
        return;
      }
      if (event.key === "Enter") {
        const active = menu.querySelector("li.is-active[data-id]");
        if (active && !menu.hidden) {
          event.preventDefault();
          hidden.value = active.dataset.id;
          input.value = active.textContent || "";
          menu.hidden = true;
        }
      }
    });

    row.querySelector("[data-physician-remove]")?.addEventListener("click", () => {
      const rows = physicianHost.querySelectorAll("[data-physician-row]");
      if (rows.length <= 1) return;
      row.remove();
    });
  }

  function addPhysicianRow() {
    if (!physicianHost || !physicianTemplate) return;
    const node = physicianTemplate.content.firstElementChild.cloneNode(true);
    physicianHost.appendChild(node);
    bindPhysicianRow(node);
  }

  document.querySelector("[data-physician-add]")?.addEventListener("click", addPhysicianRow);
  if (physicianHost && physicianTemplate && !physicianHost.children.length) {
    addPhysicianRow();
  } else {
    physicianHost?.querySelectorAll("[data-physician-row]").forEach(bindPhysicianRow);
  }
})();
