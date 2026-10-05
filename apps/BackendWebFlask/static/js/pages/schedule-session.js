(() => {
  const form = document.querySelector("[data-schedule-form]");
  if (!form) return;

  const stockUrl = form.dataset.stockRecheckUrl || "";
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
  let physicianOptions = [];
  try {
    physicianOptions = JSON.parse(physicianDataEl?.textContent || "[]") || [];
  } catch (_err) {
    physicianOptions = [];
  }

  function applySemaphore(badge, value) {
    if (!badge) return;
    const sem = String(value || "OK").toUpperCase();
    badge.textContent = sem;
    badge.className =
      "semaphore-badge semaphore-badge--" +
      (sem === "OK" ? "ok" : sem === "WARN" ? "warn" : "block");
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

  form.querySelectorAll("[data-qty-input]").forEach((input) => {
    input.addEventListener("change", recheckStock);
    input.addEventListener("blur", recheckStock);
  });

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
            title: "Privacy notice required",
            message: "Select a privacy notice version, or use Continue without privacy notice.",
            okLabel: "OK",
            cancelLabel: "Close",
          });
        }
        return;
      }
      if (!confirmCb?.checked) {
        if (window.appConfirm) {
          window.appConfirm({
            title: "Confirmation required",
            message: "Confirm the privacy notice checkbox before saving with a notice.",
            okLabel: "OK",
            cancelLabel: "Close",
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
    openPrivacyModal();
  });

  const kitSelect = form.querySelector("[data-kit-select]");
  const kitReloadUrl = form.dataset.kitReloadUrl || "";
  kitSelect?.addEventListener("change", () => {
    if (!kitReloadUrl) return;
    const kitId = kitSelect.value || "";
    const url = new URL(kitReloadUrl, window.location.origin);
    if (kitId) url.searchParams.set("kit_id", kitId);
    else url.searchParams.delete("kit_id");
    window.location.assign(url.toString());
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
      empty.textContent = "No matching physicians";
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
