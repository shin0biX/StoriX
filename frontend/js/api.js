const API_BASE = window.location.origin;

function getToken() {
    return localStorage.getItem('access_token');
}

function setToken(token) {
    localStorage.setItem('access_token', token);
}

function removeToken() {
    localStorage.removeItem('access_token');
}

async function apiFetch(endpoint, options = {}) {
    const token = getToken();
    const headers = {
        ...options.headers,
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    }

    const config = {
        ...options,
        headers,
    };

    const response = await fetch(`${API_BASE}${endpoint}`, config);

    if (response.status === 401 && endpoint !== '/auth/token') {
        removeToken();
        window.location.href = '/index.html';
        return;
    }

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(data.detail || 'Something went wrong');
    }

    return data;
}
