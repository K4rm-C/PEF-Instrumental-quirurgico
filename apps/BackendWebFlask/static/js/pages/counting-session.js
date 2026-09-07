/*
  Active Counting Session — presentation-only behavior.

  Increments/decrements the visible counted quantity for one instrument row, recomputes the
  displayed difference, and toggles that row's discrepancy highlight. This is PREVIEW /
  PRESENTATION behavior only: it does not save anything, call any API, infer workflow
  authorization, or communicate with the capture/YOLO pipeline. The backend remains the
  source of truth and will later validate and persist the actual counts.
*/
(function () {
    "use strict";

    function updateRow(row) {
        var valueEl = row.querySelector("[data-counter-value]");
        var differenceEl = row.querySelector("[data-counter-difference]");
        var decrementButton = row.querySelector("[data-counter-decrement]");

        var expected = parseInt(valueEl.getAttribute("data-expected"), 10);
        var counted = parseInt(valueEl.textContent, 10);
        var difference = counted - expected;

        differenceEl.textContent = String(difference);
        differenceEl.classList.toggle("text-danger", difference !== 0);
        differenceEl.classList.toggle("text-success", difference === 0);
        row.classList.toggle("data-table__row--highlight-danger", difference !== 0);

        if (decrementButton) {
            decrementButton.disabled = counted <= 0;
        }
    }

    document.addEventListener("click", function (event) {
        var incrementButton = event.target.closest("[data-counter-increment]");
        var decrementButton = event.target.closest("[data-counter-decrement]");
        var button = incrementButton || decrementButton;
        if (!button) {
            return;
        }

        var row = button.closest("tr");
        var valueEl = row.querySelector("[data-counter-value]");
        var counted = parseInt(valueEl.textContent, 10);

        if (incrementButton) {
            counted += 1;
        } else if (counted > 0) {
            counted -= 1;
        }

        valueEl.textContent = String(counted);
        updateRow(row);
    });
})();
