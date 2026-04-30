document.addEventListener('DOMContentLoaded', async () => {
    if (!getToken()) {
        window.location.href = '/index.html';
        return;
    }

    // Elements
    const userInfoEl = document.getElementById('user-info');
    const logoutBtn = document.getElementById('logout-btn');
    const planBtn = document.getElementById('plan-btn');
    const planModal = document.getElementById('plan-modal');
    const closePlanModal = document.getElementById('close-plan-modal');
    const planStatus = document.getElementById('plan-status');

    const shareModal = document.getElementById('share-modal');
    const closeShareModal = document.getElementById('close-share-modal');
    const shareLinkInput = document.getElementById('share-link-input');
    const copyShareBtn = document.getElementById('copy-share-btn');

    const filesList = document.getElementById('files-list');
    const fileInput = document.getElementById('file-input');
    const dropZone = document.getElementById('drop-zone');
    const uploadBtn = document.getElementById('upload-btn');
    const uploadStatus = document.getElementById('upload-status');

    let currentUser = null;

    // Format bytes
    function formatBytes(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    // Load User and Files
    async function init() {
        try {
            currentUser = await apiFetch('/auth/me');
            userInfoEl.innerHTML = `<span class="font-semibold">${currentUser.full_name || currentUser.username}</span> <span class="px-2 py-1 bg-slate-800 rounded-md text-xs ml-2 border border-slate-700">${currentUser.plan.name} Plan</span>`;
            updateStorageUI();
            await loadFiles();
        } catch (err) {
            console.error('Failed to load user', err);
        }
    }

    function updateStorageUI() {
        const used = currentUser.used_storage;
        const limit = currentUser.plan.storage_limit;
        const percentage = Math.min((used / limit) * 100, 100);
        
        document.getElementById('storage-bar').style.width = `${percentage}%`;
        document.getElementById('storage-text').textContent = `${formatBytes(used)} / ${formatBytes(limit)}`;
        
        if (percentage > 90) {
            document.getElementById('storage-bar').classList.remove('from-blue-500', 'to-purple-500');
            document.getElementById('storage-bar').classList.add('bg-red-500');
        }
    }

    async function loadFiles() {
        try {
            const files = await apiFetch('/files/get-files');
            filesList.innerHTML = '';
            
            if (files.length === 0) {
                filesList.innerHTML = `<tr><td colspan="4" class="text-center py-8 text-slate-500">No files uploaded yet.</td></tr>`;
                return;
            }

            files.forEach(file => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="py-3 px-4 truncate max-w-[200px]" title="${file.filename}">${file.filename}</td>
                    <td class="py-3 px-4 text-slate-400">${formatBytes(file.size)}</td>
                    <td class="py-3 px-4 text-center">
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" class="sr-only peer visibility-toggle" data-id="${file.id}" ${file.is_public ? 'checked' : ''}>
                            <div class="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-brand-500"></div>
                        </label>
                    </td>
                    <td class="py-3 px-4 text-right space-x-2">
                        <button class="text-brand-400 hover:text-brand-300 download-btn" data-id="${file.id}">Down</button>
                        <button class="text-purple-400 hover:text-purple-300 share-btn" data-id="${file.id}">Share</button>
                        <button class="text-red-400 hover:text-red-300 delete-btn" data-id="${file.id}">Del</button>
                    </td>
                `;
                filesList.appendChild(tr);
            });

            // Attach listeners
            document.querySelectorAll('.visibility-toggle').forEach(btn => {
                btn.addEventListener('change', async (e) => {
                    const fileId = e.target.getAttribute('data-id');
                    try {
                        await apiFetch(`/files/${fileId}/visibility`, {
                            method: 'PUT',
                            body: JSON.stringify({ choice: e.target.checked })
                        });
                    } catch(err) {
                        e.target.checked = !e.target.checked;
                        alert(err.message);
                    }
                });
            });

            document.querySelectorAll('.download-btn').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    const fileId = e.target.getAttribute('data-id');
                    const url = `${API_BASE}/files/download/${fileId}`;
                    // We can just open this URL if public, but since it might require auth, we fetch it and download
                    try {
                        const res = await fetch(url, { headers: { 'Authorization': `Bearer ${getToken()}` } });
                        if(!res.ok) throw new Error("Failed to download");
                        const blob = await res.blob();
                        const a = document.createElement('a');
                        a.href = window.URL.createObjectURL(blob);
                        // Try to get filename from content-disposition header if possible, else generic
                        const cd = res.headers.get('content-disposition');
                        let fn = "download";
                        if(cd && cd.indexOf('filename=') !== -1) {
                            fn = cd.split('filename=')[1].replace(/"/g,'');
                        }
                        a.download = fn;
                        document.body.appendChild(a);
                        a.click();
                        a.remove();
                    } catch(err) {
                        alert("Download failed.");
                    }
                });
            });

            document.querySelectorAll('.delete-btn').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    if(!confirm("Are you sure?")) return;
                    const fileId = e.target.getAttribute('data-id');
                    try {
                        await apiFetch(`/files/file-delete/${fileId}`, { method: 'DELETE' });
                        init(); // Reload
                    } catch(err) {
                        alert(err.message);
                    }
                });
            });

            document.querySelectorAll('.share-btn').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    const fileId = e.target.getAttribute('data-id');
                    try {
                        const res = await apiFetch(`/files/share/${fileId}?expires_minutes=30`, { method: 'POST' });
                        shareLinkInput.value = res.share_url;
                        shareModal.classList.remove('hidden');
                    } catch(err) {
                        alert(err.message);
                    }
                });
            });

        } catch (err) {
            console.error(err);
        }
    }

    // Logout
    logoutBtn.addEventListener('click', () => {
        removeToken();
        window.location.href = '/index.html';
    });

    // Upload
    fileInput.addEventListener('change', () => {
        if(fileInput.files.length > 0) {
            dropZone.querySelector('p').textContent = fileInput.files[0].name;
        }
    });

    uploadBtn.addEventListener('click', async () => {
        if (fileInput.files.length === 0) return;
        const file = fileInput.files[0];
        
        const formData = new FormData();
        formData.append('file', file);

        uploadBtn.disabled = true;
        uploadStatus.textContent = 'Uploading...';
        uploadStatus.className = 'mt-2 text-sm text-center text-slate-300';

        try {
            await apiFetch('/files/upload/', {
                method: 'POST',
                body: formData
            });
            uploadStatus.textContent = 'Upload complete!';
            uploadStatus.className = 'mt-2 text-sm text-center text-green-400';
            fileInput.value = '';
            dropZone.querySelector('p').textContent = 'Drag & drop or click';
            setTimeout(() => { uploadStatus.textContent = ''; }, 3000);
            init();
        } catch (err) {
            uploadStatus.textContent = err.message;
            uploadStatus.className = 'mt-2 text-sm text-center text-red-400';
        } finally {
            uploadBtn.disabled = false;
        }
    });

    // Share Modal
    closeShareModal.addEventListener('click', () => shareModal.classList.add('hidden'));
    copyShareBtn.addEventListener('click', () => {
        shareLinkInput.select();
        document.execCommand('copy');
        copyShareBtn.textContent = 'Copied!';
        setTimeout(() => { copyShareBtn.textContent = 'Copy Link'; }, 2000);
    });

    // Plan Modal
    planBtn.addEventListener('click', () => planModal.classList.remove('hidden'));
    closePlanModal.addEventListener('click', () => planModal.classList.add('hidden'));

    document.querySelectorAll('.select-plan-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const planName = e.target.getAttribute('data-plan');
            planStatus.textContent = 'Changing plan...';
            planStatus.className = 'mt-4 text-center text-sm text-slate-300';
            try {
                const res = await apiFetch('/plans/change-plan', {
                    method: 'PUT',
                    body: JSON.stringify({ plan: planName })
                });
                planStatus.textContent = res.message;
                planStatus.className = 'mt-4 text-center text-sm text-green-400';
                setTimeout(() => {
                    planModal.classList.add('hidden');
                    planStatus.textContent = '';
                    init();
                }, 2000);
            } catch(err) {
                planStatus.textContent = err.message;
                planStatus.className = 'mt-4 text-center text-sm text-red-400';
            }
        });
    });

    // Start
    init();
});
