const signInForm = document.querySelector('#sign-in-form');

if (signInForm) {
    const errorMessage = document.querySelector('#sign-in-error');
    const homeByRole = {
        operator_cde: {
            url: signInForm.dataset.operatorHome,
            prefix: '/operator/',
        },
        supervisor_quality: {
            url: signInForm.dataset.supervisorHome,
            prefix: '/supervisor/',
        },
        it_admin: {
            url: signInForm.dataset.adminHome,
            prefix: '/admin/',
        },
    };

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
                throw new Error(payload.error || 'Sign in failed. Please try again.');
            }

            const role = payload.user?.roles?.map(({ code }) => homeByRole[code]).find(Boolean);
            if (!role) {
                throw new Error('Your account has no application role.');
            }

            const requestedNext = formData.get('next');
            window.location.assign(safeNextUrl(requestedNext, role));
        } catch (error) {
            errorMessage.textContent = error instanceof TypeError
                ? 'Authentication service unavailable.'
                : error.message;
            errorMessage.hidden = false;
        }
    });
}