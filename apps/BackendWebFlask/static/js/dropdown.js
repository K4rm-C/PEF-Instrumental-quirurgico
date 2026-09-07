(function () {
    "use strict";

    function closeDropdown(dropdown) {
        var trigger = dropdown.querySelector("[data-dropdown-trigger]");
        var menu = dropdown.querySelector("[data-dropdown-menu]");
        if (!trigger || !menu) {
            return;
        }
        trigger.setAttribute("aria-expanded", "false");
        menu.hidden = true;
    }

    function closeAllDropdowns() {
        document.querySelectorAll("[data-dropdown]").forEach(closeDropdown);
    }

    function openDropdown(dropdown) {
        closeAllDropdowns();
        var trigger = dropdown.querySelector("[data-dropdown-trigger]");
        var menu = dropdown.querySelector("[data-dropdown-menu]");
        if (!trigger || !menu) {
            return;
        }
        trigger.setAttribute("aria-expanded", "true");
        menu.hidden = false;
    }

    document.addEventListener("click", function (event) {
        var trigger = event.target.closest("[data-dropdown-trigger]");
        if (trigger) {
            var dropdown = trigger.closest("[data-dropdown]");
            var isExpanded = trigger.getAttribute("aria-expanded") === "true";
            if (isExpanded) {
                closeDropdown(dropdown);
            } else {
                openDropdown(dropdown);
            }
            return;
        }

        var menuItem = event.target.closest("[data-dropdown-menu] button");
        if (menuItem) {
            closeAllDropdowns();
            return;
        }

        if (!event.target.closest("[data-dropdown]")) {
            closeAllDropdowns();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeAllDropdowns();
        }
    });
})();
