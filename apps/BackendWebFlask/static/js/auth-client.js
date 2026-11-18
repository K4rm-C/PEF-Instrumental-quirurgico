document.addEventListener('click', async (event) => {
    const signOutLink = event.target.closest('[data-auth-logout]');
    if (!signOutLink) return;

    event.preventDefault();
    const confirmMessage = signOutLink.getAttribute('data-app-confirm-link');
    if (confirmMessage && window.appConfirm) {
        const ok = await window.appConfirm({
            title: signOutLink.getAttribute('data-app-confirm-title') || (window.pefT ? window.pefT('confirm') : 'Confirm'),
            message: confirmMessage,
            okLabel: window.pefT ? window.pefT('continue') : 'Continue',
            cancelLabel: window.pefT ? window.pefT('cancel') : 'Cancel',
        });
        if (!ok) return;
    }
    const authUrl = document.body.dataset.authServiceUrl;
    try {
        await fetch(`${authUrl}/logout`, {
            method: 'POST',
            credentials: 'include',
        });
    } catch {}
    window.location.assign(signOutLink.href);
});