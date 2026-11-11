document.addEventListener('click', async (event) => {
    const signOutLink = event.target.closest('[data-auth-logout]');
    if (!signOutLink) return;

    event.preventDefault();
    const authUrl = document.body.dataset.authServiceUrl;
    try {
        await fetch(`${authUrl}/logout`, {
            method: 'POST',
            credentials: 'include',
        });
    } catch {}
    window.location.assign(signOutLink.href);
});