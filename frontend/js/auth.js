document.addEventListener('DOMContentLoaded', () => {
    // If already logged in, redirect to dashboard
    if (getToken()) {
        window.location.href = '/dashboard.html';
        return;
    }

    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');
    
    // Toggle forms
    document.getElementById('show-register').addEventListener('click', (e) => {
        e.preventDefault();
        loginForm.classList.add('hidden');
        registerForm.classList.remove('hidden');
    });

    document.getElementById('show-login').addEventListener('click', (e) => {
        e.preventDefault();
        registerForm.classList.add('hidden');
        loginForm.classList.remove('hidden');
    });

    // Login
    document.getElementById('login-btn').addEventListener('click', async () => {
        const username = document.getElementById('login-username').value;
        const password = document.getElementById('login-password').value;
        const errorEl = document.getElementById('login-error');
        errorEl.classList.add('hidden');

        if (!username || !password) {
            errorEl.textContent = 'Please fill in all fields';
            errorEl.classList.remove('hidden');
            return;
        }

        try {
            const formData = new URLSearchParams();
            formData.append('username', username);
            formData.append('password', password);

            const res = await apiFetch('/auth/token', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                body: formData.toString()
            });

            setToken(res.access_token);
            window.location.href = '/dashboard.html';
        } catch (err) {
            errorEl.textContent = err.message;
            errorEl.classList.remove('hidden');
        }
    });

    // Register
    document.getElementById('register-btn').addEventListener('click', async () => {
        const fullname = document.getElementById('reg-fullname').value;
        const email = document.getElementById('reg-email').value;
        const username = document.getElementById('reg-username').value;
        const password = document.getElementById('reg-password').value;
        
        const errorEl = document.getElementById('register-error');
        const successEl = document.getElementById('register-success');
        
        errorEl.classList.add('hidden');
        successEl.classList.add('hidden');

        if (!fullname || !email || !username || !password) {
            errorEl.textContent = 'Please fill in all fields';
            errorEl.classList.remove('hidden');
            return;
        }

        try {
            await apiFetch('/auth/', {
                method: 'POST',
                body: JSON.stringify({
                    full_name: fullname,
                    email: email,
                    username: username,
                    password: password,
                    plan_id: 1 // Default to 1 (Free)
                })
            });

            successEl.classList.remove('hidden');
            setTimeout(() => {
                document.getElementById('show-login').click();
            }, 1500);
        } catch (err) {
            errorEl.textContent = err.message;
            errorEl.classList.remove('hidden');
        }
    });
});
