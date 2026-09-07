/*
  IT Administrator New User form — lightweight, presentation-only Password / Confirm
  Password match validation (implementation prompt section 22). Shows an inline,
  translation-ready message (read from the form's data-password-mismatch-message attribute,
  rendered server-side via Jinja) when the two fields differ; never hashes or persists a
  password client-side.
*/
(function () {
    "use strict";

    var form = document.getElementById("user-form");
    if (!form) {
        return;
    }

    var password = document.getElementById("user-password");
    var confirmPassword = document.getElementById("user-confirm-password");
    var mismatchMessage = document.getElementById("user-password-mismatch");

    if (!password || !confirmPassword || !mismatchMessage) {
        return;
    }

    function checkPasswordsMatch() {
        var mismatch = confirmPassword.value.length > 0 && password.value !== confirmPassword.value;
        mismatchMessage.textContent = mismatch ? form.getAttribute("data-password-mismatch-message") : "";
        mismatchMessage.hidden = !mismatch;
        return !mismatch;
    }

    password.addEventListener("input", checkPasswordsMatch);
    confirmPassword.addEventListener("input", checkPasswordsMatch);

    form.addEventListener("submit", function (event) {
        if (!checkPasswordsMatch()) {
            event.preventDefault();
        }
    });
})();
