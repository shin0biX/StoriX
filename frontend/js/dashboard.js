document.addEventListener('DOMContentLoaded', async () => {
    if (!getToken()) {
        window.location.href = '/auth.html';
        return;
    }

    /* ---------------- State ---------------- */
    let user = null;
    let view = 'files';
    let currentFolder = null;          // folder id or null (root)
    let files = [];                    // active files
    let trashFiles = [];
    let folders = [];
    let selection = new Set();
    let searchQ = '';
    let viewMode = localStorage.getItem('storix.viewMode') || 'list';
    let sortMode = 'date-desc';
    let previewURL = null;             // object URL to revoke
    let previewFileRef = null;
    let shareFileRef = null;

    /* ---------------- Elements ---------------- */
    const $ = (id) => document.getElementById(id);
    const browser = $('browser');
    const fileInput = $('file-input');
    const dropzone = $('dropzone');

    /* ---------------- Icons ---------------- */
    document.querySelectorAll('[data-ic]').forEach(el => { el.innerHTML = icon(el.dataset.ic); });
    $('logo-mark').innerHTML = icon('logo');

    /* ---------------- Data loading ---------------- */
    async function loadData() {
        const [me, fs, fds] = await Promise.all([
            apiFetch('/auth/me'),
            apiFetch('/files/get-files'),
            apiFetch('/files/folders').catch(() => []),
        ]);
        user = me;
        files = fs;
        folders = fds;
        updateSidebar();
        render();
    }

    async function loadTrash() {
        try { trashFiles = await apiFetch('/files/trash'); } catch { trashFiles = []; }
    }

    function updateSidebar() {
        const initials = (user.full_name || user.username || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
        $('sb-avatar').textContent = initials;
        $('sb-username').textContent = user.full_name || user.username;
        $('sb-plan-name').textContent = `${user.plan.name} plan`;

        const badge = $('sb-plan-badge');
        badge.textContent = user.plan.name;
        badge.className = 'badge badge-accent';

        const used = user.used_storage, limit = user.plan.storage_limit;
        const pct = Math.min((used / limit) * 100, 100);
        const bar = $('sb-bar');
        bar.style.width = `${pct}%`;
        bar.classList.toggle('warn', pct > 75 && pct <= 90);
        bar.classList.toggle('full', pct > 90);
        $('sb-used').textContent = formatBytes(used);
        $('sb-limit').textContent = formatBytes(limit);

        if (user.role === 'admin') $('nav-admin').style.display = 'flex';

        const countEl = (n) => n > 0 ? n : '';
        $('nav-count-shared').textContent = countEl(files.filter(f => f.is_public).length);
        $('nav-count-starred').textContent = countEl(files.filter(f => f.is_starred).length);
        $('nav-count-trash').textContent = trashFiles.length > 0 ? trashFiles.length : '';

        $('dz-hint').textContent = `Up to ${formatBytes(user.plan.max_file_size)} per file on the ${user.plan.name} plan`;
        if (!user.plan.can_share) {
            $('dz-hint').textContent += ' · sharing requires Pro';
        }
    }

    /* ---------------- Views ---------------- */
    const VIEW_META = {
        files: { title: 'My Files', sub: () => `Files in ${currentFolderName() || 'root'}` },
        recent: { title: 'Recent', sub: () => 'Most recently uploaded files' },
        shared: { title: 'Shared', sub: () => 'Files visible to anyone with the link' },
        starred: { title: 'Starred', sub: () => 'Files you marked with a star' },
        trash: { title: 'Trash', sub: () => 'Deleted files — restore them or free the space' },
    };

    function currentFolderName() {
        if (currentFolder === null) return null;
        const f = folders.find(x => x.id === currentFolder);
        return f ? f.name : null;
    }

    function visibleFiles() {
        let list;
        if (view === 'trash') list = trashFiles;
        else if (view === 'shared') list = files.filter(f => f.is_public);
        else if (view === 'starred') list = files.filter(f => f.is_starred);
        else if (view === 'recent') list = [...files];
        else list = files.filter(f => (currentFolder === null ? f.folder_id == null : f.folder_id === currentFolder));

        if (searchQ) {
            // searching spans every folder
            if (view === 'files') list = [...files];
            const q = searchQ.toLowerCase();
            list = list.filter(f => f.filename.toLowerCase().includes(q));
        }

        const cmp = {
            'name': (a, b) => a.filename.localeCompare(b.filename),
            'size-desc': (a, b) => b.size - a.size,
            'size-asc': (a, b) => a.size - b.size,
            'date-desc': (a, b) => new Date(b.uploaded_at) - new Date(a.uploaded_at),
            'date-asc': (a, b) => new Date(a.uploaded_at) - new Date(b.uploaded_at),
        }[sortMode] || ((a, b) => 0);
        return [...list].sort(cmp);
    }

    /* ---------------- Rendering ---------------- */
    function render() {
        browser.scrollTop = 0; // overflow:hidden panels can still be scrolled programmatically
        const meta = VIEW_META[view];
        $('view-title').textContent = meta.title;
        $('view-sub').textContent = meta.sub();

        document.querySelectorAll('.nav-item[data-view]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === view);
        });

        // folder-specific controls
        const isFiles = view === 'files';
        $('btn-new-folder').style.display = isFiles ? 'inline-flex' : 'none';
        $('btn-empty-trash').style.display = view === 'trash' ? 'inline-flex' : 'none';
        dropzone.style.display = isFiles ? 'block' : 'none';
        renderCrumbs();
        renderTypeBreakdown();

        const list = visibleFiles();
        $('view-sub').textContent = searchQ ? `${list.length} result${list.length === 1 ? '' : 's'} for "${searchQ}"` : meta.sub();

        // clear selection entries that are no longer visible
        const visibleIds = new Set(list.map(f => f.id));
        selection.forEach(id => { if (!visibleIds.has(id)) selection.delete(id); });
        updateBulkBar();

        if (list.length === 0) {
            browser.innerHTML = emptyStateHTML();
            return;
        }
        browser.classList.remove('view-fade');
        void browser.offsetWidth;
        browser.classList.add('view-fade');

        if (viewMode === 'grid' && view !== 'trash') renderGrid(list);
        else renderList(list);
    }

    function emptyStateHTML() {
        const states = {
            files: currentFolder !== null
                ? { ic: 'folder', h: 'This folder is empty', p: 'Drop files here or use the upload button.' }
                : { ic: 'files', h: 'No files yet', p: 'Your workspace is waiting. Upload your first file above.' },
            recent: { ic: 'clock', h: 'Nothing recent', p: 'Files you upload will show up here.' },
            shared: { ic: 'globe', h: 'Nothing shared', p: 'Mark a file as public or create a share link to see it here.' },
            starred: { ic: 'star', h: 'No starred files', p: 'Star the files you reach for most and they will live here.' },
            trash: { ic: 'trash', h: 'Trash is empty', p: 'Deleted files rest here until you remove them for good.' },
        }[view];
        if (searchQ) {
            return `<div class="empty-state"><div class="empty-icon">${icon('search')}</div>
                <h3>No matches for "${escapeHtml(searchQ)}"</h3><p>Try a different search term.</p></div>`;
        }
        return `<div class="empty-state"><div class="empty-icon">${icon(states.ic)}</div><h3>${states.h}</h3><p>${states.p}</p></div>`;
    }

    function renderCrumbs() {
        const crumbs = $('crumbs');
        if (view !== 'files') { crumbs.innerHTML = ''; return; }
        let html = `<button data-crumb="root">My Files</button>`;
        // build chain up to current folder
        const chain = [];
        let cur = folders.find(f => f.id === currentFolder);
        while (cur) { chain.unshift(cur); cur = folders.find(f => f.id === cur.parent_id); }
        chain.forEach(f => {
            html += icon('chevronRight');
            html += f.id === currentFolder
                ? `<span class="current">${escapeHtml(f.name)}</span>`
                : `<button data-crumb="${f.id}">${escapeHtml(f.name)}</button>`;
        });
        crumbs.innerHTML = html;
    }

    function renderTypeBreakdown() {
        const el = $('type-breakdown');
        if (view !== 'files' || currentFolder !== null || searchQ || files.length === 0) {
            el.style.display = 'none';
            return;
        }
        const colors = { image: '#60a5fa', video: '#f472b6', audio: '#a78bfa', pdf: '#fb923c', doc: '#fb923c', archive: '#fbbf24', code: '#34d399', other: '#6b7280' };
        const totals = {};
        files.forEach(f => {
            const t = fileType(f.filename);
            totals[t] = (totals[t] || 0) + f.size;
        });
        const totalBytes = files.reduce((s, f) => s + f.size, 0);
        const top = Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 4);
        el.style.display = 'block';
        el.innerHTML = `<div class="text-[12px] font-medium text-mut mb-3">Storage by type — ${formatBytes(totalBytes)} total</div>` +
            top.map(([t, bytes]) => `
                <div class="typebar mb-2" style="max-width:440px">
                    <span style="width:64px">${t}</span>
                    <div class="tb-track"><div class="tb-fill" style="width:${(bytes / totalBytes * 100).toFixed(1)}%;background:${colors[t]}"></div></div>
                    <span class="mono" style="width:70px;text-align:right">${formatBytes(bytes)}</span>
                </div>`).join('');
    }

    function starBtnHTML(f) {
        return `<button class="icon-action star-btn ${f.is_starred ? 'starred' : ''}" data-action="star" data-id="${f.id}" title="${f.is_starred ? 'Unstar' : 'Star'}">
            <svg viewBox="0 0 24 24" fill="${f.is_starred ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS.star}</svg></button>`;
    }

    function rowMenuHTML() {
        return `<button class="icon-action" data-row-menu title="More actions">${icon('more')}</button>`;
    }

    function fileMenuItems(f) {
        if (view === 'trash') {
            return [
                { id: 'restore', label: 'Restore', icon: 'restore', onSelect: () => restoreFile(f) },
                { sep: true },
                { id: 'perm-delete', label: 'Delete forever', icon: 'trash', danger: true, onSelect: () => permDeleteFile(f) },
            ];
        }
        return [
            { id: 'preview', label: 'Preview', icon: 'eye', onSelect: () => openPreview(f) },
            { id: 'download', label: 'Download', icon: 'download', onSelect: () => downloadFile(f) },
            { id: 'versions', label: 'Version history', icon: 'history', onSelect: () => openVersions(f) },
            { sep: true },
            { id: 'share', label: 'Share…', icon: 'link', onSelect: () => {
                if (!user.plan.can_share) { toast('Sharing requires the Pro plan or above', 'error'); return; }
                openShare(f);
            } },
            { id: 'rename', label: 'Rename', icon: 'pencil', onSelect: () => openRename(f) },
            { id: 'move', label: 'Move to folder…', icon: 'move', onSelect: () => openMove(f) },
            { sep: true },
            { id: 'delete', label: 'Move to trash', icon: 'trash', danger: true, onSelect: () => deleteFile(f) },
        ];
    }

    function folderMenuItems(folder) {
        return [
            { id: 'f-open', label: 'Open', icon: 'folder', onSelect: () => openFolder(folder.id) },
            { sep: true },
            { id: 'f-rename', label: 'Rename', icon: 'pencil', onSelect: () => renameFolderFlow(folder.id) },
            { id: 'f-delete', label: 'Delete folder', icon: 'trash', danger: true, onSelect: () => deleteFolderFlow(folder.id) },
        ];
    }

    function folderMenuHTML() {
        return rowMenuHTML();
    }

    function visibleFolders() {
        if (view !== 'files' || searchQ) return [];
        return folders.filter(f => (f.parent_id ?? null) === currentFolder)
            .sort((a, b) => a.name.localeCompare(b.name));
    }

    function folderListHTML(list) {
        const fdrs = visibleFolders();
        if (!fdrs.length) return '';
        const rows = fdrs.map(fd => `
            <tr class="file-row" data-row="${fd.id}">
                <td style="width:36px"></td>
                <td><div class="file-name-cell">
                    <span class="ft-icon folder">${icon('folder')}</span>
                    <span class="fname">${escapeHtml(fd.name)}</span>
                    <span class="badge" style="margin-left:8px">folder</span>
                </div></td>
                <td class="text-mut mono col-size" style="width:90px">—</td>
                <td class="text-mut col-date" style="width:120px">—</td>
                <td style="width:130px"><div class="row-actions">${folderMenuHTML(fd)}</div></td>
            </tr>`).join('');
        return rows;
    }

    function renderList(list) {
        const isTrash = view === 'trash';
        const rows = list.map(f => `
            <tr class="file-row ${selection.has(f.id) ? 'selected' : ''}" data-row="${f.id}">
                <td style="width:36px"><input type="checkbox" class="cbx row-cbx" data-id="${f.id}" ${selection.has(f.id) ? 'checked' : ''}></td>
                <td><div class="file-name-cell">
                    ${fileIconEl(f.filename)}
                    <span class="fname" title="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}${f.version > 1 ? ` <span class="badge" style="margin-left:6px">v${f.version}</span>` : ''}</span>
                    ${f.is_public && !isTrash ? `<span class="badge badge-accent" style="margin-left:8px">${icon('globe')}public</span>` : ''}
                </div></td>
                <td class="text-mut mono nowrap col-size" style="width:90px">${formatBytes(f.size)}</td>
                <td class="text-mut nowrap col-date" style="width:120px">${isTrash ? 'deleted ' + formatDate(f.deleted_at) : formatDate(f.uploaded_at)}</td>
                <td style="width:130px">
                    <div class="row-actions">
                        ${isTrash ? '' : starBtnHTML(f)}
                        ${rowMenuHTML(f)}
                    </div>
                </td>
            </tr>`).join('');

        browser.innerHTML = `
            <table class="file-table">
                <thead><tr>
                    <th style="width:36px"></th>
                    <th>Name</th>
                    <th class="col-size" style="width:90px">Size</th>
                    <th class="col-date" style="width:120px">${isTrash ? 'Deleted' : 'Uploaded'}</th>
                    <th style="width:130px"></th>
                </tr></thead>
                <tbody>${folderListHTML(list)}${rows}</tbody>
            </table>`;
    }

    function renderGrid(list) {
        const folderCards = visibleFolders().map(fd => `
            <div class="file-card" data-row="${fd.id}">
                <div class="flex items-start justify-between">
                    <span class="ft-icon folder" style="width:42px;height:42px">${icon('folder')}</span>
                    <div class="row-actions">${folderMenuHTML(fd)}</div>
                </div>
                <div class="card-name" title="${escapeHtml(fd.name)}">${escapeHtml(fd.name)}</div>
                <div class="text-dim text-[11.5px] mt-0.5">Folder</div>
            </div>`).join('');
        const cards = list.map(f => `
            <div class="file-card ${selection.has(f.id) ? 'selected' : ''}" data-row="${f.id}">
                <div class="flex items-start justify-between">
                    ${fileIconEl(f.filename)}
                    <div class="row-actions">${rowMenuHTML(f)}</div>
                </div>
                <div class="card-name" title="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}</div>
                <div class="text-dim text-[11.5px] mt-0.5 mono">${formatBytes(f.size)} · ${formatDate(f.uploaded_at)}</div>
                <input type="checkbox" class="cbx row-cbx" data-id="${f.id}" style="position:absolute;left:12px;top:12px" ${selection.has(f.id) ? 'checked' : ''}>
            </div>`).join('');
        browser.innerHTML = `<div class="file-grid">${folderCards}${cards}</div>`;
    }

    function updateBulkBar() {
        const bar = $('bulk-bar');
        if (selection.size > 0 && view !== 'trash') {
            bar.style.display = 'flex';
            $('bulk-count').textContent = selection.size;
        } else {
            bar.style.display = 'none';
        }
    }

    /* ---------------- Folder navigation ---------------- */
    function openFolder(id) {
        currentFolder = id;
        selection.clear();
        render();
    }

    function folderOptionsHTML(selectedId) {
        let html = `<option value="">Workspace root</option>`;
        const build = (parentId, depth) => {
            folders.filter(f => (f.parent_id ?? null) === parentId).forEach(f => {
                const sel = f.id === selectedId ? 'selected' : '';
                html += `<option value="${f.id}" ${sel}>${'— '.repeat(depth)}${escapeHtml(f.name)}</option>`;
                build(f.id, depth + 1);
            });
        };
        build(null, 0);
        return html;
    }

    /* ---------------- Actions ---------------- */
    function findFile(id) {
        return trashFiles.find(f => f.id === id) || files.find(f => f.id === id);
    }

    async function downloadFile(f) {
        try {
            const blob = await fetchBlob(`/files/download/${f.id}`);
            saveBlob(blob, f.filename);
            toast('Download started', 'success');
        } catch (err) { toast(err.message, 'error'); }
    }

    async function toggleStar(f) {
        try {
            await apiFetch(`/files/${f.id}/star`, { method: 'PUT', body: JSON.stringify({ starred: !f.is_starred }) });
            f.is_starred = !f.is_starred;
            const active = files.find(x => x.id === f.id);
            if (active) active.is_starred = f.is_starred;
            render();
            toast(f.is_starred ? 'Added to starred' : 'Removed from starred', 'success', 1600);
        } catch (err) { toast(err.message, 'error'); }
    }

    async function deleteFile(f) {
        const ok = await confirmDialog(`Move "${f.filename}" to trash?`);
        if (!ok) return;
        try {
            await apiFetch(`/files/file-delete/${f.id}`, { method: 'DELETE' });
            toast('Moved to trash', 'success');
            selection.delete(f.id);
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function restoreFile(f) {
        try {
            await apiFetch(`/files/restore/${f.id}`, { method: 'POST' });
            toast('File restored', 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function permDeleteFile(f) {
        const ok = await confirmDialog(`Permanently delete "${f.filename}"?`, { confirmText: 'Delete forever' });
        if (!ok) return;
        try {
            await apiFetch(`/files/permanent-delete/${f.id}`, { method: 'DELETE' });
            toast('File permanently deleted', 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function emptyTrash() {
        const ok = await confirmDialog('Permanently delete everything in the trash?', { confirmText: 'Empty trash' });
        if (!ok) return;
        try {
            const res = await apiFetch('/files/trash/empty', { method: 'POST' });
            toast(res.message, 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    /* ---------------- Share ---------------- */
    function openShare(f) {
        shareFileRef = f;
        $('share-link-input').value = '';
        $('share-file-name').textContent = f.filename;
        $('btn-create-link').style.display = '';
        openModal($('modal-share'));
    }

    async function createShareLink() {
        if (!shareFileRef) return;
        const minutes = $('share-expiry').value;
        const btn = $('btn-create-link');
        btn.disabled = true;
        try {
            const res = await apiFetch(`/files/share/${shareFileRef.id}?expires_minutes=${minutes}`, { method: 'POST' });
            $('share-link-input').value = res.share_url;
            $('btn-create-link').style.display = 'none';
            const mins = Math.round((new Date(res.expires_at) - Date.now()) / 60000);
            const expiryText = mins >= 1440 ? `in ${Math.round(mins / 1440)} day${mins >= 2880 ? 's' : ''}`
                : mins >= 60 ? `in ${Math.round(mins / 60)} hour${mins >= 120 ? 's' : ''}`
                : `in ${mins} minute${mins === 1 ? '' : 's'}`;
            toast(`Share link created — expires ${expiryText}`, 'success');
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            btn.disabled = false;
        }
    }

    /* ---------------- Rename / move / folders ---------------- */
    let renameTarget = null, moveTarget = null;

    function openRename(f) {
        renameTarget = f;
        $('rename-input').value = f.filename;
        openModal($('modal-rename'));
    }

    async function saveRename() {
        if (!renameTarget) return;
        try {
            await apiFetch(`/files/${renameTarget.id}/rename`, { method: 'PUT', body: JSON.stringify({ filename: $('rename-input').value }) });
            closeModal($('modal-rename'));
            toast('File renamed', 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    function openMove(f) {
        moveTarget = f;
        $('move-select').innerHTML = folderOptionsHTML(f.folder_id);
        openModal($('modal-move'));
    }

    async function saveMove() {
        if (!moveTarget) return;
        const val = $('move-select').value;
        try {
            await apiFetch(`/files/${moveTarget.id}/move`, { method: 'PUT', body: JSON.stringify({ folder_id: val ? parseInt(val) : null }) });
            closeModal($('modal-move'));
            toast('File moved', 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function createFolder() {
        const name = $('folder-name-input').value.trim();
        if (!name) return;
        try {
            await apiFetch('/files/folders', { method: 'POST', body: JSON.stringify({ name, parent_id: currentFolder }) });
            closeModal($('modal-folder'));
            $('folder-name-input').value = '';
            toast('Folder created', 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function renameFolderFlow(folderId) {
        const folder = folders.find(f => f.id === folderId);
        if (!folder) return;
        const name = prompt('New folder name:', folder.name);
        if (!name || name === folder.name) return;
        try {
            await apiFetch(`/files/folders/${folderId}/rename`, { method: 'PUT', body: JSON.stringify({ name }) });
            toast('Folder renamed', 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function deleteFolderFlow(folderId) {
        const folder = folders.find(f => f.id === folderId);
        const ok = await confirmDialog(`Delete folder "${folder ? folder.name : ''}" and move its files to trash?`);
        if (!ok) return;
        try {
            const res = await apiFetch(`/files/folders/${folderId}`, { method: 'DELETE' });
            toast(res.message, 'success');
            if (currentFolder === folderId) currentFolder = null;
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    /* ---------------- Preview ---------------- */
    function openPreview(f) {
        previewFileRef = f;
        $('preview-title').textContent = f.filename;
        const stage = $('preview-stage');
        stage.innerHTML = `<div class="spinner"></div>`;
        openModal($('modal-preview'));

        const t = fileType(f.filename);
        fetchBlob(`/files/preview/${f.id}`).then(blob => {
            if (previewURL) URL.revokeObjectURL(previewURL);
            if (t === 'image') {
                previewURL = URL.createObjectURL(blob);
                stage.innerHTML = `<img src="${previewURL}" alt="">`;
            } else if (t === 'video') {
                previewURL = URL.createObjectURL(blob);
                stage.innerHTML = `<video src="${previewURL}" controls autoplay></video>`;
            } else if (t === 'audio') {
                previewURL = URL.createObjectURL(blob);
                stage.innerHTML = `<audio src="${previewURL}" controls autoplay style="width:min(420px,90%)"></audio>`;
            } else if (t === 'pdf') {
                previewURL = URL.createObjectURL(blob);
                stage.innerHTML = `<iframe src="${previewURL}"></iframe>`;
            } else if (blob.size < 1024 * 1024) {
                blob.text().then(txt => {
                    stage.innerHTML = `<pre>${escapeHtml(txt.slice(0, 200000))}</pre>`;
                });
            } else {
                stage.innerHTML = `<div class="empty-state" style="padding:32px"><div class="empty-icon">${icon('file')}</div>
                    <h3>No inline preview</h3><p>Use the download button to get this file.</p></div>`;
            }
        }).catch(err => {
            stage.innerHTML = `<div class="empty-state"><div class="empty-icon">${icon('alert')}</div><h3>Preview failed</h3><p>${escapeHtml(err.message)}</p></div>`;
        });
    }

    /* ---------------- Versions ---------------- */
    let versionsTarget = null;

    async function openVersions(f) {
        versionsTarget = f;
        $('versions-title').textContent = `Versions — ${f.filename}`;
        const list = $('versions-list');
        list.innerHTML = `<div style="padding:24px;text-align:center"><span class="spinner"></span></div>`;
        openModal($('modal-versions'));
        try {
            const versions = await apiFetch(`/files/versions/${f.id}`);
            if (versions.length <= 1) {
                list.innerHTML = `<div class="empty-state" style="padding:32px"><div class="empty-icon">${icon('history')}</div>
                    <h3>No older versions</h3><p>Re-upload a file with the same name to create version history.</p></div>`;
                return;
            }
            list.innerHTML = versions.map(v => `
                <div class="flex items-center gap-3 px-3 py-2.5" style="border-bottom:1px solid var(--border)">
                    <span class="ft-icon ${v.is_latest ? '' : ''}" style="width:30px;height:30px">${icon('history')}</span>
                    <div style="flex:1;min-width:0">
                        <div class="text-[13px] font-medium">Version ${v.version} ${v.is_latest ? '<span class="badge badge-accent" style="margin-left:6px">current</span>' : ''}</div>
                        <div class="text-[11.5px] text-dim mono">${formatBytes(v.size)} · ${formatDate(v.uploaded_at)}</div>
                    </div>
                    ${!v.is_latest ? `<button class="btn btn-sm" data-restore-version="${v.id}">Restore</button>` : ''}
                </div>`).join('');
        } catch (err) {
            list.innerHTML = `<div class="empty-state" style="padding:32px"><h3>Failed to load versions</h3><p>${escapeHtml(err.message)}</p></div>`;
        }
    }

    /* ---------------- Plans ---------------- */
    const PLANS = [
        { name: 'Free', price: 0, storage: '1 GB', perFile: '100 MB', share: false },
        { name: 'Pro', price: 10, storage: '10 GB', perFile: '1 GB', share: true, tag: 'Most popular' },
        { name: 'Premium', price: 20, storage: '100 GB', perFile: '10 GB', share: true },
    ];

    function renderPlans() {
        $('plan-grid').innerHTML = PLANS.map(p => {
            const isCurrent = user && user.plan.name.toLowerCase() === p.name.toLowerCase();
            return `
            <div class="plan-card ${isCurrent ? 'current' : ''}">
                ${p.tag ? `<span class="plan-tag">${p.tag}</span>` : ''}
                <h3 class="font-semibold mb-1" style="font-size:16px">${p.name}</h3>
                <p class="mb-4"><span class="font-bold" style="font-size:26px;font-family:var(--font-display)">$${p.price}</span><span class="text-dim text-[12px]">/mo</span></p>
                <ul class="space-y-1.5 text-[13px] text-mut" style="list-style:none;padding:0;margin:0 0 16px">
                    <li>${p.storage} storage</li>
                    <li>${p.perFile} max file size</li>
                    <li>${p.share ? 'Public files & share links' : 'Private files only'}</li>
                </ul>
                ${isCurrent
                    ? `<button class="btn w-full" disabled>Current plan</button>`
                    : `<button class="btn ${p.name === 'Pro' ? 'btn-primary' : ''} w-full" data-select-plan="${p.name}">Switch to ${p.name}</button>`}
            </div>`;
        }).join('');
    }

    async function selectPlan(planName) {
        const status = $('plan-status');
        status.style.color = 'var(--text-2)';
        status.textContent = `Switching to ${planName}…`;
        try {
            const res = await apiFetch('/plans/change-plan', { method: 'PUT', body: JSON.stringify({ plan: planName }) });
            status.style.color = 'var(--accent)';
            status.textContent = res.message;
            toast(res.message, 'success');
            const me = await apiFetch('/auth/me');
            user = me;
            updateSidebar();
            renderPlans();
            setTimeout(() => { closeModal($('modal-plan')); status.textContent = ''; }, 1400);
        } catch (err) {
            status.style.color = 'var(--danger)';
            status.textContent = err.message;
        }
    }

    /* ---------------- Upload ---------------- */
    let uploadQueue = [];
    let uploading = false;

    function showUploadPanel() {
        $('upload-panel').style.display = 'block';
        $('btn-upload-close').style.display = 'none';
    }

    function addUploadItem(name) {
        const el = document.createElement('div');
        el.className = 'upload-item';
        el.innerHTML = `
            <div class="up-row">${fileIconEl(name)}<span class="up-name">${escapeHtml(name)}</span></div>
            <div class="up-bar"><div class="up-bar-fill"></div></div>
            <div class="up-status">Waiting…</div>`;
        $('upload-list').appendChild(el);
        $('upload-list').scrollTop = $('upload-list').scrollHeight;
        return {
            setProgress(p) { el.querySelector('.up-bar-fill').style.width = `${p}%`; el.querySelector('.up-status').textContent = `Uploading — ${p}%`; },
            done(msg) { el.classList.add('done'); el.querySelector('.up-bar-fill').style.width = '100%'; el.querySelector('.up-status').textContent = msg || 'Uploaded'; },
            fail(msg) { el.classList.add('failed'); el.querySelector('.up-bar-fill').style.width = '100%'; el.querySelector('.up-status').textContent = msg || 'Failed'; },
        };
    }

    async function processQueue() {
        if (uploading) return;
        uploading = true;
        showUploadPanel();
        $('upload-title').textContent = `Uploading ${uploadQueue.length} file${uploadQueue.length === 1 ? '' : 's'}…`;

        let succeeded = 0, failed = 0;
        while (uploadQueue.length > 0) {
            const { file, ui } = uploadQueue.shift();
            try {
                const res = await uploadFileWithProgress(file, (p) => ui.setProgress(p), currentFolder);
                if (res.errors && res.errors.length) {
                    ui.fail(res.errors[0].detail);
                    failed++;
                } else {
                    ui.done(`Uploaded${res.uploaded && res.uploaded[0] && res.uploaded[0].version > 1 ? ` — v${res.uploaded[0].version}` : ''}`);
                    succeeded++;
                }
            } catch (err) {
                ui.fail(err.message);
                failed++;
            }
        }

        $('upload-title').textContent = `Uploads finished — ${succeeded} done${failed ? `, ${failed} failed` : ''}`;
        uploading = false;
        $('btn-upload-close').style.display = 'inline-flex';
        if (failed) toast(`${failed} upload${failed === 1 ? '' : 's'} failed`, 'error');
        if (succeeded) toast(`${succeeded} file${succeeded === 1 ? '' : 's'} uploaded`, 'success');
        await refreshAll();
        setTimeout(() => { if (!uploading) $('upload-panel').style.display = 'none'; }, 5000);
    }

    function queueFiles(fileList) {
        const arr = Array.from(fileList);
        if (!arr.length) return;
        showUploadPanel();
        arr.forEach(file => uploadQueue.push({ file, ui: addUploadItem(file.name) }));
        processQueue();
    }

    /* ---------------- Bulk ---------------- */
    async function bulkZip() {
        if (!selection.size) return;
        try {
            toast('Preparing ZIP…', 'info', 2000);
            const blob = await fetchBlob(`/files/download-zip?ids=${[...selection].join(',')}`);
            saveBlob(blob, 'storix-files.zip');
            toast('ZIP downloaded', 'success');
        } catch (err) { toast(err.message, 'error'); }
    }

    async function bulkDelete() {
        if (!selection.size) return;
        const ok = await confirmDialog(`Move ${selection.size} file${selection.size === 1 ? '' : 's'} to trash?`);
        if (!ok) return;
        let failed = 0;
        for (const id of selection) {
            try { await apiFetch(`/files/file-delete/${id}`, { method: 'DELETE' }); }
            catch { failed++; }
        }
        selection.clear();
        if (failed) toast(`${failed} could not be deleted`, 'error');
        else toast('Files moved to trash', 'success');
        await refreshAll();
    }

    /* ---------------- Refresh ---------------- */
    async function refreshAll() {
        if (view === 'trash') await loadTrash();
        await loadData();
        if (view === 'trash') { updateSidebar(); render(); }
    }

    /* ---------------- Event wiring ---------------- */

    // Sidebar navigation
    document.querySelectorAll('.nav-item[data-view]').forEach(btn => {
        btn.addEventListener('click', () => {
            view = btn.dataset.view;
            currentFolder = null;
            selection.clear();
            searchQ = '';
            $('search-input').value = '';
            location.hash = view;
            if (view === 'trash') { loadTrash().then(render); }
            render();
            closeSidebarMobile();
        });
    });
    $('nav-admin').addEventListener('click', () => { window.location.href = '/admin.html'; });

    // hash routing on load
    const hashView = location.hash.replace('#', '');
    if (VIEW_META[hashView]) view = hashView;

    // Mobile sidebar
    $('btn-menu').addEventListener('click', () => {
        $('sidebar').classList.add('open');
        $('scrim').classList.add('show');
    });
    $('scrim').addEventListener('click', closeSidebarMobile);
    function closeSidebarMobile() {
        $('sidebar').classList.remove('open');
        $('scrim').classList.remove('show');
    }

    // Logout
    $('btn-logout').addEventListener('click', () => {
        removeToken();
        window.location.href = '/auth.html';
    });

    // Plans
    $('btn-upgrade').addEventListener('click', () => { renderPlans(); openModal($('modal-plan')); });
    $('plan-grid').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-select-plan]');
        if (btn) selectPlan(btn.dataset.selectPlan);
    });

    // Upload
    $('btn-upload').addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('click', (e) => { if (e.target === dropzone || e.target.closest('p') || e.target.closest('span')) fileInput.click(); });
    fileInput.addEventListener('change', () => { queueFiles(fileInput.files); fileInput.value = ''; });
    ['dragover', 'dragenter'].forEach(ev => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add('dragover'); }));
    ['dragleave', 'drop'].forEach(ev => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove('dragover'); }));
    dropzone.addEventListener('drop', (e) => { if (e.dataTransfer.files.length) queueFiles(e.dataTransfer.files); });
    $('btn-upload-close').addEventListener('click', () => { $('upload-panel').style.display = 'none'; $('upload-list').innerHTML = ''; });

    // Search
    let searchTimer = null;
    $('search-input').addEventListener('input', (e) => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { searchQ = e.target.value.trim(); render(); }, 180);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA' && !document.activeElement.isContentEditable) {
            e.preventDefault();
            $('search-input').focus();
        }
    });

    // View mode + sort
    function updateViewModeBtn() {
        $('btn-view-mode').innerHTML = icon(viewMode === 'list' ? 'grid' : 'list');
        $('btn-view-mode').title = viewMode === 'list' ? 'Switch to grid view' : 'Switch to list view';
    }
    $('btn-view-mode').addEventListener('click', () => {
        viewMode = viewMode === 'list' ? 'grid' : 'list';
        localStorage.setItem('storix.viewMode', viewMode);
        updateViewModeBtn();
        render();
    });
    $('sort-select').addEventListener('change', (e) => { sortMode = e.target.value; render(); });
    updateViewModeBtn();

    // Breadcrumbs
    $('crumbs').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-crumb]');
        if (!btn) return;
        openFolder(btn.dataset.crumb === 'root' ? null : parseInt(btn.dataset.crumb));
    });

    // Browser delegation: checkbox, row click, actions
    browser.addEventListener('change', (e) => {
        if (e.target.classList.contains('row-cbx')) {
            const id = parseInt(e.target.dataset.id);
            if (e.target.checked) selection.add(id); else selection.delete(id);
            const row = e.target.closest('[data-row]');
            if (row) row.classList.toggle('selected', e.target.checked);
            updateBulkBar();
        }
    });

    browser.addEventListener('click', async (e) => {
        // 3-dots trigger: build the context menu for this row's file/folder
        const menuTrig = e.target.closest('[data-row-menu]');
        if (menuTrig) {
            e.stopPropagation();
            const rowEl = menuTrig.closest('[data-row]');
            if (!rowEl) return;
            const id = parseInt(rowEl.dataset.row);
            const folder = view === 'files' ? folders.find(x => x.id === id) : null;
            const f = folder ? null : findFile(id);
            if (!folder && !f) return;
            openMenuFor(menuTrig, folder ? folderMenuItems(folder) : fileMenuItems(f));
            return;
        }

        // row click: open folder or preview file
        const row = e.target.closest('[data-row]');
        if (row && !e.target.closest('.cbx') && !e.target.closest('[data-row-menu]')) {
            const id = parseInt(row.dataset.row);
            const folder = folders.find(x => x.id === id);
            if (folder && view === 'files') openFolder(id);
            else {
                const f = findFile(id);
                if (f && fileType(f.filename) !== 'other') openPreview(f);
            }
        }
    });

    // Modals
    $('btn-create-link').addEventListener('click', createShareLink);
    $('btn-copy-link').addEventListener('click', async () => {
        const val = $('share-link-input').value;
        if (!val) return;
        await copyText(val);
        toast('Link copied to clipboard', 'success', 1800);
    });
    $('btn-rename-save').addEventListener('click', saveRename);
    $('rename-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') saveRename(); });
    $('btn-move-save').addEventListener('click', saveMove);
    $('btn-folder-save').addEventListener('click', createFolder);
    $('folder-name-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') createFolder(); });
    $('btn-new-folder').addEventListener('click', () => { $('folder-name-input').value = ''; openModal($('modal-folder')); });
    $('btn-empty-trash').addEventListener('click', emptyTrash);
    $('btn-preview-download').addEventListener('click', () => { if (previewFileRef) downloadFile(previewFileRef); });
    $('modal-preview').addEventListener('click', () => {
        if (previewURL) { URL.revokeObjectURL(previewURL); previewURL = null; }
    });
    $('modal-preview').addEventListener('click', (e) => {
        // revoke when closed via backdrop/close button
        if (e.target.classList.contains('modal-backdrop') || e.target.closest('[data-close]')) {
            if (previewURL) { URL.revokeObjectURL(previewURL); previewURL = null; }
        }
    });

    // Bulk
    $('btn-bulk-zip').addEventListener('click', bulkZip);
    $('btn-bulk-delete').addEventListener('click', bulkDelete);
    $('btn-bulk-clear').addEventListener('click', () => { selection.clear(); render(); });

    // Versions restore
    $('versions-list').addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-restore-version]');
        if (!btn || !versionsTarget) return;
        try {
            const res = await apiFetch(`/files/versions/${versionsTarget.id}/restore/${btn.dataset.restoreVersion}`, { method: 'POST' });
            toast(res.message, 'success');
            closeModal($('modal-versions'));
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    });

    /* ---------------- Init ---------------- */
    try {
        await loadTrash();
        await loadData();
    } catch (err) {
        toast('Failed to load workspace: ' + err.message, 'error', 6000);
    }
});
