document.addEventListener('DOMContentLoaded', async () => {
    if (!getToken() && !getRefreshToken()) {
        window.location.href = '/auth.html';
        return;
    }

    /* ---------------- State ---------------- */
    let user = null;
    let view = 'files';
    let currentFolder = null;          // folder id or null (root)
    let files = [];                    // active latest files
    let trashFiles = [];
    let folders = [];
    let plans = [];
    let selection = new Set();
    let searchQ = '';
    let viewMode = localStorage.getItem('storix.viewMode') || 'list';
    let sortMode = 'date-desc';
    let previewURL = null;
    let previewSeq = 0;
    let previewFileRef = null;
    let shareFileRef = null;
    let draggingFileIds = [];

    /* ---------------- Elements ---------------- */
    const $ = (id) => document.getElementById(id);
    const browser = $('browser');
    const fileInput = $('file-input');
    const dropzone = $('dropzone');

    /* ---------------- Icons ---------------- */
    document.querySelectorAll('[data-ic]').forEach(el => { el.innerHTML = icon(el.dataset.ic); });
    $('logo-mark').innerHTML = icon('logo');

    /* ---------------- Data loading ---------------- */
    async function fetchAllPages(endpoint, pageSize = 1000, maxPages = 10) {
        const rows = [];
        for (let page = 0; page < maxPages; page += 1) {
            const batch = await apiFetch(`${endpoint}?limit=${pageSize}&offset=${page * pageSize}`);
            rows.push(...batch);
            if (batch.length < pageSize) break;
        }
        return rows;
    }

    async function loadData() {
        const [me, fs, fds, tr, pl] = await Promise.all([
            apiFetch('/auth/me'),
            fetchAllPages('/files/get-files'),
            apiFetch('/files/folders').catch(() => []),
            fetchAllPages('/files/trash').catch(() => []),
            apiFetch('/plans/').catch(() => []),
        ]);
        user = me;
        files = fs;
        folders = fds;
        trashFiles = tr;
        plans = pl;
        updateSidebar();
        render();
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
        const pct = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
        const bar = $('sb-bar');
        bar.style.width = `${pct}%`;
        bar.classList.toggle('warn', pct > 75 && pct <= 90);
        bar.classList.toggle('full', pct > 90);
        $('sb-used').textContent = formatBytes(used);
        $('sb-limit').textContent = formatBytes(limit);

        if (user.role === 'admin') $('nav-admin').style.display = 'flex';

        const countEl = (n) => n > 0 ? n : '';
        $('nav-count-public').textContent = countEl(files.filter(f => f.is_public).length);
        $('nav-count-starred').textContent = countEl(files.filter(f => f.is_starred).length);
        $('nav-count-trash').textContent = countEl(trashFiles.length);

        $('dz-hint').textContent = `Up to ${formatBytes(user.plan.max_file_size)} per file on the ${user.plan.name} plan`;
        if (!user.plan.can_share) {
            $('dz-hint').textContent += ' · sharing requires Pro';
        }
    }

    /* ---------------- Views ---------------- */
    const VIEW_META = {
        files: { title: 'My Files', sub: () => `Files in ${currentFolderName() || 'root'}` },
        recent: { title: 'Recent', sub: () => 'Most recently uploaded files' },
        public: { title: 'Public', sub: () => 'Files that anyone with the link can see' },
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
        else if (view === 'public') list = files.filter(f => f.is_public);
        else if (view === 'starred') list = files.filter(f => f.is_starred);
        else if (view === 'recent') list = [...files];
        else list = files.filter(f => (currentFolder === null ? f.folder_id == null : f.folder_id === currentFolder));

        if (searchQ) {
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
        }[sortMode] || (() => 0);
        return [...list].sort(cmp);
    }

    /* ---------------- Rendering ---------------- */
    function render() {
        browser.scrollTop = 0;
        const meta = VIEW_META[view];
        $('view-title').textContent = meta.title;
        $('view-sub').textContent = meta.sub();

        document.querySelectorAll('.nav-item[data-view]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === view);
        });

        const isFiles = view === 'files';
        $('btn-new-folder').style.display = isFiles ? 'inline-flex' : 'none';
        $('btn-empty-trash').style.display = view === 'trash' ? 'inline-flex' : 'none';
        dropzone.style.display = isFiles ? 'flex' : 'none';
        renderCrumbs();
        renderTypeBreakdown();

        const list = visibleFiles();
        const folderCount = view === 'files' && !searchQ ? visibleFolders().length : 0;
        if (searchQ) {
            $('view-sub').textContent = `${list.length} result${list.length === 1 ? '' : 's'} for "${searchQ}"`;
        } else if (view === 'files') {
            const parts = [`${list.length} ${list.length === 1 ? 'file' : 'files'}`];
            if (folderCount) parts.push(`${folderCount} ${folderCount === 1 ? 'folder' : 'folders'}`);
            $('view-sub').textContent = parts.join(' · ');
        } else {
            $('view-sub').textContent = meta.sub();
        }

        const visibleIds = new Set(list.map(f => f.id));
        selection.forEach(id => { if (!visibleIds.has(id)) selection.delete(id); });
        updateBulkBar();

        if (list.length === 0 && folderCount === 0) {
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
            public: { ic: 'globe', h: 'Nothing public', p: 'Mark a file as public to see it here.' },
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
        const chain = [];
        let cur = folders.find(f => f.id === currentFolder);
        const seen = new Set();
        while (cur && !seen.has(cur.id)) {
            seen.add(cur.id);
            chain.unshift(cur);
            cur = folders.find(f => f.id === cur.parent_id);
        }
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
        const colors = { image: '#60a5fa', video: '#f472b6', audio: '#a78bfa', pdf: '#fb923c', doc: '#fb923c', archive: '#fbbf24', code: '#34d399', other: '#6b7280', more: '#4b5563' };
        const totals = {};
        files.forEach(f => {
            const t = fileType(f.filename);
            totals[t] = (totals[t] || 0) + f.size;
        });
        const totalBytes = files.reduce((s, f) => s + f.size, 0);
        if (totalBytes <= 0) {
            el.style.display = 'none';
            return;
        }

        const entries = Object.entries(totals).sort((a, b) => b[1] - a[1]);
        const top = entries.slice(0, 4);
        const remainder = entries.slice(4).reduce((sum, [, bytes]) => sum + bytes, 0);
        if (remainder > 0) top.push(['more', remainder]);

        const pct = (bytes) => (bytes / totalBytes * 100).toFixed(1);
        const label = top.map(([type, bytes]) => `${type} ${formatBytes(bytes)}`).join(', ');
        el.style.display = 'block';
        el.innerHTML = `
            <div class="type-summary">
                <span class="type-summary-label">Storage by type <span class="text-dim">· ${formatBytes(totalBytes)}</span></span>
                <div class="type-stack" role="img" aria-label="Storage by type: ${label}">
                    ${top.map(([type, bytes]) => `<span style="width:${pct(bytes)}%;background:${colors[type] || colors.more}"></span>`).join('')}
                </div>
                <div class="type-legend">
                    ${top.map(([type, bytes]) => `<span class="type-chip"><i style="background:${colors[type] || colors.more}"></i>${type}<span class="mono type-bytes">${formatBytes(bytes)}</span></span>`).join('')}
                </div>
            </div>`;
    }

    function starBtnHTML(f) {
        return `<button class="icon-action star-btn ${f.is_starred ? 'starred' : ''}" data-action="star" data-id="${f.id}" title="${f.is_starred ? 'Unstar' : 'Star'}" aria-label="${f.is_starred ? 'Unstar' : 'Star'} ${escapeHtml(f.filename)}">
            <svg viewBox="0 0 24 24" fill="${f.is_starred ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS.star}</svg></button>`;
    }

    function rowMenuHTML() {
        return `<button class="icon-action" data-row-menu title="More actions" aria-label="More actions">${icon('more')}</button>`;
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
            { id: 'share', label: 'Create share link…', icon: 'link', onSelect: () => {
                if (!user.plan.can_share) { toast('Sharing requires the Pro plan or above', 'error'); return; }
                openShare(f);
            } },
            { id: 'visibility', label: f.is_public ? 'Make private' : 'Make public', icon: 'globe', onSelect: () => toggleVisibility(f) },
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

    function visibleFolders() {
        if (view !== 'files' || searchQ) return [];
        return folders.filter(f => (f.parent_id ?? null) === currentFolder)
            .sort((a, b) => a.name.localeCompare(b.name));
    }

    function folderListHTML() {
        const fdrs = visibleFolders();
        if (!fdrs.length) return '';
        return fdrs.map(fd => `
            <tr class="file-row" data-kind="folder" data-id="${fd.id}" tabindex="0">
                <td style="width:36px"></td>
                <td><div class="file-name-cell">
                    <span class="ft-icon folder">${icon('folder')}</span>
                    <span class="fname">${escapeHtml(fd.name)}</span>
                    <span class="badge" style="margin-left:8px">folder</span>
                </div></td>
                <td class="text-mut mono col-size" style="width:90px">—</td>
                <td class="text-mut col-date" style="width:120px">—</td>
                <td style="width:130px"><div class="row-actions">${rowMenuHTML()}</div></td>
            </tr>`).join('');
    }

    function renderList(list) {
        const isTrash = view === 'trash';
        const rows = list.map(f => `
            <tr class="file-row ${selection.has(f.id) ? 'selected' : ''}" data-kind="file" data-id="${f.id}" tabindex="0"${view === 'files' ? ' draggable="true"' : ''}>
                <td style="width:36px"><input type="checkbox" class="cbx row-cbx" data-id="${f.id}" aria-label="Select ${escapeHtml(f.filename)}" ${selection.has(f.id) ? 'checked' : ''}></td>
                <td><div class="file-name-cell">
                    ${fileIconEl(f.filename)}
                    <span class="fname" title="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}${f.version > 1 ? ` <span class="badge" style="margin-left:6px">v${f.version}</span>` : ''}</span>
                    ${f.is_public && !isTrash ? `<span class="badge badge-accent" style="margin-left:8px">${icon('globe')}public</span>` : ''}
                </div></td>
                <td class="text-mut mono nowrap col-size" style="width:90px">${formatBytes(f.size)}</td>
                <td class="text-mut nowrap col-date" style="width:120px" title="${formatAbsoluteDate(isTrash ? f.deleted_at : f.uploaded_at)}">${isTrash ? 'deleted ' + formatDate(f.deleted_at) : formatDate(f.uploaded_at)}</td>
                <td style="width:130px">
                    <div class="row-actions">
                        ${isTrash ? '' : starBtnHTML(f)}
                        ${rowMenuHTML()}
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
                <tbody>${folderListHTML()}${rows}</tbody>
            </table>`;
    }

    function renderGrid(list) {
        const folderCards = visibleFolders().map(fd => `
            <div class="file-card" data-kind="folder" data-id="${fd.id}" tabindex="0">
                <div class="flex items-start justify-between">
                    <span class="ft-icon folder" style="width:42px;height:42px">${icon('folder')}</span>
                    <div class="row-actions">${rowMenuHTML()}</div>
                </div>
                <div class="card-name" title="${escapeHtml(fd.name)}">${escapeHtml(fd.name)}</div>
                <div class="text-dim text-[11.5px] mt-0.5">Folder</div>
            </div>`).join('');
        const cards = list.map(f => `
            <div class="file-card ${selection.has(f.id) ? 'selected' : ''}" data-kind="file" data-id="${f.id}" tabindex="0"${view === 'files' ? ' draggable="true"' : ''}>
                <div class="flex items-start justify-between">
                    ${fileIconEl(f.filename)}
                    <div class="row-actions">${rowMenuHTML()}</div>
                </div>
                <div class="card-name" title="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}</div>
                <div class="text-dim text-[11.5px] mt-0.5 mono">${formatBytes(f.size)} · ${formatDate(f.uploaded_at)}</div>
                <input type="checkbox" class="cbx row-cbx" data-id="${f.id}" aria-label="Select ${escapeHtml(f.filename)}" style="position:absolute;right:12px;bottom:12px" ${selection.has(f.id) ? 'checked' : ''}>
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
        const seen = new Set();
        const build = (parentId, depth) => {
            folders.filter(f => (f.parent_id ?? null) === parentId).forEach(f => {
                if (seen.has(f.id)) return;
                seen.add(f.id);
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
        return trashFiles.find(f => f.id === id) || files.find(f => f.id === id) || null;
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
            updateSidebar();
            toast(f.is_starred ? 'Added to starred' : 'Removed from starred', 'success', 1600);
        } catch (err) { toast(err.message, 'error'); }
    }

    async function toggleVisibility(f) {
        if (!user.plan.can_share && !f.is_public) {
            toast('Public files require the Pro plan or above', 'error');
            return;
        }
        try {
            const next = !f.is_public;
            await apiFetch(`/files/${f.id}/visibility`, {
                method: 'PUT',
                body: JSON.stringify({ choice: next }),
            });
            f.is_public = next;
            render();
            updateSidebar();
            toast(next ? 'File is now public' : 'File is now private', 'success');
        } catch (err) { toast(err.message, 'error'); }
    }

    async function deleteFile(f) {
        const ok = await confirmDialog(`Move "${f.filename}" to trash?`, {
            note: 'You can restore it from Trash later.',
            confirmText: 'Move to trash',
        });
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
        const ok = await confirmDialog(`Permanently delete "${f.filename}" and all its versions?`, {
            confirmText: 'Delete forever',
        });
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
            const res = await apiFetch(`/files/share/${shareFileRef.id}?expires_minutes=${encodeURIComponent(minutes)}`, { method: 'POST' });
            $('share-link-input').value = res.share_url;
            $('btn-create-link').style.display = 'none';
            const mins = Math.round((new Date(res.expires_at) - Date.now()) / 60000);
            const expiryText = mins >= 1440
                ? `in ${Math.round(mins / 1440)} day${mins >= 2880 ? 's' : ''}`
                : mins >= 60
                    ? `in ${Math.round(mins / 60)} hour${mins >= 120 ? 's' : ''}`
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
            await apiFetch(`/files/${renameTarget.id}/rename`, {
                method: 'PUT',
                body: JSON.stringify({ filename: $('rename-input').value }),
            });
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
            await apiFetch(`/files/${moveTarget.id}/move`, {
                method: 'PUT',
                body: JSON.stringify({ folder_id: val ? parseInt(val, 10) : null }),
            });
            closeModal($('modal-move'));
            toast('File moved', 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    function folderLabel(folderId) {
        if (folderId === null) return 'workspace root';
        const folder = folders.find(f => f.id === folderId);
        return folder ? `"${folder.name}"` : 'folder';
    }

    async function moveFilesToFolder(ids, folderId) {
        const targets = [...new Set(ids)]
            .map(id => files.find(f => f.id === id))
            .filter(f => f && (f.folder_id ?? null) !== folderId);
        if (!targets.length) return;

        const results = await Promise.allSettled(targets.map(f => apiFetch(`/files/${f.id}/move`, {
            method: 'PUT',
            body: JSON.stringify({ folder_id: folderId }),
        })));

        let moved = 0;
        results.forEach((result, i) => {
            if (result.status === 'fulfilled') {
                targets[i].folder_id = folderId;
                selection.delete(targets[i].id);
                moved += 1;
            }
        });

        const failed = targets.length - moved;
        if (moved) {
            render();
            updateSidebar();
        }
        if (failed) toast(`${moved} moved, ${failed} failed`, 'error');
        else toast(moved === 1 ? `Moved to ${folderLabel(folderId)}` : `${moved} files moved`, 'success');
    }

    async function createFolder() {
        const name = $('folder-name-input').value.trim();
        if (!name) return;
        try {
            await apiFetch('/files/folders', {
                method: 'POST',
                body: JSON.stringify({ name, parent_id: currentFolder }),
            });
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
            await apiFetch(`/files/folders/${folderId}/rename`, {
                method: 'PUT',
                body: JSON.stringify({ name }),
            });
            toast('Folder renamed', 'success');
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function deleteFolderFlow(folderId) {
        const folder = folders.find(f => f.id === folderId);
        const ok = await confirmDialog(`Delete folder "${folder ? folder.name : ''}" and move its files to trash?`, {
            note: 'Files inside can be restored from Trash.',
            confirmText: 'Delete folder',
        });
        if (!ok) return;
        try {
            const res = await apiFetch(`/files/folders/${folderId}`, { method: 'DELETE' });
            toast(res.message, 'success');
            if (currentFolder === folderId) currentFolder = null;
            await refreshAll();
        } catch (err) { toast(err.message, 'error'); }
    }

    /* ---------------- Preview ---------------- */
    function revokePreview() {
        if (previewURL) {
            URL.revokeObjectURL(previewURL);
            previewURL = null;
        }
    }

    function openPreview(f) {
        previewFileRef = f;
        const sequence = ++previewSeq;
        revokePreview();
        $('preview-title').textContent = f.filename;
        const stage = $('preview-stage');
        stage.innerHTML = `<div class="spinner"></div>`;
        openModal($('modal-preview'));

        const t = fileType(f.filename);
        const lowerName = f.filename.toLowerCase();

        fetchBlob(`/files/preview/${f.id}`).then(blob => {
            if (sequence !== previewSeq) return;
            if (t === 'image' && lowerName.endsWith('.svg')) {
                stage.innerHTML = `<div class="empty-state" style="padding:32px"><div class="empty-icon">${icon('image')}</div>
                    <h3>SVG preview disabled</h3><p>SVGs can contain active content. Download the file to inspect it safely.</p></div>`;
            } else if (t === 'image') {
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
                stage.innerHTML = `<iframe src="${previewURL}" title="PDF preview"></iframe>`;
            } else if (isTextFile(f.filename) && blob.size < 1024 * 1024) {
                blob.text().then(txt => {
                    if (sequence !== previewSeq) return;
                    stage.innerHTML = `<pre>${escapeHtml(txt.slice(0, 200000))}</pre>`;
                });
            } else {
                stage.innerHTML = `<div class="empty-state" style="padding:32px"><div class="empty-icon">${icon('file')}</div>
                    <h3>No inline preview</h3><p>Use the download button to get this file.</p></div>`;
            }
        }).catch(err => {
            if (sequence !== previewSeq) return;
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
                    <span class="ft-icon" style="width:30px;height:30px">${icon('history')}</span>
                    <div style="flex:1;min-width:0">
                        <div class="text-[13px] font-medium">Version ${v.version} ${v.is_latest ? '<span class="badge badge-accent" style="margin-left:6px">current</span>' : ''}</div>
                        <div class="text-[11.5px] text-dim mono" title="${formatAbsoluteDate(v.uploaded_at)}">${formatBytes(v.size)} · ${formatDate(v.uploaded_at)}</div>
                    </div>
                    ${!v.is_latest ? `<button class="btn btn-sm" data-restore-version="${v.id}">Restore</button>` : ''}
                </div>`).join('');
        } catch (err) {
            list.innerHTML = `<div class="empty-state" style="padding:32px"><h3>Failed to load versions</h3><p>${escapeHtml(err.message)}</p></div>`;
        }
    }

    /* ---------------- Plans ---------------- */
    function renderPlans() {
        const grid = $('plan-grid');
        if (!plans.length) {
            grid.innerHTML = `<div class="empty-state" style="padding:24px"><h3>Plans unavailable</h3><p>Could not load plan information.</p></div>`;
            return;
        }
        grid.innerHTML = plans.map(p => {
            const isCurrent = user && user.plan.name.toLowerCase() === p.name.toLowerCase();
            const canSwitch = Boolean(user && user.can_change_plan) && !isCurrent;
            const tag = p.name.toLowerCase() === 'pro' ? '<span class="plan-tag">Most popular</span>' : '';
            return `
            <div class="plan-card ${isCurrent ? 'current' : ''}">
                ${tag}
                <h3 class="font-semibold mb-1" style="font-size:16px">${escapeHtml(p.name)}</h3>
                <p class="mb-4"><span class="font-bold" style="font-size:26px;font-family:var(--font-display)">$${p.price}</span><span class="text-dim text-[12px]">/mo</span></p>
                <ul class="space-y-1.5 text-[13px] text-mut" style="list-style:none;padding:0;margin:0 0 16px">
                    <li>${formatBytes(p.storage_limit)} storage</li>
                    <li>${formatBytes(p.max_file_size)} max file size</li>
                    <li>${p.can_share ? 'Public files & share links' : 'Private files only'}</li>
                </ul>
                ${isCurrent
                    ? `<button class="btn w-full" disabled>Current plan</button>`
                    : canSwitch
                        ? `<button class="btn ${p.name.toLowerCase() === 'pro' ? 'btn-primary' : ''} w-full" data-select-plan="${escapeHtml(p.name)}">Switch to ${escapeHtml(p.name)}</button>`
                        : `<button class="btn w-full" disabled>Admin managed</button>`}
            </div>`;
        }).join('');
        $('plan-status').textContent = user && !user.can_change_plan
            ? 'Paid plan changes are managed by an administrator.'
            : '';
    }

    async function openPlans() {
        $('plan-status').textContent = '';
        try {
            plans = await apiFetch('/plans/');
            renderPlans();
            openModal($('modal-plan'));
        } catch (err) {
            toast('Could not load plans: ' + err.message, 'error');
        }
    }

    async function selectPlan(planName) {
        if (!user || !user.can_change_plan) return;
        const status = $('plan-status');
        status.style.color = 'var(--text-2)';
        status.textContent = `Switching to ${planName}…`;
        try {
            const res = await apiFetch('/plans/change-plan', { method: 'PUT', body: JSON.stringify({ plan: planName }) });
            status.style.color = 'var(--accent)';
            status.textContent = res.message;
            toast(res.message, 'success');
            const [me, pl] = await Promise.all([apiFetch('/auth/me'), apiFetch('/plans/')]);
            user = me;
            plans = pl;
            updateSidebar();
            renderPlans();
            setTimeout(() => {
                closeModal($('modal-plan'));
                status.textContent = '';
            }, 1400);
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
            const { file, ui, folderId } = uploadQueue.shift();
            try {
                const res = await uploadFileWithProgress(file, (p) => ui.setProgress(p), folderId);
                if (res.errors && res.errors.length) {
                    ui.fail(res.errors[0].detail);
                    failed++;
                } else {
                    const uploaded = res.uploaded && res.uploaded[0];
                    ui.done(`Uploaded${uploaded && uploaded.version > 1 ? ` — v${uploaded.version}` : ''}`);
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
        // Snapshot the destination: navigating during the queue must not move
        // the remaining files somewhere else.
        const destination = currentFolder;
        showUploadPanel();
        arr.forEach(file => uploadQueue.push({
            file,
            folderId: destination,
            ui: addUploadItem(file.name),
        }));
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
        const count = selection.size;
        const ok = await confirmDialog(`Move ${count} file${count === 1 ? '' : 's'} to trash?`, {
            note: 'You can restore them from Trash later.',
            confirmText: 'Move to trash',
        });
        if (!ok) return;
        let failed = 0;
        for (const id of [...selection]) {
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
        await loadData();
    }

    /* ---------------- View routing ---------------- */
    function applyView(next, { updateHash = true } = {}) {
        if (!VIEW_META[next]) next = 'files';
        view = next;
        currentFolder = null;
        selection.clear();
        searchQ = '';
        $('search-input').value = '';
        if (updateHash && window.location.hash !== `#${next}`) {
            window.location.hash = next;
        }
        render();
        closeSidebarMobile();
    }

    /* ---------------- Event wiring ---------------- */
    document.querySelectorAll('.nav-item[data-view]').forEach(btn => {
        btn.addEventListener('click', () => applyView(btn.dataset.view));
    });
    $('nav-admin').addEventListener('click', () => { window.location.href = '/admin.html'; });

    window.addEventListener('hashchange', () => {
        const next = window.location.hash.replace('#', '');
        if (VIEW_META[next] && next !== view) applyView(next, { updateHash: false });
    });

    const initialHash = window.location.hash.replace('#', '');
    if (VIEW_META[initialHash]) view = initialHash;

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

    $('btn-logout').addEventListener('click', async () => {
        await logout();
        window.location.href = '/auth.html';
    });

    // Plans
    $('btn-upgrade').addEventListener('click', openPlans);
    $('plan-grid').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-select-plan]');
        if (btn) selectPlan(btn.dataset.selectPlan);
    });

    // Upload
    $('btn-upload').addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('click', (e) => {
        if (e.target === dropzone || e.target.closest('p') || e.target.closest('span')) fileInput.click();
    });
    dropzone.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            fileInput.click();
        }
    });
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
        $('btn-view-mode').setAttribute('aria-label', $('btn-view-mode').title);
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
        openFolder(btn.dataset.crumb === 'root' ? null : parseInt(btn.dataset.crumb, 10));
    });

    // Browser delegation
    browser.addEventListener('change', (e) => {
        if (!e.target.classList.contains('row-cbx')) return;
        const id = parseInt(e.target.dataset.id, 10);
        if (e.target.checked) selection.add(id); else selection.delete(id);
        const row = e.target.closest('[data-kind="file"]');
        if (row) row.classList.toggle('selected', e.target.checked);
        updateBulkBar();
    });

    browser.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        if (e.target.closest('button, input, select, a, textarea')) return;
        const row = e.target.closest('[data-kind]');
        if (!row) return;
        e.preventDefault();
        row.click();
    });

    browser.addEventListener('click', async (e) => {
        const starBtn = e.target.closest('[data-action="star"]');
        if (starBtn) {
            e.stopPropagation();
            const f = findFile(parseInt(starBtn.dataset.id, 10));
            if (f) toggleStar(f);
            return;
        }

        const menuTrig = e.target.closest('[data-row-menu]');
        if (menuTrig) {
            e.stopPropagation();
            const rowEl = menuTrig.closest('[data-kind]');
            if (!rowEl) return;
            const id = parseInt(rowEl.dataset.id, 10);
            if (rowEl.dataset.kind === 'folder') {
                const folder = folders.find(x => x.id === id);
                if (folder) openMenuFor(menuTrig, folderMenuItems(folder));
            } else {
                const f = findFile(id);
                if (f) openMenuFor(menuTrig, fileMenuItems(f));
            }
            return;
        }

        const row = e.target.closest('[data-kind]');
        if (row && !e.target.closest('.cbx') && !e.target.closest('[data-row-menu]')) {
            const id = parseInt(row.dataset.id, 10);
            if (row.dataset.kind === 'folder') {
                if (view === 'files') openFolder(id);
            } else {
                const f = findFile(id);
                if (f) openPreview(f);
            }
        }
    });

    // Drag files onto a folder row to move them in, or onto "My Files" to move
    // them back out to the root.
    function dragFileIds(e) {
        try {
            const raw = e.dataTransfer.getData('application/x-storix-files');
            const parsed = raw ? JSON.parse(raw) : null;
            if (Array.isArray(parsed?.ids)) {
                return parsed.ids.map(id => parseInt(id, 10)).filter(Number.isFinite);
            }
        } catch { /* fall through to the local drag state */ }
        return [...draggingFileIds];
    }

    function clearDragState() {
        document.querySelectorAll('.dragging, .drop-target-active').forEach(el => {
            el.classList.remove('dragging', 'drop-target-active');
        });
        document.body.classList.remove('dragging-files');
        draggingFileIds = [];
    }

    browser.addEventListener('dragstart', (e) => {
        if (view !== 'files') { e.preventDefault(); return; }
        const row = e.target.closest('[data-kind="file"]');
        if (!row || e.target.closest('button, input, a, select')) { e.preventDefault(); return; }
        const id = parseInt(row.dataset.id, 10);
        const ids = selection.has(id) ? [...selection] : [id];
        draggingFileIds = ids;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('application/x-storix-files', JSON.stringify({ ids }));
        e.dataTransfer.setData('text/plain', ids.map(fileId => {
            const f = files.find(x => x.id === fileId);
            return f ? f.filename : String(fileId);
        }).join(', '));
        row.classList.add('dragging');
        document.body.classList.add('dragging-files');
    });

    browser.addEventListener('dragend', clearDragState);

    browser.addEventListener('dragover', (e) => {
        if (view !== 'files') return;
        const folderRow = e.target.closest('[data-kind="folder"]');
        if (!folderRow) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        folderRow.classList.add('drop-target-active');
    });

    browser.addEventListener('dragleave', (e) => {
        const folderRow = e.target.closest('[data-kind="folder"]');
        if (folderRow && !folderRow.contains(e.relatedTarget)) {
            folderRow.classList.remove('drop-target-active');
        }
    });

    browser.addEventListener('drop', (e) => {
        const folderRow = e.target.closest('[data-kind="folder"]');
        if (!folderRow) return;
        e.preventDefault();
        const ids = dragFileIds(e);
        clearDragState();
        if (ids.length) moveFilesToFolder(ids, parseInt(folderRow.dataset.id, 10));
    });

    function wireRootDropTarget(el) {
        el.addEventListener('dragover', (e) => {
            if (view !== 'files' || currentFolder === null) return;
            const rootCrumb = el.id === 'crumbs' ? e.target.closest('[data-crumb="root"]') : el;
            if (!rootCrumb) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            rootCrumb.classList.add('drop-target-active');
        });
        el.addEventListener('dragleave', (e) => {
            const rootCrumb = el.id === 'crumbs' ? e.target.closest('[data-crumb="root"]') : el;
            if (rootCrumb && !rootCrumb.contains(e.relatedTarget)) {
                rootCrumb.classList.remove('drop-target-active');
            }
        });
        el.addEventListener('drop', (e) => {
            if (view !== 'files' || currentFolder === null) return;
            const rootCrumb = el.id === 'crumbs' ? e.target.closest('[data-crumb="root"]') : el;
            if (!rootCrumb) return;
            e.preventDefault();
            const ids = dragFileIds(e);
            clearDragState();
            if (ids.length) moveFilesToFolder(ids, null);
        });
    }

    wireRootDropTarget($('crumbs'));
    wireRootDropTarget($('nav-files'));

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

    // Revoke preview object URLs only when the preview closes, never on
    // playback controls or the download button.
    $('modal-preview').addEventListener('click', (e) => {
        if (e.target.classList.contains('modal-backdrop') || e.target.closest('[data-close]')) {
            previewSeq++;
            revokePreview();
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && $('modal-preview').classList.contains('open')) {
            previewSeq++;
            revokePreview();
        }
    });
    window.addEventListener('beforeunload', revokePreview);

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
        await loadData();
    } catch (err) {
        toast('Failed to load workspace: ' + err.message, 'error', 6000);
    }
});
