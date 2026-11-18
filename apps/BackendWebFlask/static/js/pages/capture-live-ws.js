/* ZIP3 WS (session.bind, frame.submit, detections.result) adapted to V1's strict file-count workflow.
   Preview is NON-AUTHORITATIVE: never calls /capture/initial, /final or /finish. */
(function () {
    "use strict";
    var cfg = document.querySelector('script[src*="capture-live-ws.js"]');
    if (!cfg) return;
    var ticketUrl = cfg.dataset.ticketUrl, sessionId = cfg.dataset.sessionId;
    var start = document.getElementById('capture-live-start');
    var stop = document.getElementById('capture-live-stop');
    var select = document.getElementById('capture-live-select');
    var raw = document.getElementById('capture-live-raw');
    var canvas = document.getElementById('capture-live-canvas');
    var output = document.getElementById('capture-live-result');
    var placeholder = document.getElementById('capture-live-placeholder');
    var status = document.getElementById('capture-live-status');
    if (!start || !stop || !raw || !canvas) return;
    var ctx = canvas.getContext('2d');
    var renewAfterMs = 40000;
    var stream = null, socket = null, active = false, awaiting = false;
    var loop = null, pace = 200, pendingBind = false, renew = null;
    var AUTH_CODES = {unauthorized: 1, revoked: 1, session_expired: 1};
    // MS-11 (R09): automatic reconnection after an unexpected loss of the WebSocket (worker restart, network).
    // Never after an authorization problem (revoked / expired / unauthorized) or when the Operator stops.
    var RECONNECT_MAX = 6, STALL_MS = 10000;
    var reconnectAttempts = 0, reconnectTimer = null, sentAt = 0;
    // MS-05: what the browser knows about the camera, read by station-heartbeat.js (a report only).
    function cameraState(status, error) { window.pefCameraState = {status: status, error: error || null}; }
    cameraState('unknown');
    function setStatus(value) { if (status) status.textContent = value; }
    function cleanup() {
        active = false; pendingBind = false; awaiting = false;
        reconnectAttempts = 0;
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        if (loop) { clearTimeout(loop); loop = null; }
        if (renew) { clearInterval(renew); renew = null; }
        if (socket) {
            var ws = socket; socket = null;
            try {
                if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:'session.cancel'}));
                ws.close();
            } catch (err) { /* client already disconnected */ }
        }
        if (stream) { stream.getTracks().forEach(function (track) { track.stop(); }); stream = null; }
        raw.srcObject = null;
        start.disabled = false;
        stop.disabled = true;
    }
    function schedule() {
        if (active) loop = setTimeout(sendFrame, pace);
    }
    function dropSocket() {
        // Closes the current socket WITHOUT stopping the camera; onclose then decides whether to reconnect.
        var ws = socket;
        if (!ws) return;
        try { ws.close(); } catch (err) { /* already closed */ }
    }
    function scheduleReconnect() {
        active = false; pendingBind = false; awaiting = false;
        if (loop) { clearTimeout(loop); loop = null; }
        if (renew) { clearInterval(renew); renew = null; }
        socket = null;
        if (!stream) { cleanup(); setStatus('WebSocket disconnected'); return; }
        reconnectAttempts += 1;
        if (reconnectAttempts > RECONNECT_MAX) {
            cleanup();
            setStatus('Live preview lost: the connection could not be restored. Start the camera again.');
            return;
        }
        var delay = Math.min(15000, 1000 * Math.pow(2, reconnectAttempts - 1));
        setStatus('Connection lost; reconnecting in ' + Math.round(delay / 1000) + ' s (attempt ' +
            reconnectAttempts + '/' + RECONNECT_MAX + ')');
        reconnectTimer = setTimeout(function () { reconnectTimer = null; connect(); }, delay);
    }
    function sendFrame() {
        loop = null;
        if (active && awaiting && sentAt && Date.now() - sentAt > STALL_MS) {
            // The socket is open but the worker stopped answering: treat it as a lost connection.
            setStatus('No answer from the vision worker; reconnecting...');
            dropSocket(); return;
        }
        if (!active || awaiting || !socket || socket.readyState !== WebSocket.OPEN) {
            schedule(); return;
        }
        if (!raw.videoWidth || !raw.videoHeight) { schedule(); return; }
        var w = Math.min(960, raw.videoWidth), h = Math.max(1, Math.round(raw.videoHeight * w / raw.videoWidth));
        canvas.width = w; canvas.height = h;
        ctx.drawImage(raw, 0, 0, w, h);
        awaiting = true;
        sentAt = Date.now();
        try {
            socket.send(JSON.stringify({type:'frame.submit', session_id:sessionId,
                frame_id: String(Date.now()), captured_at:new Date().toISOString(),
                image_base64:canvas.toDataURL('image/jpeg', 0.72).split(',')[1]}));
        } catch (err) { awaiting = false; setStatus(String(err.message || err)); }
        schedule();
    }
    function authLost() {
        // MS-03: the server closed or refused the live connection; stop sending frames, keep the page usable.
        cleanup();
        setStatus('Live preview stopped: your authorization is no longer valid. Sign in again to continue.');
    }
    async function renewTicket() {
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        try {
            var response = await fetch(ticketUrl, {credentials:'same-origin', redirect:'manual'});
            if (!response.ok) { authLost(); return; }
            var ticket = await response.json();
            if (!ticket.ticket) { authLost(); return; }
            socket.send(JSON.stringify({type:'session.renew', session_id:sessionId, auth_token:ticket.ticket}));
        } catch (err) { /* transient network error: the worker closes the socket if the ticket expires */ }
    }
    function handleMessage(payload) {
        var msg;
        try { msg = JSON.parse(payload); } catch (err) { return; }
        if (msg.type === 'error' && AUTH_CODES[msg.code]) { authLost(); return; }
        if (msg.type === 'error') {
            awaiting = false;
            setStatus('WebSocket: ' + (msg.message || msg.code || 'error'));
            if (pendingBind) cleanup();
            return;
        }
        if (msg.type === 'worker.status' && msg.state === 'ready') {
            awaiting = false;
            if (pendingBind) {
                pendingBind = false; active = true;
                if (reconnectAttempts) setStatus('Live preview reconnected');
                reconnectAttempts = 0;
                start.disabled = true; stop.disabled = false;
                if (!renew) renew = setInterval(renewTicket, renewAfterMs);
                sendFrame();
            }
            return;
        }
        if (msg.type === 'detections.result') {
            awaiting = false;
            var session = msg.session || {};
            if (session.frame_jpeg_b64) {
                output.src = 'data:image/jpeg;base64,' + session.frame_jpeg_b64;
                output.hidden = false;
                if (placeholder) placeholder.hidden = true;
            }
            if (msg.latency_ms) pace = Math.max(120, Math.round(Number(msg.latency_ms) * 1.15));
            var total = (msg.counts_by_family || []).reduce(function (n, r) {
                return n + (Number(r.detected_quantity) || 0);
            }, 0);
            setStatus('Live preview · detections: ' + total + ' · latency: ' +
                (msg.latency_ms == null ? '—' : msg.latency_ms + ' ms') +
                ' · not registered as a final count');
        }
    }
    async function enumerate() {
        if (!select || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
        try {
            var devices = await navigator.mediaDevices.enumerateDevices();
            var cameras = devices.filter(function (d) { return d.kind === 'videoinput'; });
            var current = select.value;
            select.innerHTML = '';
            var opt = document.createElement('option'); opt.value = ''; opt.textContent = 'Default camera';
            select.appendChild(opt);
            cameras.forEach(function (d, i) {
                var item = document.createElement('option');
                item.value = d.deviceId; item.textContent = d.label || ('Camera ' + (i + 1));
                select.appendChild(item);
            });
            if (current && cameras.some(function (d) {return d.deviceId === current;})) select.value = current;
        } catch (err) { /* camera permission or unsupported environment */ }
    }
    async function startCamera() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus('Camera requires localhost or HTTPS with browser permissions'); return;
        }
        start.disabled = true;
        try {
            var constraints = {audio:false, video:select && select.value ? {deviceId:{exact:select.value}} : true};
            try {
                stream = await navigator.mediaDevices.getUserMedia(constraints);
            } catch (cameraErr) {
                cameraState('error', cameraErr && cameraErr.name ? cameraErr.name : 'camera_error');
                throw cameraErr;
            }
            cameraState('ok');
            stream.getVideoTracks().forEach(function (track) {
                track.addEventListener('ended', function () { cameraState('unavailable', 'track_ended'); });
            });
            raw.srcObject = stream;
            await raw.play();
            await enumerate();
            await connect();
        } catch (err) { cleanup(); setStatus(String(err.message || err)); }
    }
    async function connect() {
        // A fresh ticket on every (re)connection: an expired or revoked user gets none and the preview stops.
        var response;
        try {
            response = await fetch(ticketUrl + (ticketUrl.indexOf('?') < 0 ? '?' : '&') + 'bind=1',
                                   {credentials:'same-origin', redirect:'manual'});
        } catch (err) { scheduleReconnect(); return; }
        if (response.status === 401 || response.status === 403 || response.type === 'opaqueredirect') { authLost(); return; }
        var ticket = await response.json().catch(function () { return {}; });
        if (!response.ok || !ticket.ticket || !ticket.ws_url) {
            if (response.status >= 500) { scheduleReconnect(); return; }
            throw Error(ticket.error || 'Ticket unavailable');
        }
        renewAfterMs = Math.max(10, Number(ticket.renew_after_s) || 40) * 1000;
        pendingBind = true;
        var ws = new WebSocket(ticket.ws_url);
        socket = ws;
        ws.onopen = function () {
            ws.send(JSON.stringify({type:'session.bind', session_id:sessionId, auth_token:ticket.ticket}));
        };
        ws.onmessage = function (event) { handleMessage(event.data); };
        ws.onerror = function () { setStatus('WebSocket connection failed'); };
        ws.onclose = function () {
            // socket === ws only for an UNEXPECTED close: cleanup()/authLost() detach the socket first.
            if (socket === ws) { scheduleReconnect(); }
        };
        setStatus(reconnectAttempts ? 'Reconnecting the live preview...' : 'Camera connected; authenticating WebSocket...');
    }
    start.addEventListener('click', startCamera);
    stop.addEventListener('click', function () { cleanup(); cameraState('unknown'); setStatus('Camera stopped'); });
    window.addEventListener('pagehide', cleanup);
    enumerate();
}());
