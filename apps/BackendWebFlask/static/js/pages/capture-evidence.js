/*
  Capture page — take a photo (camera on devices that support capture="environment") or pick
  an existing JPEG, preview it locally, then POST it to the session's capture endpoint. The
  server validates and stores the file; this script only improves the interaction and blocks
  accidental double submits.
*/
(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        var form = document.getElementById("capture-form");
        var input = document.getElementById("capture-input");
        var submit = document.getElementById("capture-submit");
        var preview = document.getElementById("capture-preview");
        var latest = document.getElementById("capture-latest");
        var empty = document.getElementById("capture-empty");
        var selectedName = document.getElementById("capture-selected-name");
        if (!form || !input || !submit) {
            return;
        }

        form.querySelectorAll("[data-capture-source]").forEach(function (button) {
            button.addEventListener("click", function () {
                if (button.getAttribute("data-capture-source") === "camera") {
                    input.setAttribute("capture", "environment");
                } else {
                    input.removeAttribute("capture");
                }
                input.click();
            });
        });

        input.addEventListener("change", function () {
            var file = input.files && input.files[0];
            submit.disabled = !file;
            if (!file) {
                return;
            }
            if (selectedName) {
                selectedName.textContent = file.name;
                selectedName.hidden = false;
            }
            if (preview && window.URL) {
                preview.src = window.URL.createObjectURL(file);
                preview.hidden = false;
                if (latest) { latest.hidden = true; }
                if (empty) { empty.hidden = true; }
            }
        });

        form.addEventListener("submit", function (event) {
            if (submit.disabled) {
                event.preventDefault();
                return;
            }
            submit.disabled = true;
            var label = submit.querySelector("span");
            if (label) {
                label.textContent = form.getAttribute("data-uploading-label") || (window.PEF_I18N ? window.PEF_I18N.t("uploading", "Uploading...") : "Uploading...");
            }
        });
    });
})();
