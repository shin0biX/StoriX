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

async function tryRefreshToken() {
    const token = getToken();
    if (!token) return false;
    try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) return false;
        const data = await res.json();
        setToken(data.access_token);
        return true;
    } catch {
        return false;
    }
}

async function apiFetch(endpoint, options = {}) {
    const buildConfig = () => {
        const headers = { ...options.headers };
        const token = getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        if (!(options.body instanceof FormData)) {
            headers['Content-Type'] = headers['Content-Type'] || 'application/json';
        }
        return { ...options, headers };
    };

    let config = buildConfig();
    const response = await fetch(`${API_BASE}${endpoint}`, config);

    if (response.status === 401 && endpoint !== '/auth/token' && !options._retried) {
        // try a silent token refresh once, then retry the request
        const refreshed = await tryRefreshToken();
        if (refreshed) {
            const retryConfig = { ...options, headers: { ...options.headers }, _retried: true };
            const token = getToken();
            if (token) retryConfig.headers['Authorization'] = `Bearer ${token}`;
            if (!(retryConfig.body instanceof FormData)) {
                retryConfig.headers['Content-Type'] = retryConfig.headers['Content-Type'] || 'application/json';
            }
            const retryRes = await fetch(`${API_BASE}${endpoint}`, retryConfig);
            const retryData = await retryRes.json().catch(() => ({}));
            if (!retryRes.ok) throw new Error(retryData.detail || 'Something went wrong');
            return retryData;
        }
        removeToken();
        window.location.href = '/auth.html';
        return;
    }

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Something went wrong');
    }

    return data;
}

/* Upload a single file with progress reporting via XHR */
function uploadFileWithProgress(file, onProgress, folderId = null) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const formData = new FormData();
        formData.append('files', file);
        let url = `${API_BASE}/files/upload/`;
        if (folderId) url += `?folder_id=${folderId}`;

        xhr.open('POST', url);
        xhr.setRequestHeader('Authorization', `Bearer ${getToken()}`);

        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
        });

        xhr.addEventListener('load', () => {
            let data = {};
            try { data = JSON.parse(xhr.responseText); } catch {}
            if (xhr.status >= 200 && xhr.status < 300) {
                resolve(data);
            } else {
                reject(new Error(typeof data.detail === 'string' ? data.detail : 'Upload failed'));
            }
        });
        xhr.addEventListener('error', () => reject(new Error('Network error during upload')));

        xhr.send(formData);
    });
}

/* Authorized blob download (used for previews and file saves) */
async function fetchBlob(url) {
    const res = await fetch(url, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) {
        let detail = 'Request failed';
        try { const j = await res.json(); detail = j.detail || detail; } catch {}
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
