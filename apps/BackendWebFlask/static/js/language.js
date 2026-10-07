/**
 * Header language selector (partials/language_selector.html).
 * POSTs the chosen public locale (en | es-MX) to the web backend, which stores it in the
 * pef_locale cookie and, for signed-in users, in user.ui_preferences. Then reloads the
 * current URL unchanged (path, query and #hash tab) so the page renders in the new language.
 * dropdown.js closes the menu on the same click; this script only handles the selection.
 */
(function () {
    "use strict";

    var pending = false;

    function currentLocale() {
        return document.documentElement.getAttribute("lang") || "en";
    }

    function changeLocale(locale) {
        if (pending || !locale || locale === currentLocale()) {
            return;
        }
        pending = true;
        fetch(document.body.dataset.localeUrl || "/locale", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ locale: locale }),
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("locale " + response.status);
                }
                // Full reload of the same URL; a hash-only assign() would not reload.
                window.location.reload();
            })
            .catch(function () {
                pending = false;
                if (window.appConfirm && window.pefT) {
                    window.appConfirm({
                        title: window.pefT("confirm"),
                        message: window.pefT("languageChangeFailed"),
                        okLabel: window.pefT("ok"),
                        cancelLabel: window.pefT("close"),
                    });
                }
            });
    }

    document.addEventListener("click", function (event) {
        var option = event.target.closest("[data-language-code]");
        if (!option) {
            return;
        }
        changeLocale(option.getAttribute("data-language-code"));
    });
})();
