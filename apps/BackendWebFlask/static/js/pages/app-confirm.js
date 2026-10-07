/**
 * Formal confirm overlay replacing window.confirm().
 * Usage:
 *   - data-app-confirm="Message" on a form → intercepts submit
 *   - window.appConfirm({ title, message|html, okLabel, cancelLabel }) → Promise<boolean>
 *   - window.appConfirmProceed(form) → submit once after a custom confirm (no re-entrancy)
 */
(function () {
    "use strict";

    // Default button/title text comes translated from base.html (window.PEF_I18N).
    function t(key) {
        return window.pefT ? window.pefT(key) : key;
    }

    function ensureModal() {
        var existing = document.getElementById("app-confirm-modal");
        if (existing) {
            return existing;
        }
        var wrap = document.createElement("div");
        wrap.id = "app-confirm-modal";
        wrap.className = "app-modal";
        wrap.setAttribute("role", "dialog");
        wrap.setAttribute("aria-modal", "true");
        wrap.innerHTML =
            '<div class="app-modal__panel">' +
            '<h2 class="app-modal__title" data-app-confirm-title></h2>' +
            '<div class="app-modal__body" data-app-confirm-body></div>' +
            '<div class="app-modal__actions">' +
            '<button type="button" class="btn btn--secondary" data-app-confirm-cancel></button>' +
            '<button type="button" class="btn btn--primary" data-app-confirm-ok></button>' +
            "</div></div>";
        document.body.appendChild(wrap);
        return wrap;
    }

    function openConfirm(options) {
        return new Promise(function (resolve) {
            var modal = ensureModal();
            var titleEl = modal.querySelector("[data-app-confirm-title]");
            var bodyEl = modal.querySelector("[data-app-confirm-body]");
            var okBtn = modal.querySelector("[data-app-confirm-ok]");
            var cancelBtn = modal.querySelector("[data-app-confirm-cancel]");
            titleEl.textContent = options.title || t("confirm");
            if (options.html) {
                bodyEl.innerHTML = options.html;
            } else {
                bodyEl.textContent = options.message || "";
            }
            okBtn.textContent = options.okLabel || t("ok");
            cancelBtn.textContent = options.cancelLabel || t("cancel");
            modal.classList.add("is-open");

            function close(result) {
                modal.classList.remove("is-open");
                okBtn.removeEventListener("click", onOk);
                cancelBtn.removeEventListener("click", onCancel);
                modal.removeEventListener("click", onBackdrop);
                resolve(result);
            }
            function onOk() {
                close(true);
            }
            function onCancel() {
                close(false);
            }
            function onBackdrop(event) {
                if (event.target === modal) {
                    close(false);
                }
            }
            okBtn.addEventListener("click", onOk);
            cancelBtn.addEventListener("click", onCancel);
            modal.addEventListener("click", onBackdrop);
            okBtn.focus();
        });
    }

    /**
     * Submit a form after a custom confirmation without re-firing submit handlers.
     * Native HTMLFormElement.submit() does not dispatch the submit event.
     */
    function proceedSubmit(form) {
        if (!(form instanceof HTMLFormElement)) {
            return;
        }
        form.setAttribute("data-app-confirm-skip", "1");
        HTMLFormElement.prototype.submit.call(form);
    }

    window.appConfirm = openConfirm;
    window.appConfirmProceed = proceedSubmit;

    document.addEventListener(
        "submit",
        function (event) {
            var form = event.target;
            if (!(form instanceof HTMLFormElement)) {
                return;
            }
            if (form.getAttribute("data-app-confirm-skip") === "1") {
                // Only consume skip for declarative data-app-confirm forms.
                // Custom handlers (kit/instrument/etc.) manage their own skip flag.
                if (form.hasAttribute("data-app-confirm")) {
                    form.removeAttribute("data-app-confirm-skip");
                }
                return;
            }
            var message = form.getAttribute("data-app-confirm");
            if (!message) {
                return;
            }
            event.preventDefault();
            event.stopImmediatePropagation();
            openConfirm({
                title: form.getAttribute("data-app-confirm-title") || t("confirm"),
                message: message,
                okLabel: form.getAttribute("data-app-confirm-ok") || t("continue"),
                cancelLabel: form.getAttribute("data-app-confirm-cancel") || t("cancel"),
                html: form.getAttribute("data-app-confirm-html") || null,
            }).then(function (ok) {
                if (!ok) {
                    return;
                }
                proceedSubmit(form);
            });
        },
        true
    );
})();
