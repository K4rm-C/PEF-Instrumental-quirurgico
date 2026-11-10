(function () {
    "use strict";

    var root = document.querySelector("[data-evidence-validation]");
    if (!root) return;

    var listUrl = root.getAttribute("data-clips-url") || "";
    var layout = root.querySelector("[data-ev-layout]");
    var stage = root.querySelector("[data-ev-stage]");
    var stageImg = root.querySelector("[data-ev-still]");
    var stageVideo = root.querySelector("[data-ev-video]");
    var placeholder = root.querySelector("[data-ev-placeholder]");
    var clipList = root.querySelector("[data-ev-clip-list]");
    var emptyEl = root.querySelector("[data-ev-empty]");
    var toggleBtn = root.querySelector("[data-ev-toggle-drawer]");
    var zoomBtn = root.querySelector("[data-ev-zoom]");
    var lightbox = root.querySelector("[data-ev-lightbox]");
    var lightboxMedia = root.querySelector("[data-ev-lightbox-media]");
    var lightboxClose = root.querySelector("[data-ev-lightbox-close]");
    var countHint = root.querySelector("[data-ev-count-hint]");
    var garageBanner = root.querySelector("[data-ev-garage-banner]");

    // Still from Finish (annotated). Evidence videos use native controls; overlay is still-only.
    var stillSrc = stageImg ? (stageImg.getAttribute("src") || "") : "";

    var i18n = {
        empty: root.getAttribute("data-i18n-empty") || "No evidence clips yet.",
        loadError: root.getAttribute("data-i18n-load-error") || "Could not load evidence clips.",
        frame: root.getAttribute("data-i18n-frame") || "frame",
        frames: root.getAttribute("data-i18n-frames") || "frames",
        openDrawer: root.getAttribute("data-i18n-open-drawer") || "Evidence clips",
        closeDrawer: root.getAttribute("data-i18n-close-drawer") || "Hide evidence",
        ratio: root.getAttribute("data-i18n-ratio") || "",
        garageFail: root.getAttribute("data-i18n-garage-fail") ||
            "Some clips are not yet synced to object storage / DB. Local playback is used until upload retry succeeds."
    };

    function setGarageBanner(on) {
        if (!garageBanner) return;
        if (on) {
            garageBanner.hidden = false;
            garageBanner.textContent = i18n.garageFail;
        } else {
            garageBanner.hidden = true;
            garageBanner.textContent = "";
        }
    }

    function showStill() {
        if (stageVideo) {
            stageVideo.pause();
            stageVideo.removeAttribute("src");
            stageVideo.load();
            stageVideo.hidden = true;
        }
        if (stageImg) {
            if (stillSrc) {
                stageImg.src = stillSrc;
                stageImg.hidden = false;
            } else {
                stageImg.hidden = true;
            }
        }
        if (placeholder) placeholder.hidden = !!stillSrc;
        if (stage) stage.removeAttribute("data-zoom");
    }

    function playClip(url, kind) {
        if (!url) return;
        if (kind === "jpeg" || /\.(jpe?g)(\?|$)/i.test(url)) {
            if (stageImg) {
                stageImg.src = url;
                stageImg.hidden = false;
            }
            if (stageVideo) {
                stageVideo.hidden = true;
                stageVideo.removeAttribute("src");
            }
            if (placeholder) placeholder.hidden = true;
            return;
        }
        if (stageImg) stageImg.hidden = true;
        if (stageVideo) {
            stageVideo.src = url;
            stageVideo.hidden = false;
            stageVideo.play().catch(function () { /* autoplay */ });
        }
        if (placeholder) placeholder.hidden = true;
    }

    function openStillLightbox() {
        if (!lightbox || !lightboxMedia || !stillSrc) return;
        lightboxMedia.innerHTML = "";
        var img = document.createElement("img");
        img.src = stillSrc;
        img.alt = "";
        lightboxMedia.appendChild(img);
        lightbox.classList.add("is-open");
    }

    function closeLightbox() {
        if (!lightbox || !lightboxMedia) return;
        lightbox.classList.remove("is-open");
        lightboxMedia.innerHTML = "";
    }

    function setDrawerOpen(open) {
        if (!layout) return;
        layout.classList.toggle("is-drawer-closed", !open);
        if (toggleBtn) {
            toggleBtn.textContent = open ? i18n.closeDrawer : i18n.openDrawer;
            toggleBtn.setAttribute("aria-expanded", open ? "true" : "false");
        }
    }

    function reasonLabel(clip) {
        if (clip && clip.reason_label) return String(clip.reason_label);
        return String((clip && clip.reason) || "clip").replace(/_/g, " ");
    }

    function renderClips(clips) {
        if (!clipList) return;
        clipList.innerHTML = "";
        if (!clips || !clips.length) {
            if (emptyEl) {
                emptyEl.hidden = false;
                emptyEl.textContent = i18n.empty;
            }
            return;
        }
        if (emptyEl) emptyEl.hidden = true;
        clips.forEach(function (clip) {
            var li = document.createElement("li");
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "v3-op-ev__clip";
            btn.setAttribute("data-clip-id", clip.clip_id || "");
            var playUrl = clip.play_url || clip.signed_url || "";
            btn.setAttribute("data-play-url", playUrl);
            btn.setAttribute("data-kind", clip.kind || "mp4");

            var title = document.createElement("span");
            title.className = "v3-op-ev__clip-reason";
            title.textContent = reasonLabel(clip);

            var meta = document.createElement("span");
            meta.className = "v3-op-ev__clip-meta";
            var bits = [];
            if (clip.phase_name || clip.phase_code) {
                bits.push(clip.phase_name || clip.phase_code);
            }
            if (clip.ratio_label) bits.push(clip.ratio_label);
            if (clip.occurred_at) bits.push(String(clip.occurred_at).replace("T", " ").slice(0, 19));
            if (clip.frame_count != null) {
                var frameWord = (Number(clip.frame_count) === 1 && i18n.frame)
                    ? i18n.frame
                    : i18n.frames;
                bits.push(clip.frame_count + " " + frameWord);
            }
            meta.textContent = bits.join(" · ");

            btn.appendChild(title);
            btn.appendChild(meta);
            btn.addEventListener("click", function () {
                clipList.querySelectorAll(".v3-op-ev__clip").forEach(function (el) {
                    el.classList.remove("is-active");
                });
                btn.classList.add("is-active");
                playClip(playUrl, clip.kind || "mp4");
            });
            li.appendChild(btn);
            clipList.appendChild(li);
        });
    }

    function loadClips() {
        if (!listUrl) {
            renderClips([]);
            return;
        }
        fetch(listUrl, { credentials: "same-origin" })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (!res.ok) throw new Error(res.j.error || "load");
                renderClips(res.j.clips || []);
                if (countHint && res.j.ratio_label) {
                    countHint.textContent = res.j.ratio_label;
                }
                setGarageBanner(!!res.j.garage_fail);
            })
            .catch(function () {
                if (emptyEl) {
                    emptyEl.hidden = false;
                    emptyEl.textContent = i18n.loadError;
                }
                setGarageBanner(false);
            });
    }

    // Click on last-frame stage → overlay (not CSS scale-to-center).
    if (stage) {
        stage.addEventListener("click", function (ev) {
            if (ev.target && ev.target.closest && ev.target.closest("video")) {
                return;
            }
            openStillLightbox();
        });
    }
    if (zoomBtn) zoomBtn.addEventListener("click", function (ev) {
        ev.preventDefault();
        openStillLightbox();
    });
    if (lightboxClose) lightboxClose.addEventListener("click", closeLightbox);
    if (lightbox) {
        lightbox.addEventListener("click", function (ev) {
            if (ev.target === lightbox) closeLightbox();
        });
    }
    document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") closeLightbox();
    });
    if (toggleBtn) {
        toggleBtn.addEventListener("click", function () {
            var closed = layout && layout.classList.contains("is-drawer-closed");
            setDrawerOpen(closed);
        });
    }

    showStill();
    setDrawerOpen(true);
    loadClips();
})();
