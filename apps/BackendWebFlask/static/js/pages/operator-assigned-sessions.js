/*
  Operator V3 Assigned Sessions — client-side demo filtering of the fixture rows.

  Loaded only in demo mode (the RF list keeps its previous filter bar). Search matches the
  session id, procedure or patient (case- and accent-insensitive); Status and Operating Room
  match exactly; Date is "Today" (the demo day, data-today) or "Upcoming" (next 7 days).
  No request is made and nothing is stored; a reload shows every row again.
*/
(function () {
    "use strict";

    var form = document.querySelector("[data-v3-session-filter]");
    if (!form) {
        return;
    }

    var rows = Array.prototype.slice.call(document.querySelectorAll("[data-session-row]"));
    var empty = document.querySelector("[data-session-filter-empty]");
    var info = document.querySelector("[data-session-filter-info]");
    var search = form.querySelector('[name="search"]');
    var status = form.querySelector('[name="status"]');
    var room = form.querySelector('[name="room"]');
    var date = form.querySelector('[name="date"]');
    var today = form.dataset.today;

    function normalize(value) {
        return (value || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
    }

    function daysFromToday(isoDate) {
        var start = Date.parse(today + "T00:00:00Z");
        var target = Date.parse(isoDate + "T00:00:00Z");
        if (isNaN(start) || isNaN(target)) {
            return null;
        }
        return Math.round((target - start) / 86400000);
    }

    function matchesDate(row) {
        if (!date.value) {
            return true;
        }
        var days = daysFromToday(row.dataset.date);
        if (date.value === "today") {
            return days === 0;
        }
        return days !== null && days >= 1 && days <= 7;
    }

    function apply() {
        var query = normalize(search.value);
        var shown = 0;
        rows.forEach(function (row) {
            var visible = (!query || normalize(row.dataset.search).indexOf(query) !== -1)
                && (!status.value || row.dataset.status === status.value)
                && (!room.value || row.dataset.room === room.value)
                && matchesDate(row);
            row.hidden = !visible;
            shown += visible ? 1 : 0;
        });
        if (empty) {
            empty.hidden = shown !== 0;
        }
        if (info && info.dataset.template) {
            info.textContent = info.dataset.template.replace("{shown}", shown).replace("{total}", rows.length);
        }
    }

    form.addEventListener("input", apply);
    form.addEventListener("change", apply);
    // Filtering is instant; pressing Enter in Search must not reload the page.
    form.addEventListener("submit", function (event) {
        event.preventDefault();
        apply();
    });
    apply();
})();
