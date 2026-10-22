document.addEventListener('DOMContentLoaded', async () => {
    if (getToken()) {
        window.location.href = '/dashboard.html';
        return;
    }
    // An expired access token may still have a valid refresh token; try it
    // before showing the sign-in form again.
    if (getRefreshToken() && await tryRefreshToken()) {
        window.location.href = '/dashboard.html';
        return;
    }

    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');
    const loginError = document.getElementById('login-error');
    const registerError = document.getElementById('register-error');
    const registerSuccess = document.getElementById('register-success');

    const params = new URLSearchParams(window.location.search);
    const next = params.get('next');
    const nextUrl = next && next.startsWith('/') && !next.startsWith('//') ? next : '/dashboard.html';

    function showError(el, message) {
        el.textContent = message;
        el.style.display = 'block';
    }

    function hideError(el) {
        el.style.display = 'none';
        el.textContent = '';
    }

    function setBusy(button, busy, label) {
        button.disabled = busy;
        if (busy) {
            button.dataset.label = button.textContent;
            button.textContent = label;
        } else if (button.dataset.label) {
            button.textContent = button.dataset.label;
            delete button.dataset.label;
        }
    }

    document.getElementById('show-register').addEventListener('click', (e) => {
        e.preventDefault();
        loginForm.style.display = 'none';
        registerForm.style.display = 'block';
        hideError(loginError);
        document.getElementById('reg-fullname').focus();
    });

    document.getElementById('show-login').addEventListener('click', (e) => {
        e.preventDefault();
        registerForm.style.display = 'none';
        loginForm.style.display = 'block';
        hideError(registerError);
        registerSuccess.style.display = 'none';
        document.getElementById('login-username').focus();
    });

    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const username = document.getElementById('login-username').value.trim();
        const password = document.getElementById('login-password').value;
        const button = document.getElementById('login-btn');
        hideError(loginError);

        if (!username || !password) {
            showError(loginError, 'Please fill in all fields');
            return;
        }

        setBusy(button, true, 'Signing in…');
        try {
            const formData = new URLSearchParams();
            formData.append('username', username);
            formData.append('password', password);

            const res = await apiFetch('/auth/token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: formData.toString(),
            });
            setTokens(res);
            window.location.href = nextUrl;
        } catch (err) {
            showError(loginError, err.message);
        } finally {
            setBusy(button, false);
        }
    });

    registerForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fullname = document.getElementById('reg-fullname').value.trim();
        const email = document.getElementById('reg-email').value.trim();
        const username = document.getElementById('reg-username').value.trim();
        const password = document.getElementById('reg-password').value;
        const button = document.getElementById('register-btn');

        hideError(registerError);
        registerSuccess.style.display = 'none';

        if (!fullname || !email || !username || !password) {
            showError(registerError, 'Please fill in all fields');
            return;
        }
        if (password.length < 8) {
            showError(registerError, 'Password must be at least 8 characters');
            return;
        }

        setBusy(button, true, 'Creating account…');
        try {
            await apiFetch('/auth/', {
                method: 'POST',
                body: JSON.stringify({
                    full_name: fullname,
                    email,
                    username,
                    password,
                }),
            });

            registerSuccess.style.display = 'block';
            document.getElementById('login-username').value = username;
            setTimeout(() => {
                registerForm.style.display = 'none';
                loginForm.style.display = 'block';
                document.getElementById('login-password').focus();
                toast('Account created — sign in to continue', 'success');
            }, 900);
        } catch (err) {
            showError(registerError, err.message);
        } finally {
            setBusy(button, false);
        }
    });

    ['login-username', 'login-password'].forEach(id => {
        document.getElementById(id).addEventListener('input', () => hideError(loginError));
    });
    ['reg-fullname', 'reg-email', 'reg-username', 'reg-password'].forEach(id => {
        document.getElementById(id).addEventListener('input', () => hideError(registerError));
    });
});
