/*
  Client-side list filtering for forms marked data-client-filter. The rows are already the
  real, institution-scoped rows rendered by the server; this only hides rows that do not match:
  - the search input matches any text in the row (case-insensitive);
  - each select matches its selected option label (text after "Label: ") as a whole word in
    the row, so "Active" never matches "Inactive". An empty value means "all".
*/
(function () {
    "use strict";

    function escapeRegExp(value) {
        return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }

    function criteria(form) {
        var result = [];
        form.querySelectorAll("input[type=search], input[type=text]").forEach(function (input) {
            if (input.value.trim()) {
                result.push({ text: input.value.trim().toLowerCase() });
            }
        });
        form.querySelectorAll("select").forEach(function (select) {
            var option = select.options[select.selectedIndex];
            if (option && option.value) {
                var label = option.textContent.trim();
                label = label.indexOf(": ") >= 0 ? label.slice(label.lastIndexOf(": ") + 2) : label;
                result.push({ word: new RegExp("(^|[^\\w])" + escapeRegExp(label) + "($|[^\\w])", "i") });
            }
        });
        return result;
    }

    function apply(form, table) {
        var rules = criteria(form);
        var shown = 0;
        table.querySelectorAll("tbody tr").forEach(function (row) {
            var text = row.textContent.replace(/\s+/g, " ");
            var match = rules.every(function (rule) {
                return rule.text ? text.toLowerCase().indexOf(rule.text) >= 0 : rule.word.test(text);
            });
            row.hidden = !match;
            shown += match ? 1 : 0;
        });
        var info = document.querySelector(".pagination__info");
        if (info) {
            info.setAttribute("data-filtered-count", String(shown));
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("form[data-client-filter]").forEach(function (form) {
            var table = document.querySelector(".data-table");
            if (!table) {
                return;
            }
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                apply(form, table);
            });
            form.addEventListener("input", function () { apply(form, table); });
            form.addEventListener("change", function () { apply(form, table); });
            apply(form, table);
        });
    });
})();
