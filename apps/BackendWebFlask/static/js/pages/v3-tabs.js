/*
  V3 client-side tabs — presentation only (Operator / Supervisor Session Details).

  Markup:
    <div data-v3-tabs [data-v3-tabs-hash]>
      <div class="v3-tabs" role="tablist" aria-label="...">
        <button type="button" class="v3-tabs__link" role="tab" id="tab-x"
                data-tab-target="x" aria-controls="tab-panel-x">X</button>
        ...
      </div>
      <section class="card v3-tab-panel" role="tabpanel" id="tab-panel-x"
               aria-labelledby="tab-x" data-tab-panel="x">...</section>
    </div>

  - Tabs are free navigation (not steps): any tab can be opened at any time.
  - Only the selected panel is shown; the others get the `hidden` attribute.
  - Keyboard: Left / Right / Home / End move between tabs (roving tabindex).
  - With data-v3-tabs-hash, the selection is kept in location.hash (#x) so a reload or a
    link carrying the anchor reopens the same tab. Nothing is sent to the server.
*/
(function () {
    "use strict";

    function initTabs(root) {
        var tabs = Array.prototype.slice.call(root.querySelectorAll("[role='tab'][data-tab-target]"));
        var panels = Array.prototype.slice.call(root.querySelectorAll("[data-tab-panel]"));
        if (!tabs.length || !panels.length) {
            return;
        }
        var useHash = root.hasAttribute("data-v3-tabs-hash");

        function keyExists(key) {
            return tabs.some(function (tab) { return tab.getAttribute("data-tab-target") === key; });
        }

        function select(key, options) {
            options = options || {};
            tabs.forEach(function (tab) {
                var active = tab.getAttribute("data-tab-target") === key;
                tab.classList.toggle("v3-tabs__link--active", active);
                tab.setAttribute("aria-selected", active ? "true" : "false");
                tab.tabIndex = active ? 0 : -1;
                if (active && options.focus) {
                    tab.focus();
                }
            });
            panels.forEach(function (panel) {
                panel.hidden = panel.getAttribute("data-tab-panel") !== key;
            });
            if (useHash && options.updateHash && window.history && window.history.replaceState) {
                window.history.replaceState(null, "", "#" + key);
            }
        }

        function keyFromHash() {
            var key = (window.location.hash || "").replace(/^#/, "");
            return key && keyExists(key) ? key : null;
        }

        root.addEventListener("click", function (event) {
            var tab = event.target.closest("[role='tab'][data-tab-target]");
            if (!tab || !root.contains(tab)) {
                return;
            }
            select(tab.getAttribute("data-tab-target"), { updateHash: true });
        });

        root.addEventListener("keydown", function (event) {
            var index = tabs.indexOf(event.target);
            if (index === -1) {
                return;
            }
            var next = null;
            if (event.key === "ArrowRight") {
                next = (index + 1) % tabs.length;
            } else if (event.key === "ArrowLeft") {
                next = (index - 1 + tabs.length) % tabs.length;
            } else if (event.key === "Home") {
                next = 0;
            } else if (event.key === "End") {
                next = tabs.length - 1;
            }
            if (next === null) {
                return;
            }
            event.preventDefault();
            select(tabs[next].getAttribute("data-tab-target"), { focus: true, updateHash: true });
        });

        if (useHash) {
            window.addEventListener("hashchange", function () {
                var key = keyFromHash();
                if (key) {
                    select(key);
                }
            });
        }

        var initial = (useHash && keyFromHash()) ||
            (tabs.filter(function (tab) { return tab.getAttribute("aria-selected") === "true"; })[0] || tabs[0])
                .getAttribute("data-tab-target");
        select(initial);
    }

    Array.prototype.forEach.call(document.querySelectorAll("[data-v3-tabs]"), initTabs);
})();
