/* Header bell: unread count of the signed-in user, loaded after the page has rendered.
   It never blocks or breaks the page: any failure (NotificationsService down, network, timeout)
   simply leaves the bell without a badge and marks it as unavailable in its tooltip. */
(function () {
    var bell = document.querySelector("[data-notifications-bell]");
    if (!bell || !window.fetch) {
        return;
    }
    var badge = bell.querySelector("[data-notifications-badge]");
    var url = bell.getAttribute("data-unread-url");

    function show(count) {
        if (!badge) {
            return;
        }
        if (count > 0) {
            badge.textContent = count > 99 ? "99+" : String(count);
            badge.hidden = false;
            bell.setAttribute("aria-label", window.pefT("notificationsUnread", {count: count}));
        } else {
            badge.hidden = true;
            bell.setAttribute("aria-label", window.pefT("notifications"));
        }
    }

    function refresh() {
        var controller = window.AbortController ? new AbortController() : null;
        var timer = controller ? setTimeout(function () { controller.abort(); }, 4000) : null;
        fetch(url, {credentials: "same-origin", headers: {"Accept": "application/json"},
                    signal: controller ? controller.signal : undefined})
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (body) {
                if (body && body.available) {
                    bell.removeAttribute("data-unavailable");
                    bell.title = window.pefT("notifications");
                    show(body.unread);
                } else if (body && body.enabled === false) {
                    show(0);
                } else {
                    show(0);
                    bell.setAttribute("data-unavailable", "true");
                    bell.title = window.pefT("notificationsUnavailable");
                }
            })
            .catch(function () {
                show(0);
                bell.setAttribute("data-unavailable", "true");
                bell.title = window.pefT("notificationsUnavailable");
            })
            .then(function () { if (timer) { clearTimeout(timer); } });
    }

    refresh();
    setInterval(function () {
        if (!document.hidden) {
            refresh();
        }
    }, 60000);
})();
