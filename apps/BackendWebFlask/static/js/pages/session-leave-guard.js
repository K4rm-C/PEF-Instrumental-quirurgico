/**
 * Operator session leave guard.
 *
 * Active only when the script tag carries data-enabled="1". While active, any click that would
 * navigate away from the current page (sidebar links, breadcrumb links, sign out, or any other
 * same-origin link) first opens window.appConfirm with a 3 s countdown on the OK button
 * ("Leave (3)" → "Leave (2)" → "Leave (1)" → enabled "Leave"). Cancel keeps the user here.
 * Forms are only guarded when they opt in with data-leave-session.
 */
(function () {
    "use strict";

    var script = document.currentScript;
    if (!script || script.getAttribute("data-enabled") !== "1") {
        return;
    }

    var COUNTDOWN_SECONDS = 3;
    var NAV_SELECTOR = "a.sidebar__nav-link, .breadcrumb-list a, .breadcrumb-item a, [data-auth-logout]";
    var bypass = false;
    var pending = false;

    function t(key, fallback) {
        if (!window.pefT) {
            return fallback;
        }
        var text = window.pefT(key);
        return text === key ? fallback : text;
    }

    function leavesCurrentPage(link) {
        if (link.hasAttribute("download")) {
            return false;
        }
        var target = link.getAttribute("target");
        if (target && target !== "_self") {
            return false;
        }
        var rawHref = link.getAttribute("href");
        if (!rawHref || rawHref.charAt(0) === "#") {
            return false;
        }
        var url;
        try {
            url = new URL(link.href, window.location.href);
        } catch (err) {
            return false;
        }
        if (url.protocol !== "http:" && url.protocol !== "https:") {
            return false;
        }
        if (url.origin !== window.location.origin) {
            return false;
        }
        // Same path and query (only the hash differs) → not leaving the page.
        return !(url.pathname === window.location.pathname && url.search === window.location.search);
    }

    function askToLeave() {
        var title = t("leaveSessionTitle", "Leave this session?");
        var message = t(
            "leaveSessionMessage",
            "You are working in an active session. If you leave now, you can resume it later from your sessions list."
        );
        var okLabel = t("leaveSessionOk", "Leave");

        if (typeof window.appConfirm === "function") {
            return window.appConfirm({
                title: title,
                message: message,
                okLabel: okLabel,
                okDelaySeconds: COUNTDOWN_SECONDS,
            });
        }
        return Promise.resolve(window.confirm(message));
    }

    function guard(event, proceed) {
        if (pending) {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        pending = true;
        askToLeave().then(function (ok) {
            pending = false;
            if (!ok) {
                return;
            }
            bypass = true;
            try {
                proceed();
            } finally {
                bypass = false;
            }
        });
    }

    document.addEventListener(
        "click",
        function (event) {
            if (bypass || event.defaultPrevented) {
                return;
            }
            if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
                return;
            }
            if (!(event.target instanceof Element)) {
                return;
            }
            if (event.target.closest("#app-confirm-modal")) {
                return;
            }
            var logout = event.target.closest("[data-auth-logout]");
            var link = logout || event.target.closest(NAV_SELECTOR) || event.target.closest("a[href]");
            if (!link) {
                return;
            }
            // Sign out always leaves the session; other links only when they change page.
            if (!logout && !leavesCurrentPage(link)) {
                return;
            }
            // Re-dispatching click lets other handlers (e.g. auth-client sign out) run as usual.
            guard(event, function () {
                link.click();
            });
        },
        true
    );

    document.addEventListener(
        "submit",
        function (event) {
            var form = event.target;
            if (bypass || !(form instanceof HTMLFormElement) || !form.hasAttribute("data-leave-session")) {
                return;
            }
            guard(event, function () {
                if (typeof window.appConfirmProceed === "function") {
                    window.appConfirmProceed(form);
                } else {
                    HTMLFormElement.prototype.submit.call(form);
                }
            });
        },
        true
    );
})();
