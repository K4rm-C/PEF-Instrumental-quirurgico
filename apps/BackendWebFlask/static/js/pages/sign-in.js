const signInForm = document.querySelector('#sign-in-form');

if (signInForm) {
    const errorMessage = document.querySelector('#sign-in-error');
    const homeByRole = {
        // RF: station operator lands on session list, not metrics dashboard.
        station_operator: {
            url: signInForm.dataset.operatorHome || '/operator/sessions',
            prefix: '/operator/',
        },
        spd_supervisor: {
            url: signInForm.dataset.supervisorHome,
            prefix: '/supervisor/',
        },
        it_admin: {
            url: signInForm.dataset.adminHome,
            prefix: '/admin/',
        },
    };

    const t = (key) => (window.pefT ? window.pefT(key) : key);
    // The auth service keeps English API messages; show the localized UI text instead.
    const authErrorKeys = {
        'Invalid credentials': 'authInvalidCredentials',
        'Too many failed login attempts. Try again later.': 'authTooManyAttempts',
        'Session store unavailable': 'authSessionStoreUnavailable',
        'Database unavailable': 'authDatabaseUnavailable',
        'Session expired; sign in again.': 'authSessionExpired',
    };
    const authErrorMessage = (apiMessage) => t(authErrorKeys[apiMessage] || 'signInFailed');

    const safeNextUrl = (value, role) => {
        if (!value) return role.url;
        try {
            const target = new URL(value, window.location.origin);
            if (target.origin === window.location.origin && target.pathname.startsWith(role.prefix)) {
                return `${target.pathname}${target.search}${target.hash}`;
            }
        } catch {
            return role.url;
        }
        return role.url;
    };

    signInForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        errorMessage.hidden = true;

        const authUrl = document.body.dataset.authServiceUrl;
        const formData = new FormData(signInForm);
        try {
            const response = await fetch(`${authUrl}/login`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    email: formData.get('institutional_email'),
                    password: formData.get('password'),
                }),
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(authErrorMessage(payload.error));
            }

            const role = payload.user?.roles?.map(({ code }) => homeByRole[code]).find(Boolean);
            if (!role) {
                throw new Error(t('noApplicationRole'));
            }

            // DS01: after login the account preference wins over the guest pef_locale cookie.
            const locale = payload.user?.ui_preferences?.locale;
            if (locale === 'en' || locale === 'es-MX') {
                document.cookie = `pef_locale=${locale}; Path=/; Max-Age=31536000; SameSite=Lax`;
            }

            const requestedNext = formData.get('next');
            window.location.assign(safeNextUrl(requestedNext, role));
        } catch (error) {
            errorMessage.textContent = error instanceof TypeError
                ? t('authServiceUnavailable')
                : error.message;
            errorMessage.hidden = false;
        }
    });
}