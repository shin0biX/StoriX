const API_BASE = window.location.origin;
const ACCESS_KEY = 'access_token';
const REFRESH_KEY = 'refresh_token';

function getToken() {
    return localStorage.getItem(ACCESS_KEY);
}

function getRefreshToken() {
    return localStorage.getItem(REFRESH_KEY);
}

function setTokens(data) {
    if (data && data.access_token) localStorage.setItem(ACCESS_KEY, data.access_token);
    if (data && data.refresh_token) localStorage.setItem(REFRESH_KEY, data.refresh_token);
}

// Backwards-compatible helper for callers that only have an access token.
function setToken(token) {
    if (token) localStorage.setItem(ACCESS_KEY, token);
}

function removeToken() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
}

function redirectToAuth() {
    if (window.location.pathname.endsWith('/auth.html')) return;
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `/auth.html?next=${next}`;
}

let refreshPromise = null;

async function tryRefreshToken() {
    if (refreshPromise) return refreshPromise;
    const refresh = getRefreshToken();
    if (!refresh) {
        removeToken();
        return false;
    }

    refreshPromise = (async () => {
        try {
            const res = await fetch(`${API_BASE}/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refresh }),
            });
            if (!res.ok) {
                removeToken();
                return false;
            }
            setTokens(await res.json());
            return true;
        } catch {
            return false;
        } finally {
            refreshPromise = null;
        }
    })();
    return refreshPromise;
}

function formatApiError(data, response) {
    if (data && typeof data.detail === 'string') return data.detail;
    if (data && Array.isArray(data.detail)) {
        return data.detail.map(item => {
            const where = Array.isArray(item.loc) ? item.loc.filter(p => p !== 'body').join('.') : '';
            return where ? `${where}: ${item.msg}` : item.msg;
        }).join('; ');
    }
    if (data && data.detail && typeof data.detail === 'object') {
        return data.detail.message || JSON.stringify(data.detail);
    }
    return response.statusText || 'Something went wrong';
}

async function parseResponse(response) {
    if (response.status === 204) return null;
    const type = response.headers.get('content-type') || '';
    if (type.includes('application/json')) {
        return response.json().catch(() => ({}));
    }
    const text = await response.text();
    return text ? { detail: text } : {};
}

async function apiFetch(endpoint, options = {}) {
    const retried = Boolean(options._retried);
    const { _retried, ...fetchOptions } = options;
    const headers = { ...(fetchOptions.headers || {}) };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (!(fetchOptions.body instanceof FormData) && fetchOptions.body !== undefined) {
        headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    }

    let response;
    try {
        response = await fetch(`${API_BASE}${endpoint}`, { ...fetchOptions, headers });
    } catch {
        throw new Error('Network error — check your connection and try again');
    }

    const authEndpoint = endpoint === '/auth/token' || endpoint === '/auth/refresh';
    if (response.status === 401 && !retried && !authEndpoint) {
        if (await tryRefreshToken()) {
            return apiFetch(endpoint, { ...options, _retried: true });
        }
        redirectToAuth();
        throw new Error('Session expired. Please sign in again.');
    }

    const data = await parseResponse(response);
    if (!response.ok) throw new Error(formatApiError(data, response));
    return data;
}

/* Upload a single file with progress reporting via XHR, with token retry. */
function uploadFileWithProgress(file, onProgress, folderId = null, retried = false) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const formData = new FormData();
        formData.append('files', file);
        let url = `${API_BASE}/files/upload/`;
        if (folderId) url += `?folder_id=${encodeURIComponent(folderId)}`;

        xhr.open('POST', url);
        const token = getToken();
        if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);

        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
        });

        xhr.addEventListener('load', async () => {
            let data = {};
            try { data = JSON.parse(xhr.responseText); } catch {}
            if (xhr.status === 401 && !retried) {
                if (await tryRefreshToken()) {
                    uploadFileWithProgress(file, onProgress, folderId, true).then(resolve, reject);
                } else {
                    redirectToAuth();
                    reject(new Error('Session expired. Please sign in again.'));
                }
                return;
            }
            if (xhr.status >= 200 && xhr.status < 300) {
                resolve(data);
            } else {
                reject(new Error(formatApiError(data, { statusText: 'Upload failed' })));
            }
        });
        xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
        xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')));

        xhr.send(formData);
    });
}

/* Authorized blob download (used for previews and file saves), with retry. */
async function fetchBlob(url, retried = false) {
    const headers = {};
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    let res;
    try {
        res = await fetch(url, { headers });
    } catch {
        throw new Error('Network error while downloading');
    }

    if (res.status === 401 && !retried) {
        if (await tryRefreshToken()) return fetchBlob(url, true);
        redirectToAuth();
        throw new Error('Session expired. Please sign in again.');
    }
    if (!res.ok) {
        let detail = 'Request failed';
        try {
            const data = await res.json();
            detail = formatApiError(data, res);
        } catch {}
        throw new Error(detail);
    }
    return res.blob();
}

function saveBlob(blob, filename) {
    const a = document.createElement('a');
    a.href = window.URL.createObjectURL(blob);
    a.download = filename || 'download';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

async function logout() {
    const refresh = getRefreshToken();
    removeToken();
    try {
        const options = { method: 'POST' };
        if (refresh) {
            options.headers = { 'Content-Type': 'application/json' };
            options.body = JSON.stringify({ refresh_token: refresh });
        }
        await fetch(`${API_BASE}/auth/logout`, options);
    } catch {
        // Offline logout still clears local state.
    }
}
