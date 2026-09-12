document.addEventListener('DOMContentLoaded', () => {
    // If already logged in, go straight to the workspace
    if (getToken()) {
        window.location.href = '/dashboard.html';
        return;
    }

    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');

    function showError(el, message) {
        el.textContent = message;
        el.style.display = 'inline-flex';
    }
    function hideError(el) {
        el.style.display = 'none';
    }

    // Toggle forms
    document.getElementById('show-register').addEventListener('click', (e) => {
        e.preventDefault();
        loginForm.style.display = 'none';
        registerForm.style.display = 'block';
    });

    document.getElementById('show-login').addEventListener('click', (e) => {
        e.preventDefault();
        registerForm.style.display = 'none';
        loginForm.style.display = 'block';
    });

    // Login
    document.getElementById('login-btn').addEventListener('click', async () => {
        const username = document.getElementById('login-username').value.trim();
        const password = document.getElementById('login-password').value;
        const errorEl = document.getElementById('login-error');
        const btn = document.getElementById('login-btn');
        hideError(errorEl);

        if (!username || !password) {
            showError(errorEl, 'Please fill in all fields');
            return;
        }

        btn.disabled = true;
        try {
            const formData = new URLSearchParams();
            formData.append('username', username);
            formData.append('password', password);

            const res = await apiFetch('/auth/token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: formData.toString()
            });

            setToken(res.access_token);
            window.location.href = '/dashboard.html';
        } catch (err) {
            showError(errorEl, err.message);
        } finally {
            btn.disabled = false;
        }
    });

    // Register
    document.getElementById('register-btn').addEventListener('click', async () => {
        const fullname = document.getElementById('reg-fullname').value.trim();
        const email = document.getElementById('reg-email').value.trim();
        const username = document.getElementById('reg-username').value.trim();
        const password = document.getElementById('reg-password').value;

        const errorEl = document.getElementById('register-error');
        const successEl = document.getElementById('register-success');
        const btn = document.getElementById('register-btn');

        hideError(errorEl);
        successEl.style.display = 'none';

        if (!fullname || !email || !username || !password) {
            showError(errorEl, 'Please fill in all fields');
            return;
        }

        btn.disabled = true;
        try {
            await apiFetch('/auth/', {
                method: 'POST',
                body: JSON.stringify({
                    full_name: fullname,
                    email: email,
                    username: username,
                    password: password,
                    plan_id: 1 // Free plan
                })
            });

            successEl.style.display = 'inline-flex';
            setTimeout(() => {
                document.getElementById('show-login').click();
                toast('Account created — sign in to continue', 'success');
            }, 1200);
        } catch (err) {
            showError(errorEl, err.message);
        } finally {
            btn.disabled = false;
        }
    });
});
