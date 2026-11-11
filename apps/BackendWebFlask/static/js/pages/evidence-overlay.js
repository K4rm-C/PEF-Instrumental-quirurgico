/* MS-03: place YOLO boxes (bbox in image pixels: x, y, width, height) over a real evidence image once it has
   loaded, using the same CSS variables as the v3 evidence figure (percentages of the frame). */
(function () {
    "use strict";

    function fitOverlay(image, overlay) {
        // The image is shown with object-fit: contain (letterboxed inside the frame): the overlay must cover
        // only the drawn picture, never the empty bands, or the boxes would be shifted.
        var frameW = image.clientWidth, frameH = image.clientHeight;
        var scale = Math.min(frameW / image.naturalWidth, frameH / image.naturalHeight);
        var w = image.naturalWidth * scale, h = image.naturalHeight * scale;
        overlay.style.inset = "auto";
        overlay.style.left = (image.offsetLeft + (frameW - w) / 2) + "px";
        overlay.style.top = (image.offsetTop + (frameH - h) / 2) + "px";
        overlay.style.width = w + "px";
        overlay.style.height = h + "px";
    }

    function place(image) {
        var overlay = image.parentElement && image.parentElement.querySelector("[data-evidence-overlay]");
        if (!overlay || !image.naturalWidth || !image.naturalHeight) {
            return;
        }
        fitOverlay(image, overlay);
        window.addEventListener("resize", function () { fitOverlay(image, overlay); });
        overlay.querySelectorAll("[data-bbox]").forEach(function (box) {
            var parts = (box.getAttribute("data-bbox") || "").split(",").map(Number);
            if (parts.length !== 4 || parts.some(isNaN)) {
                return;
            }
            box.style.setProperty("--v3-box-x", (parts[0] / image.naturalWidth * 100) + "%");
            box.style.setProperty("--v3-box-y", (parts[1] / image.naturalHeight * 100) + "%");
            box.style.setProperty("--v3-box-w", (parts[2] / image.naturalWidth * 100) + "%");
            box.style.setProperty("--v3-box-h", (parts[3] / image.naturalHeight * 100) + "%");
            box.hidden = false;
        });
    }

    function missing(image) {
        // The image could not be loaded (expired link, storage down): show a placeholder, keep the page.
        var frame = image.parentElement;
        if (!frame) {
            return;
        }
        frame.classList.add("v3-evidence__frame--placeholder");
        var label = document.createElement("span");
        label.className = "v3-evidence__placeholder-label";
        label.setAttribute("data-evidence-missing", "");
        label.textContent = frame.closest("[data-evidence-traceability]")
            ? "The image could not be loaded right now."
            : "";
        image.replaceWith(label);
        var overlay = frame.querySelector("[data-evidence-overlay]");
        if (overlay) {
            overlay.remove();
        }
    }

    function init() {
        document.querySelectorAll("img[data-evidence-image]").forEach(function (image) {
            if (image.complete && image.naturalWidth) {
                place(image);
            } else if (image.complete) {
                missing(image);
            } else {
                image.addEventListener("load", function () { place(image); });
                image.addEventListener("error", function () { missing(image); });
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
}());
