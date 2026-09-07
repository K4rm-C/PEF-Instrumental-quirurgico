(function () {
    "use strict";

    var STORAGE_KEY = "themePreference";
    var root = document.documentElement;

    function getStoredPreference() {
        try {
            var value = localStorage.getItem(STORAGE_KEY);
            return value === "light" || value === "dark" || value === "system" ? value : "system";
        } catch (error) {
            return "system";
        }
    }

    function resolveAppliedTheme(preference) {
        if (preference === "system") {
            return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        }
        return preference;
    }

    function applyPreference(preference) {
        var appliedTheme = resolveAppliedTheme(preference);
        root.setAttribute("data-theme", appliedTheme);
        root.setAttribute("data-theme-preference", preference);

        try {
            localStorage.setItem(STORAGE_KEY, preference);
        } catch (error) {
            /* localStorage may be unavailable (private browsing, disabled storage); the
               theme still applies for this page view, it just will not persist. */
        }

        updateThemeSelectorUi(preference);
    }

    function updateThemeSelectorUi(preference) {
        var currentLabelEl = document.querySelector("[data-theme-selector-current-label]");
        var options = document.querySelectorAll("[data-theme-choice]");

        options.forEach(function (option) {
            var isSelected = option.getAttribute("data-theme-choice") === preference;
            var listItem = option.closest("[role='option']");
            if (listItem) {
                listItem.setAttribute("aria-selected", isSelected ? "true" : "false");
            }
            if (isSelected && currentLabelEl) {
                currentLabelEl.textContent = option.getAttribute("data-theme-label");
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        updateThemeSelectorUi(getStoredPreference());

        document.querySelectorAll("[data-theme-choice]").forEach(function (option) {
            option.addEventListener("click", function () {
                applyPreference(option.getAttribute("data-theme-choice"));
            });
        });
    });

    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
        if (getStoredPreference() === "system") {
            applyPreference("system");
        }
    });
})();
