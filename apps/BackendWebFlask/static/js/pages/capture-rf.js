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
    var statusEl = document.getElementById("capture-status");
    var progressEl = document.getElementById("capture-progress");
    var modelEl = document.getElementById("capture-model");
    var latencyEl = document.getElementById("capture-latency");
    var table = document.getElementById("capture-counts-table");
    var uploadForm = document.getElementById("capture-upload-form");
    var uploadBtn = document.getElementById("capture-upload-btn");
    var phaseForm = document.getElementById("capture-phase-form");
    var phaseMsg = document.getElementById("capture-phase-msg");
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
        error: script.getAttribute("data-i18n-error") || "",
        uploadFailed: script.getAttribute("data-i18n-upload-failed") || "",
        phaseChanged: script.getAttribute("data-i18n-phase-changed") || "",
        phaseFailed: script.getAttribute("data-i18n-phase-failed") || "",
        latency: script.getAttribute("data-i18n-latency") || "",
        camDenied: script.getAttribute("data-i18n-cam-denied") || "",
        camMissing: script.getAttribute("data-i18n-cam-missing") || "",
        camBusy: script.getAttribute("data-i18n-cam-busy") || "",
        camStart: script.getAttribute("data-i18n-cam-start") || "Start camera",
        camStop: script.getAttribute("data-i18n-cam-stop") || "Stop camera",
        live: script.getAttribute("data-i18n-live") || "live camera"
    };

    function fill(template, values) {
        return template.replace(/%\((\w+)\)s/g, function (match, name) {
            return values[name] !== undefined ? String(values[name]) : match;
        }).replace(/%%/g, "%");
    }

    function fmt(n) {
        return n == null ? "—" : Number(n).toFixed(2);
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
            if (!metaEl) return;
            if (!meta) {
                metaEl.textContent = "";
                if (pendEl) pendEl.textContent = "";
                return;
            }
            metaEl.textContent = fill(i18n.catMeta, {
                expected: meta.expected, named: meta.named,
                unnamed: meta.unnamed, detected: meta.detected
            });
            if (pendEl) {
                var pending = meta.pending || [];
                if (!pending.length) {
                    pendEl.textContent = "";
                } else {
                    pendEl.textContent = fill(i18n.pending, {items: pending.map(function (p) {
                        return p.family_name + " (−" + p.missing + ")";
                    }).join(", ")});
                }
            }
        });
    }

    function applyState(session, source) {
        if (!session) return;
        statusEl.textContent = fill(i18n.status, {status: (session.status || "—") + (session.message ? " · " + session.message : "")});
        if (typeof session.progress === "number") {
            progressEl.textContent = fill(i18n.progress, {percent: Math.round(session.progress * 100)});
        }
        if (session.model_version) {
            modelEl.textContent = fill(i18n.model, {model: session.model_version
                + (session.pipeline ? " · " + session.pipeline : "")
                + (session.assign_solver ? " / " + session.assign_solver : "")
                + (session.model_device ? " · " + session.model_device : "")});
        }
        if (session.latency_ms != null) {
            latencyEl.textContent = fill(i18n.latency, {
                total: Math.round(Number(session.latency_ms)),
                yolo: Math.round(Number(session.last_yolo_ms || 0)),
                post: Math.round(Number(session.last_post_ms || 0)),
                avg: Math.round(Number(session.latency_avg_ms || session.latency_ms)),
                max: Math.round(Number(session.latency_max_ms || session.latency_ms)),
                n: session.latency_n || 1
            });
        }
        var skipJpeg = liveMode && source !== "ws";
        if (session.frame_jpeg_b64 && !skipJpeg && session.frames_processed !== lastProcessed) {
            lastProcessed = session.frames_processed;
            img.src = "data:image/jpeg;base64," + session.frame_jpeg_b64;
            img.hidden = false;
            placeholder.hidden = true;
        }
        var counts = session.counts || {};
        table.querySelectorAll("tr[data-family-code]").forEach(function (row) {
            var code = row.getAttribute("data-family-code");
            var meta = counts[code];
            var det = row.querySelector("[data-detected]");
            var diff = row.querySelector("[data-diff]");
            var best = row.querySelector("[data-best]");
            var weak = row.querySelector("[data-weak]");
            var conf = row.querySelector("[data-conf]");
            if (!meta) return;
            det.textContent = String(meta.detected_quantity);
            diff.textContent = meta.difference;
            best.textContent = fmt(meta.best_confidence);
            weak.textContent = fmt(meta.weakest_confidence);
            conf.textContent = fmt(meta.avg_confidence);
            row.setAttribute("data-detected-value", meta.detected_quantity);
        });
        applyCategorySummary(session.category_summary);
        updateTotals();
        fileMode = session.capture_source === "file" && session.status === "processing";
        if (stopFileBtn) stopFileBtn.hidden = !fileMode;
    }

    function updateTotals() {
        var totalRow = table.querySelector("[data-total-row]");
        if (!totalRow) return;
        var detected = 0;
        var seen = false;
        table.querySelectorAll("tr[data-family-code][data-detected-value]").forEach(function (row) {
            var value = Number(row.getAttribute("data-detected-value"));
            if (!isNaN(value)) {
                detected += value;
                seen = true;
            }
        });
        var expected = Number(totalRow.getAttribute("data-total-expected")) || 0;
        totalRow.querySelector("[data-total-detected]").textContent = seen ? detected : "—";
        totalRow.querySelector("[data-total-diff]").textContent = seen ? detected - expected : "—";
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
            if (cameraErr) cameraErr.textContent = msg.message || msg.code || "";
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

    function listCameras() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
        navigator.mediaDevices.enumerateDevices().then(function (devs) {
            var cams = devs.filter(function (d) { return d.kind === "videoinput"; });
            if (!cameraSelect) return;
            var current = cameraSelect.value;
            cameraSelect.innerHTML = "";
            var def = document.createElement("option");
            def.value = "";
            def.textContent = i18n.camStart === "Start camera" ? "Default camera" : "Default camera";
            cameraSelect.appendChild(def);
            cams.forEach(function (cam, i) {
                var opt = document.createElement("option");
                opt.value = cam.deviceId;
                opt.textContent = cam.label || ("Camera " + (i + 1));
                cameraSelect.appendChild(opt);
            });
            if (current) cameraSelect.value = current;
            cameraSelect.hidden = cams.length < 2;
        }).catch(function () { /* ignore */ });
    }

    function startCamera() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            if (cameraErr) cameraErr.textContent = i18n.camMissing;
            return Promise.reject(new Error("getUserMedia"));
        }
        var constraints = { video: true, audio: false };
        if (cameraSelect && cameraSelect.value) {
            constraints.video = { deviceId: { exact: cameraSelect.value } };
        }
        return postCancel().then(function () {
            return navigator.mediaDevices.getUserMedia(constraints);
        }).then(function (stream) {
            stopTracks();
            mediaStream = stream;
            liveVideo.srcObject = stream;
            liveVideo.muted = true;
            return liveVideo.play().catch(function () { /* autoplay policies */ });
        }).then(function () {
            listCameras();
            if (cameraErr) cameraErr.textContent = "";
            return fetchTicket();
        }).then(function (issued) {
            return connectWs(issued.ticket, issued.ws_url);
        }).catch(function (err) {
            var name = err && err.name;
            if (name === "NotAllowedError") cameraErr.textContent = i18n.camDenied;
            else if (name === "NotFoundError") cameraErr.textContent = i18n.camMissing;
            else if (name === "NotReadableError") cameraErr.textContent = i18n.camBusy;
            else if (cameraErr) cameraErr.textContent = String(err.message || err);
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
                return;
            }
            cameraBtn.disabled = true;
            startCamera().finally(function () { cameraBtn.disabled = false; });
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
        statusEl.textContent = fill(i18n.status, {status: i18n.uploading});
        closeWs();
        postCancel().then(function () {
            return fetch(uploadUrl, { method: "POST", body: fd, credentials: "same-origin" });
        }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (!res.ok) {
                    statusEl.textContent = fill(i18n.status, {status: fill(i18n.error, {detail: res.j.error || i18n.uploadFailed})});
                    return;
                }
                liveMode = false;
                fileMode = true;
                if (stopFileBtn) stopFileBtn.hidden = false;
                statusEl.textContent = fill(i18n.status, {status: i18n.processing});
                if (res.j.session) {
                    lastSession = res.j.session;
                    applyState(res.j.session, "poll");
                }
                if (pollTimer) clearTimeout(pollTimer);
                poll();
            })
            .catch(function (err) {
                statusEl.textContent = fill(i18n.status, {status: fill(i18n.error, {detail: err})});
            })
            .finally(function () { uploadBtn.disabled = false; });
    });

    phaseForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var fd = new FormData(phaseForm);
        fetch(phaseUrl, { method: "POST", body: fd, credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                phaseMsg.textContent = data.ok
                    ? fill(i18n.phaseChanged, {phase: data.phase_code})
                    : (data.error || i18n.phaseFailed);
            })
            .catch(function (err) { phaseMsg.textContent = String(err); });
    });

    function shutdown() {
        closeWs();
        stopTracks();
        if (pollTimer) clearTimeout(pollTimer);
    }

    poll();
    window.addEventListener("pagehide", shutdown);
    window.addEventListener("beforeunload", shutdown);
})();
