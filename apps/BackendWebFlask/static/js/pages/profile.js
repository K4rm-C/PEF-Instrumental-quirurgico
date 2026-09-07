(function () {
    "use strict";

    function updateProfileThemeLabel() {
        var label = document.querySelector("[data-profile-theme-value]");
        if (!label) {
            return;
        }

        var preference = document.documentElement.getAttribute("data-theme-preference") || "system";
        var attributeName = "data-theme-" + preference + "-label";
        var translatedLabel = label.getAttribute(attributeName);
        if (translatedLabel) {
            label.textContent = translatedLabel;
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        updateProfileThemeLabel();

        var observer = new MutationObserver(updateProfileThemeLabel);
        observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ["data-theme-preference"],
        });
    });
})();
