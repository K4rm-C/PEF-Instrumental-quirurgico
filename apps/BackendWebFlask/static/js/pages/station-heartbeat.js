/* MS-05: station heartbeat and camera choice of the Operator's capture page.
   - Heartbeat every 30 s while the page is open: the portal relays it to MonitoringService for the session's
     own station (the station is never chosen by the browser). The camera state comes from the live preview
     (window.pefCameraState, set by capture-live-ws.js): "ok" only means the browser opened the camera;
     MonitoringService marks it working only when VisionWorker itself receives its frames.
   - Choosing the camera stores it on the session (audited). Nothing here touches counting. */
(function () {
    "use strict";
    var panel = document.getElementById("capture-station");
    if (!panel) return;
    var heartbeatUrl = panel.dataset.heartbeatUrl, cameraUrl = panel.dataset.cameraUrl;
    var select = document.getElementById("capture-station-camera");
    var status = document.getElementById("capture-station-status");
    var INTERVAL_MS = 30000;

    function post(url, body) {
        return fetch(url, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
                           body: JSON.stringify(body)}).then(function (r) { return r.json(); });
    }

    function beat() {
        var state = window.pefCameraState || {status: "unknown"};
        post(heartbeatUrl, {camera_status: state.status, camera_error: state.error || null})
            .then(function (data) {
                if (!status) return;
                status.textContent = data && data.recorded
                    ? (data.camera_status ? "Station online · camera: " + data.camera_status : "Station online")
                    : "Station status not recorded (" + ((data && data.reason) || "monitoring unavailable") + ")";
            })
            .catch(function () { /* monitoring must never disturb the capture */ });
    }

    if (select) {
        select.addEventListener("change", function () {
            if (!select.value) return;
            post(cameraUrl, {camera_id: select.value}).then(function (data) {
                if (status) status.textContent = data.ok ? "Camera: " + data.camera_name : (data.error || "");
                if (data.ok) beat();
            });
        });
    }
    beat();
    var timer = setInterval(beat, INTERVAL_MS);
    window.addEventListener("pagehide", function () { clearInterval(timer); });
})();
