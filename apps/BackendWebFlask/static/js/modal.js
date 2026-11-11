/*
  Generic modal open/close controller. No page in this iteration triggers a modal yet;
  this is foundational plumbing for later iterations (e.g. Close Session, Deactivate User).
  Usage: a trigger element with data-modal-open="modal-id" opens #modal-id, and any
  element inside the modal with data-modal-close closes it.
*/
(function () {
    "use strict";

    function openModal(modalId) {
        var overlay = document.getElementById(modalId);
        if (!overlay) {
            return;
        }
        overlay.hidden = false;
        var dialog = overlay.querySelector(".modal-dialog");
        if (dialog) {
            dialog.setAttribute("tabindex", "-1");
            dialog.focus();
        }
    }

    function closeModal(overlay) {
        overlay.hidden = true;
    }

    document.addEventListener("click", function (event) {
        var openTrigger = event.target.closest("[data-modal-open]");
        if (openTrigger) {
            openModal(openTrigger.getAttribute("data-modal-open"));
            return;
        }

        var closeTrigger = event.target.closest("[data-modal-close]");
        if (closeTrigger) {
            var overlay = closeTrigger.closest(".modal-overlay");
            if (overlay) {
                closeModal(overlay);
            }
            return;
        }

        if (event.target.classList && event.target.classList.contains("modal-overlay")) {
            closeModal(event.target);
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") {
            return;
        }
        document.querySelectorAll(".modal-overlay:not([hidden])").forEach(closeModal);
    });
})();
