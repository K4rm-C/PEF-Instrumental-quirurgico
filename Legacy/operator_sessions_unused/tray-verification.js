/*
  Operator V3 Final Tray Verification — demo transition only.

  On the "verifying" state, animates the progress bar and then navigates to the result URL
  the server rendered in data-next-url (failed on the first attempt, success otherwise).
  Deterministic and stateless: no API calls, nothing is stored, no workflow state changes.
*/
(function () {
    "use strict";

    var panel = document.querySelector("[data-v3-tray-verifying]");
    if (!panel) {
        return;
    }
    var nextUrl = panel.getAttribute("data-next-url");
    var bar = panel.querySelector("[data-v3-tray-progress]");
    var DELAY_MS = 2500;

    if (bar) {
        window.requestAnimationFrame(function () {
            bar.style.setProperty("--status-progress-fill", "100%");
        });
    }
    if (nextUrl) {
        window.setTimeout(function () {
            window.location.assign(nextUrl);
        }, DELAY_MS);
    }
})();
