/**
 * Transparent JWT renewal.
 * - Silent renew (touch=0): keeps access alive without resetting SPD idle.
 * - After user activity: next renew uses touch=1 so idle clock resets.
 * OP idle is skipped server-side while work_session is in_progress.
 */
(function () {
    const authUrl = document.body && document.body.dataset.authServiceUrl;
    if (!authUrl) return;
    // Guest/sign-in/landing: no session yet — skip renew (avoids noisy 401s).
    const bodyClass = (document.body && document.body.className) || '';
    if (/\bguest-page\b|\bpublic-page\b/.test(bodyClass)) return;

    const INTERVAL_MS = 8 * 60 * 1000;
    let dirty = true; // treat load as activity

    function markActivity() {
        dirty = true;
    }

    ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach((eventName) => {
        window.addEventListener(eventName, markActivity, { passive: true });
    });

    async function renew() {
        const touch = dirty ? '1' : '0';
        dirty = false;
        try {
            const response = await fetch(`${authUrl}/refresh?touch=${touch}`, {
                method: 'POST',
                credentials: 'include',
            });
            // 401 without a session is expected only if cookies were cleared; ignore body noise.
            if (response.status === 401) return;
        } catch (_) {
            /* ignore transient network errors */
        }
    }

    setTimeout(renew, 5000);
    setInterval(renew, INTERVAL_MS);
})();
