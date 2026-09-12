document.addEventListener('DOMContentLoaded', async () => {
    if (!getToken()) {
        window.location.href = '/auth.html';
        return;
    }

    const $ = (id) => document.getElementById(id);
    document.querySelectorAll('[data-ic]').forEach(el => { el.innerHTML = icon(el.dataset.ic); });
    $('logo-mark').innerHTML = icon('logo');

    let me = null;

    function initialsOf(name) {
        return (name || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
    }

    async function load() {
        try {
            me = await apiFetch('/auth/me');
            if (me.role !== 'admin') {
                window.location.href = '/dashboard.html';
                return;
            }
            $('sb-avatar').textContent = initialsOf(me.full_name || me.username);
            $('sb-username').textContent = me.full_name || me.username;

            const [users] = await Promise.all([apiFetch('/admin/users')]);
            renderStats(users);
            renderUsers(users);
        } catch (err) {
            if (String(err.message).includes('Admin')) {
                window.location.href = '/dashboard.html';
                return;
            }
            toast('Failed to load admin data: ' + err.message, 'error', 6000);
        }
    }

    function renderStats(users) {
        const totalStorage = users.reduce((s, u) => s + (u.used_storage || 0), 0);
        const totalFiles = users.reduce((s, u) => s + (u.file_count || 0), 0);
        const stats = [
            { label: 'Users', value: users.length, ic: 'users' },
            { label: 'Files stored', value: totalFiles, ic: 'files' },
            { label: 'Total storage used', value: formatBytes(totalStorage), ic: 'drive' },
            { label: 'Paid plans', value: users.filter(u => u.plan !== 'Free').length, ic: 'zap' },
        ];
        $('stats-row').innerHTML = stats.map(s => `
            <div class="panel px-5 py-4 flex items-center gap-4">
                <span class="ft-icon" style="width:38px;height:38px">${icon(s.ic)}</span>
                <div>
                    <div class="text-[20px] font-semibold" style="font-family:var(--font-display)">${s.value}</div>
                    <div class="text-[12px] text-dim">${s.label}</div>
                </div>
            </div>`).join('');
    }

    function renderUsers(users) {
        const tbody = $('users-table').querySelector('tbody');
        tbody.innerHTML = users.map(u => `
            <tr class="file-row">
                <td><div class="file-name-cell">
                    <span class="ft-icon" style="width:30px;height:30px;font-size:11px;font-weight:600;color:var(--accent)">${initialsOf(u.full_name || u.username)}</span>
                    <div style="min-width:0">
                        <div class="fname">${escapeHtml(u.full_name || u.username)}${u.role === 'admin' ? ' <span class="badge badge-accent" style="margin-left:6px">admin</span>' : ''}</div>
                        <div class="text-[11.5px] text-dim">@${escapeHtml(u.username)}</div>
                    </div>
                </div></td>
                <td class="text-mut text-[12.5px]">${escapeHtml(u.email)}</td>
                <td><span class="badge ${u.plan !== 'Free' ? 'badge-accent' : ''}">${escapeHtml(u.plan)}</span></td>
                <td class="text-mut mono">${u.file_count}</td>
                <td class="text-mut mono">${formatBytes(u.used_storage)}</td>
                <td>
                    <div class="flex items-center gap-2">
                        <select class="select" style="width:auto;padding:5px 28px 5px 10px;font-size:12px" data-plan-user="${u.id}">
                            ${['Free', 'Pro', 'Premium'].map(p => `<option ${p === u.plan ? 'selected' : ''}>${p}</option>`).join('')}
                        </select>
                        <button class="icon-action danger" data-del-user="${u.id}" title="Delete user">${icon('trash')}</button>
                    </div>
                </td>
            </tr>`).join('');
    }

    document.addEventListener('change', async (e) => {
        const sel = e.target.closest('[data-plan-user]');
        if (!sel) return;
        try {
            const res = await apiFetch(`/admin/users/${sel.dataset.planUser}/plan`, {
                method: 'PUT',
                body: JSON.stringify({ plan: sel.value })
            });
            toast(res.message, 'success');
            load();
        } catch (err) { toast(err.message, 'error'); load(); }
    });

    document.addEventListener('click', async (e) => {
        const del = e.target.closest('[data-del-user]');
        if (del) {
            const ok = await confirmDialog('Delete this user and all their files?', { confirmText: 'Delete user' });
            if (!ok) return;
            try {
                const res = await apiFetch(`/admin/users/${del.dataset.delUser}`, { method: 'DELETE' });
                toast(res.message, 'success');
                load();
            } catch (err) { toast(err.message, 'error'); }
        }
    });

    $('btn-reload').addEventListener('click', load);
    $('btn-logout').addEventListener('click', () => { removeToken(); window.location.href = '/auth.html'; });
    document.querySelector('[data-goto="/dashboard.html"]').addEventListener('click', () => {
        window.location.href = '/dashboard.html';
    });
    $('btn-menu').addEventListener('click', () => {
        $('sidebar').classList.add('open');
        $('scrim').classList.add('show');
    });
    $('scrim').addEventListener('click', () => {
        $('sidebar').classList.remove('open');
        $('scrim').classList.remove('show');
    });

    load();
});
