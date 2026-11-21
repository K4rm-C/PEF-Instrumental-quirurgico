(function () {
    "use strict";

    var script = document.querySelector("script[data-state-url]");
    if (!script) return;

    var stateUrl = script.getAttribute("data-state-url");
    var uploadUrl = script.getAttribute("data-upload-url");
    var phaseUrl = script.getAttribute("data-phase-url");
    var cancelUrl = script.getAttribute("data-cancel-url");
    var ticketUrl = script.getAttribute("data-ticket-url");
    var sessionId = script.getAttribute("data-session-id");

    var img = document.getElementById("capture-frame");
    var placeholder = document.getElementById("capture-placeholder");
    var stageEl = document.getElementById("capture-stage");
    var stageLoadingText = document.getElementById("capture-stage-loading-text");
    var toolbarMeta = document.getElementById("capture-toolbar-meta");
    var statusEl = document.getElementById("capture-status");
    var progressEl = document.getElementById("capture-progress");
    var modelEl = document.getElementById("capture-model");
    var latencyEl = document.getElementById("capture-latency");
    var table = document.getElementById("capture-counts-table");
    var uploadForm = document.getElementById("capture-upload-form");
    var uploadBtn = document.getElementById("capture-upload-btn");
    var phaseForm = document.getElementById("capture-phase-form");
    var phaseMsg = document.getElementById("capture-phase-msg");
    var phaseCurrentName = document.getElementById("capture-phase-current-name");
    var phaseSelect = phaseForm ? phaseForm.querySelector('select[name="phase_code"]') : null;
    var cameraBtn = document.getElementById("capture-camera-btn");
    var cameraSelect = document.getElementById("capture-camera-select");
    var cameraErr = document.getElementById("capture-camera-error");
    var stopFileBtn = document.getElementById("capture-stop-file-btn");
    var liveVideo = document.getElementById("capture-live-video");
    var liveCanvas = document.getElementById("capture-live-canvas");
    var liveCtx = liveCanvas ? liveCanvas.getContext("2d") : null;

    var pollTimer = null;
    var pollInFlight = false;
    var lastProcessed = -1;
    var lastSession = null;
    var liveMode = false;
    var fileMode = false;
    var workerBusy = false;
    var paceMs = 180;
    var grabTimer = null;
    var ws = null;
    var mediaStream = null;
    var latestJpeg = null;

    var IDLE_POLL_MS = 1500;
    var LIVE_POLL_MS = 2500;
    var MIN_POLL_MS = 50;
    var MAX_BUSY_POLL_MS = 400;

    var i18n = {
        catMeta: script.getAttribute("data-i18n-cat-meta") || "",
        pending: script.getAttribute("data-i18n-pending") || "",
        status: script.getAttribute("data-i18n-status") || "",
        progress: script.getAttribute("data-i18n-progress") || "",
        model: script.getAttribute("data-i18n-model") || "",
        uploading: script.getAttribute("data-i18n-uploading") || "",
        processing: script.getAttribute("data-i18n-processing") || "",
        connecting: script.getAttribute("data-i18n-connecting") || "Connecting…",
        error: script.getAttribute("data-i18n-error") || "",
        uploadFailed: script.getAttribute("data-i18n-upload-failed") || "",
        phaseChanged: script.getAttribute("data-i18n-phase-changed") || "",
        phaseFailed: script.getAttribute("data-i18n-phase-failed") || "",
        latency: script.getAttribute("data-i18n-latency") || "",
        camDenied: script.getAttribute("data-i18n-cam-denied") || "",
        camMissing: script.getAttribute("data-i18n-cam-missing") || "",
        camBusy: script.getAttribute("data-i18n-cam-busy") || "",
        camInsecure: script.getAttribute("data-i18n-cam-insecure") ||
            "Live camera needs the HTTPS portal on this Wi‑Fi (https://<LAN>:5443). Browsers block the camera on plain HTTP.",
        camStart: script.getAttribute("data-i18n-cam-start") || "Start camera",
        camStop: script.getAttribute("data-i18n-cam-stop") || "Stop camera",
        camDefault: script.getAttribute("data-i18n-cam-default") || "Default camera",
        camN: script.getAttribute("data-i18n-cam-n") || "Camera %(n)s",
        live: script.getAttribute("data-i18n-live") || "Live",
        missingItem: script.getAttribute("data-i18n-missing-item") || "%(name)s (−%(n)s)",
        noMissing: script.getAttribute("data-i18n-no-missing") || "No missing families yet."
    };

    var trafficEl = document.getElementById("capture-traffic");
    var trafficMissingEl = document.getElementById("capture-traffic-missing");
    var trafficGridEl = document.getElementById("capture-traffic-grid");
    var trafficGridEmptyEl = document.getElementById("capture-traffic-grid-empty");

    function fill(template, values) {
        return template.replace(/%\((\w+)\)s/g, function (match, name) {
            return values[name] !== undefined ? String(values[name]) : match;
        }).replace(/%%/g, "%");
    }

    function setStageLoading(on, text) {
        if (!stageEl) return;
        stageEl.classList.toggle("is-loading", !!on);
        if (stageLoadingText && text) stageLoadingText.textContent = text;
    }

    function setCameraError(msg) {
        if (!cameraErr) return;
        if (msg) {
            cameraErr.textContent = msg;
            cameraErr.hidden = false;
        } else {
            cameraErr.textContent = "";
            cameraErr.hidden = true;
        }
    }

    function refreshToolbarMeta() {
        if (!toolbarMeta) return;
        var has =
            (statusEl && statusEl.textContent) ||
            (progressEl && progressEl.textContent) ||
            (modelEl && modelEl.textContent) ||
            (latencyEl && latencyEl.textContent);
        toolbarMeta.setAttribute("data-empty", has ? "false" : "true");
    }

    function presenceFor(expected, detected) {
        var exp = Number(expected);
        var det = Number(detected);
        if (isNaN(exp) || isNaN(det)) return "pending";
        if (exp <= 0) return det > 0 ? "partial" : "ok";
        if (det === 0) return "missing";
        if (det === exp) return "ok";
        return "partial";
    }

    function uuid4() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
            var r = Math.random() * 16 | 0;
            var v = c === "x" ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    function applyCategorySummary(summary) {
        summary = summary || {};
        table.querySelectorAll("tr.capture-cat-header").forEach(function (row) {
            var cat = row.getAttribute("data-category-name");
            var meta = summary[cat];
            var metaEl = row.querySelector("[data-cat-meta]");
            var pendEl = row.querySelector("[data-cat-pending]");
            var expected = 0;
            var detected = 0;
            var seen = false;

            if (meta) {
                expected = Number(meta.expected) || 0;
                detected = Number(meta.detected) || 0;
                seen = true;
                if (metaEl) {
                    metaEl.textContent = fill(i18n.catMeta, {
                        expected: expected,
                        detected: detected
                    });
                }
                if (pendEl) {
                    var pending = meta.pending || [];
                    pendEl.textContent = pending.length
                        ? fill(i18n.pending, {items: pending.map(function (p) {
                            return p.family_name + " (−" + p.missing + ")";
                        }).join(", ")})
                        : "";
                }
            } else {
                table.querySelectorAll('tr[data-family-code][data-category-name="' + cat + '"]').forEach(function (fam) {
                    var exp = Number(fam.getAttribute("data-expected-value"));
                    var detAttr = fam.getAttribute("data-detected-value");
                    if (!isNaN(exp)) expected += exp;
                    if (detAttr != null && detAttr !== "") {
                        detected += Number(detAttr) || 0;
                        seen = true;
                    }
                });
                if (metaEl) {
                    metaEl.textContent = seen
                        ? fill(i18n.catMeta, {expected: expected, detected: detected})
                        : "";
                }
                if (pendEl) pendEl.textContent = "";
            }

            row.setAttribute("data-presence", seen ? presenceFor(expected, detected) : "pending");
        });
    }

    function applyState(session, source) {
        if (!session) return;
        var statusBits = [];
        if (liveMode) statusBits.push(i18n.live);
        else if (session.status) statusBits.push(session.status);
        if (session.message) statusBits.push(session.message);
        statusEl.textContent = statusBits.length
            ? fill(i18n.status, {status: statusBits.join(" · ")})
            : "";
        if (typeof session.progress === "number") {
            progressEl.textContent = fill(i18n.progress, {percent: Math.round(session.progress * 100)});
        } else {
            progressEl.textContent = "";
        }
        if (session.model_version) {
            modelEl.textContent = fill(i18n.model, {model: session.model_version});
        }
        if (session.latency_ms != null) {
            latencyEl.textContent = fill(i18n.latency, {
                total: Math.round(Number(session.latency_ms))
            });
        }
        refreshToolbarMeta();
        var skipJpeg = liveMode && source !== "ws";
        if (session.frame_jpeg_b64 && !skipJpeg && session.frames_processed !== lastProcessed) {
            lastProcessed = session.frames_processed;
            img.src = "data:image/jpeg;base64," + session.frame_jpeg_b64;
            img.hidden = false;
            if (placeholder) placeholder.hidden = true;
            /* Keep raw grabber hidden; only the annotated JPEG is visible. */
            if (liveVideo) liveVideo.hidden = true;
            setStageLoading(false);
        }
        var counts = session.counts || {};
        table.querySelectorAll("tr[data-family-code]").forEach(function (row) {
            var code = row.getAttribute("data-family-code");
            var meta = counts[code];
            var det = row.querySelector("[data-detected]");
            var diff = row.querySelector("[data-diff]");
            if (!meta || !det || !diff) return;
            det.textContent = String(meta.detected_quantity);
            diff.textContent = meta.difference;
            det.classList.remove("is-waiting");
            diff.classList.remove("is-waiting");
            row.setAttribute("data-detected-value", meta.detected_quantity);
        });
        applyCategorySummary(session.category_summary);
        updateTotals();
        fileMode = session.capture_source === "file" && session.status === "processing";
        if (stopFileBtn) stopFileBtn.hidden = !fileMode;
        /* Loading overlay only until the first annotated frame arrives. */
        if (img && !img.hidden) {
            setStageLoading(false);
        } else if (fileMode) {
            setStageLoading(true, i18n.processing);
        } else if (!liveMode) {
            setStageLoading(false);
        }
    }

    function trafficLevel(missingTotal, expectedTotal) {
        if (!expectedTotal || missingTotal <= 0) return "ok";
        var ratio = missingTotal / expectedTotal;
        if (ratio >= 0.4) return "danger";
        return "warn";
    }

    function updateTrafficStrip(seen, expectedTotal, missingTotal, missingFamilies) {
        if (!trafficEl) return;
        if (!seen) {
            trafficEl.setAttribute("data-level", "pending");
            if (trafficMissingEl) trafficMissingEl.textContent = "—";
            if (trafficGridEl) {
                trafficGridEl.querySelectorAll("[data-traffic-item]").forEach(function (node) {
                    node.remove();
                });
            }
            if (trafficGridEmptyEl) {
                trafficGridEmptyEl.hidden = false;
                trafficGridEmptyEl.textContent = i18n.noMissing;
            }
            return;
        }
        var level = trafficLevel(missingTotal, expectedTotal);
        trafficEl.setAttribute("data-level", level);
        if (trafficMissingEl) trafficMissingEl.textContent = String(missingTotal);
        if (!trafficGridEl) return;
        trafficGridEl.querySelectorAll("[data-traffic-item]").forEach(function (node) {
            node.remove();
        });
        if (!missingFamilies.length) {
            if (trafficGridEmptyEl) {
                trafficGridEmptyEl.hidden = false;
                trafficGridEmptyEl.textContent = i18n.noMissing;
            }
            return;
        }
        if (trafficGridEmptyEl) trafficGridEmptyEl.hidden = true;
        missingFamilies.forEach(function (item) {
            var chip = document.createElement("span");
            chip.className = "v3-op-capture__traffic-chip";
            chip.setAttribute("data-traffic-item", "1");
            chip.textContent = fill(i18n.missingItem, {name: item.name, n: item.missing});
            trafficGridEl.appendChild(chip);
        });
    }

    function updateTotals() {
        var totalRow = table.querySelector("[data-total-row]");
        var expected = totalRow
            ? Number(totalRow.getAttribute("data-total-expected")) || 0
            : 0;
        var detected = 0;
        var seen = false;
        var missingTotal = 0;
        var missingFamilies = [];
        table.querySelectorAll("tr[data-family-code]").forEach(function (row) {
            var exp = Number(row.getAttribute("data-expected-value"));
            if (isNaN(exp)) exp = 0;
            var detAttr = row.getAttribute("data-detected-value");
            if (detAttr == null || detAttr === "") return;
            var value = Number(detAttr);
            if (isNaN(value)) return;
            detected += value;
            seen = true;
            var missing = Math.max(0, exp - value);
            if (missing > 0) {
                missingTotal += missing;
                var nameEl = row.querySelector("td");
                missingFamilies.push({
                    name: nameEl ? nameEl.textContent.trim() : (row.getAttribute("data-family-code") || ""),
                    missing: missing
                });
            }
        });
        if (totalRow) {
            var detEl = totalRow.querySelector("[data-total-detected]");
            var diffEl = totalRow.querySelector("[data-total-diff]");
            if (!expected) expected = Number(totalRow.getAttribute("data-total-expected")) || 0;
            if (detEl) {
                detEl.textContent = seen ? detected : "—";
                detEl.classList.toggle("is-waiting", !seen);
            }
            if (diffEl) {
                diffEl.textContent = seen ? detected - expected : "—";
                diffEl.classList.toggle("is-waiting", !seen);
            }
        }
        missingFamilies.sort(function (a, b) { return b.missing - a.missing; });
        updateTrafficStrip(seen, expected, missingTotal, missingFamilies);
    }

    function pollDelayMs(session) {
        if (liveMode) return LIVE_POLL_MS;
        if (!session || session.status !== "processing") return IDLE_POLL_MS;
        var lat = Number(session.latency_avg_ms || session.latency_ms || 0);
        if (!lat || lat < 40) return 80;
        var paced = Math.round(lat * 0.85);
        return Math.max(MIN_POLL_MS, Math.min(MAX_BUSY_POLL_MS, paced));
    }

    function scheduleNextPoll(session) {
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, pollDelayMs(session));
    }

    function poll() {
        if (pollInFlight) return;
        pollInFlight = true;
        fetch(stateUrl, { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data && data.session) {
                    lastSession = data.session;
                    applyState(data.session, "poll");
                }
            })
            .catch(function () { /* keep polling */ })
            .finally(function () {
                pollInFlight = false;
                scheduleNextPoll(lastSession);
            });
    }

    function grabJpeg() {
        if (!liveVideo || !liveCanvas || !liveCtx) return null;
        var w = liveVideo.videoWidth;
        var h = liveVideo.videoHeight;
        if (!w || !h) return null;
        var scale = Math.min(1, 640 / w);
        liveCanvas.width = Math.max(1, Math.round(w * scale));
        liveCanvas.height = Math.max(1, Math.round(h * scale));
        liveCtx.drawImage(liveVideo, 0, 0, liveCanvas.width, liveCanvas.height);
        return liveCanvas.toDataURL("image/jpeg", 0.72).split(",")[1] || null;
    }

    function sendLatestFrame() {
        if (!ws || ws.readyState !== 1 || workerBusy || !liveMode) return;
        var jpeg = latestJpeg || grabJpeg();
        latestJpeg = null;
        if (!jpeg) return;
        workerBusy = true;
        ws.send(JSON.stringify({
            type: "frame.submit",
            session_id: sessionId,
            frame_id: uuid4(),
            captured_at: new Date().toISOString(),
            image_base64: jpeg,
            image_content_type: "image/jpeg"
        }));
    }

    function grabLoop() {
        if (!liveMode) return;
        latestJpeg = grabJpeg() || latestJpeg;
        sendLatestFrame();
        grabTimer = setTimeout(grabLoop, paceMs);
    }

    function stopGrabLoop() {
        if (grabTimer) {
            clearTimeout(grabTimer);
            grabTimer = null;
        }
    }

    function closeWs() {
        liveMode = false;
        workerBusy = false;
        stopGrabLoop();
        if (ws) {
            try { ws.close(); } catch (e) { /* ignore */ }
            ws = null;
        }
        if (cameraBtn) cameraBtn.textContent = i18n.camStart;
    }

    function stopTracks() {
        if (mediaStream) {
            mediaStream.getTracks().forEach(function (t) { t.stop(); });
            mediaStream = null;
        }
        if (liveVideo) liveVideo.srcObject = null;
    }

    function handleWsMessage(raw) {
        var msg;
        try { msg = JSON.parse(raw); } catch (e) { return; }
        if (!msg || !msg.type) return;
        if (msg.type === "worker.status") {
            if (msg.state === "busy") workerBusy = true;
            if (msg.state === "ready") {
                workerBusy = false;
                sendLatestFrame();
            }
            return;
        }
        if (msg.type === "detections.result") {
            workerBusy = false;
            var lat = Number(msg.latency_ms || 0);
            paceMs = Math.max(80, Math.round((lat || 180) * 1.1));
            if (msg.session) applyState(msg.session, "ws");
            sendLatestFrame();
            return;
        }
        if (msg.type === "error") {
            workerBusy = false;
            setCameraError(msg.message || msg.code || "");
            if (msg.code === "busy" || msg.code === "unauthorized") {
                /* keep camera; user can retry start */
            }
        }
    }

    function connectWs(ticket, wsUrl) {
        return new Promise(function (resolve, reject) {
            closeWs();
            var sock = new WebSocket(wsUrl);
            sock.onopen = function () {
                sock.send(JSON.stringify({
                    type: "session.bind",
                    session_id: sessionId,
                    auth_token: ticket
                }));
            };
            sock.onmessage = function (ev) {
                var msg;
                try { msg = JSON.parse(ev.data); } catch (e) { return; }
                if (msg && msg.type === "error" && !liveMode) {
                    reject(new Error(msg.message || msg.code || "WS bind failed"));
                    try { sock.close(); } catch (e2) { /* ignore */ }
                    return;
                }
                if (msg && msg.type === "worker.status" && msg.state === "ready" && !liveMode) {
                    ws = sock;
                    liveMode = true;
                    workerBusy = false;
                    if (cameraBtn) cameraBtn.textContent = i18n.camStop;
                    statusEl.textContent = fill(i18n.status, {status: i18n.live});
                    refreshToolbarMeta();
                    setStageLoading(false);
                    grabLoop();
                    resolve();
                    return;
                }
                handleWsMessage(ev.data);
            };
            sock.onerror = function () {
                reject(new Error("WebSocket error"));
            };
            sock.onclose = function () {
                if (ws === sock) closeWs();
            };
            setTimeout(function () {
                if (!liveMode) reject(new Error("WS bind timeout"));
            }, 8000);
        });
    }

    function fetchTicket() {
        return fetch(ticketUrl, { credentials: "same-origin" })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (!res.ok || !res.j.ticket) throw new Error(res.j.error || "ticket");
                return res.j;
            });
    }

    function cameraSecureOk() {
        /* Live camera requires a secure context. Lab path is LAN HTTPS (:5443), not localhost. */
        return !!window.isSecureContext;
    }

    function baseVideoConstraints(deviceId) {
        var video = {
            width: { ideal: 1280 },
            height: { ideal: 720 },
            frameRate: { ideal: 30, max: 30 }
        };
        if (deviceId) video.deviceId = { ideal: deviceId };
        return { video: video, audio: false };
    }

    function listCameras() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return Promise.resolve([]);
        return navigator.mediaDevices.enumerateDevices().then(function (devs) {
            var cams = devs.filter(function (d) { return d.kind === "videoinput"; });
            if (!cameraSelect) return cams;
            var current = cameraSelect.value;
            cameraSelect.innerHTML = "";
            var def = document.createElement("option");
            def.value = "";
            def.textContent = i18n.camDefault;
            cameraSelect.appendChild(def);
            cams.forEach(function (cam, i) {
                var opt = document.createElement("option");
                opt.value = cam.deviceId || "";
                opt.textContent = cam.label || fill(i18n.camN, { n: i + 1 });
                cameraSelect.appendChild(opt);
            });
            if (current) cameraSelect.value = current;
            /* Show the picker whenever at least one device is known (incl. single USB webcam). */
            cameraSelect.hidden = cams.length < 1;
            return cams;
        }).catch(function () { return []; });
    }

    function openUserMedia(constraints) {
        return navigator.mediaDevices.getUserMedia(constraints).catch(function (err) {
            /* Retry without device pin / ideals if the first constraint set is rejected. */
            var pinned = constraints && constraints.video && typeof constraints.video === "object" &&
                constraints.video.deviceId;
            if (pinned || (constraints && constraints.video && constraints.video !== true)) {
                return navigator.mediaDevices.getUserMedia({ video: true, audio: false });
            }
            throw err;
        });
    }

    function startCamera() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setCameraError(i18n.camMissing);
            return Promise.reject(new Error("getUserMedia"));
        }
        if (!cameraSecureOk()) {
            setCameraError(i18n.camInsecure);
            return Promise.reject(new Error("insecure-context"));
        }
        var deviceId = cameraSelect && cameraSelect.value ? cameraSelect.value : "";
        var constraints = baseVideoConstraints(deviceId || null);
        setStageLoading(true, i18n.connecting);
        setCameraError("");
        return postCancel().then(function () {
            return openUserMedia(constraints);
        }).then(function (stream) {
            stopTracks();
            mediaStream = stream;
            liveVideo.srcObject = stream;
            liveVideo.muted = true;
            liveVideo.hidden = true;
            if (img) img.hidden = true;
            return liveVideo.play().catch(function () { /* autoplay policies */ });
        }).then(function () {
            return listCameras();
        }).then(function () {
            setCameraError("");
            return fetchTicket();
        }).then(function (issued) {
            return connectWs(issued.ticket, issued.ws_url);
        }).catch(function (err) {
            setStageLoading(false);
            var name = err && err.name;
            if (err && err.message === "insecure-context") { /* already set */ }
            else if (name === "NotAllowedError" || name === "SecurityError") setCameraError(i18n.camDenied);
            else if (name === "NotFoundError" || name === "OverconstrainedError") setCameraError(i18n.camMissing);
            else if (name === "NotReadableError" || name === "AbortError") setCameraError(i18n.camBusy);
            else setCameraError(String((err && err.message) || err));
            throw err;
        });
    }

    function stopCamera() {
        closeWs();
        stopTracks();
        if (cancelUrl) {
            fetch(cancelUrl, { method: "POST", credentials: "same-origin" }).catch(function () {});
        }
    }

    function postCancel() {
        return fetch(cancelUrl, { method: "POST", credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .catch(function () { return { ok: false }; });
    }

    if (cameraBtn) {
        cameraBtn.addEventListener("click", function () {
            if (liveMode) {
                stopCamera();
                statusEl.textContent = "";
                refreshToolbarMeta();
                return;
            }
            cameraBtn.disabled = true;
            cameraBtn.setAttribute("aria-busy", "true");
            startCamera()
                .catch(function () { /* error already shown */ })
                .finally(function () {
                    cameraBtn.disabled = false;
                    cameraBtn.removeAttribute("aria-busy");
                });
        });
    }

    if (cameraSelect) {
        cameraSelect.addEventListener("change", function () {
            if (!mediaStream && !liveMode) return;
            startCamera().catch(function () {});
        });
    }

    if (stopFileBtn) {
        stopFileBtn.addEventListener("click", function () {
            postCancel().then(function () {
                fileMode = false;
                stopFileBtn.hidden = true;
                startCamera().catch(function () {});
            });
        });
    }

    uploadForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var fileInput = document.getElementById("capture-video");
        if (!fileInput.files || !fileInput.files[0]) return;
        var fd = new FormData();
        fd.append("video", fileInput.files[0]);
        uploadBtn.disabled = true;
        uploadBtn.setAttribute("aria-busy", "true");
        statusEl.textContent = fill(i18n.status, {status: i18n.uploading});
        refreshToolbarMeta();
        setStageLoading(true, i18n.uploading);
        closeWs();
        stopTracks();
        /* Drop the last live frame so a failed/skipped poll cannot leave a frozen camera still. */
        lastProcessed = -1;
        if (img) {
            img.removeAttribute("src");
            img.hidden = true;
        }
        if (placeholder) placeholder.hidden = false;
        postCancel().then(function () {
            return fetch(uploadUrl, { method: "POST", body: fd, credentials: "same-origin" });
        }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (!res.ok) {
                    statusEl.textContent = fill(i18n.status, {
                        status: fill(i18n.error, {detail: res.j.error || i18n.uploadFailed})
                    });
                    refreshToolbarMeta();
                    setStageLoading(false);
                    return;
                }
                liveMode = false;
                fileMode = true;
                if (stopFileBtn) stopFileBtn.hidden = false;
                statusEl.textContent = fill(i18n.status, {status: i18n.processing});
                refreshToolbarMeta();
                setStageLoading(true, i18n.processing);
                if (res.j.session) {
                    lastSession = res.j.session;
                    applyState(res.j.session, "poll");
                }
                if (pollTimer) clearTimeout(pollTimer);
                poll();
            })
            .catch(function (err) {
                statusEl.textContent = fill(i18n.status, {status: fill(i18n.error, {detail: err})});
                refreshToolbarMeta();
                setStageLoading(false);
            })
            .finally(function () {
                uploadBtn.disabled = false;
                uploadBtn.removeAttribute("aria-busy");
            });
    });

    phaseForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var submitBtn = phaseForm.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.setAttribute("aria-busy", "true");
        }
        var fd = new FormData(phaseForm);
        fetch(phaseUrl, { method: "POST", body: fd, credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ok) {
                    var label = data.phase_name || data.phase_code || "";
                    phaseMsg.textContent = fill(i18n.phaseChanged, {phase: label || data.phase_code});
                    if (phaseCurrentName && label) {
                        phaseCurrentName.textContent = label;
                    }
                    if (phaseSelect && data.phase_code) {
                        phaseSelect.value = data.phase_code;
                    }
                } else {
                    phaseMsg.textContent = data.error || i18n.phaseFailed;
                }
            })
            .catch(function (err) { phaseMsg.textContent = String(err); })
            .finally(function () {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.removeAttribute("aria-busy");
                }
            });
    });

    function shutdown() {
        closeWs();
        stopTracks();
        if (pollTimer) clearTimeout(pollTimer);
    }

    poll();
    if (!cameraSecureOk()) {
        setCameraError(i18n.camInsecure);
    } else {
        listCameras();
    }
    window.addEventListener("pagehide", shutdown);
    window.addEventListener("beforeunload", shutdown);
})();
