// ===== State =====
let authToken = null;
let currentUser = null;
let parameterDefs = {};
let selectedFile = null;
let originalFile = null;
let uploadedImageFilename = null;
let currentDetailView = null;
let progressInterval = null;
let queueInterval = null;

// Crop state
let cropRect = null;
let cropDragging = false;
let cropStartX = 0, cropStartY = 0;
let cropCanvas = null;
let cropCtx = null;
let cropImg = null;

const API = '/api';

// ===== API helpers =====
async function apiFetch(path, options = {}) {
    const headers = options.headers || {};
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    }
    const resp = await fetch(API + path, { ...options, headers });
    if (resp.status === 401) {
        logout();
        throw new Error('Unauthorized');
    }
    if (!resp.ok) {
        const data = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(data.detail || 'Request failed');
    }
    if (resp.headers.get('content-type')?.includes('application/json')) {
        return resp.json();
    }
    return resp;
}

function showToast(msg) {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.style.display = 'block';
    setTimeout(() => toast.style.display = 'none', 3500);
}

function authUrl(path) {
    const sep = path.includes('?') ? '&' : '?';
    return path + sep + 'token=' + encodeURIComponent(authToken);
}

// ===== Auth =====
function initAuth() {
    const saved = localStorage.getItem('pixal3d_token');
    const savedUser = localStorage.getItem('pixal3d_user');
    if (saved && savedUser) {
        authToken = saved;
        currentUser = JSON.parse(savedUser);
        showMainApp();
        return;
    }
    showAuthPage();
}

function showAuthPage() {
    document.getElementById('auth-page').style.display = 'flex';
    document.getElementById('main-app').style.display = 'none';
    applyTranslations();
    const langBtn = document.getElementById('lang-switch-auth');
    if (langBtn) langBtn.textContent = currentLang === 'zh' ? 'EN' : '中文';
}

function showMainApp() {
    document.getElementById('auth-page').style.display = 'none';
    document.getElementById('main-app').style.display = 'flex';
    applyTranslations();

    const badge = document.getElementById('user-badge');
    badge.textContent = currentUser.username;
    badge.classList.toggle('admin', currentUser.role === 'admin');
    if (currentUser.role === 'admin') {
        badge.textContent += t('auth.admin_suffix');
        document.getElementById('admin-nav-btn').style.display = 'flex';
    }

    loadParameters();
    startQueuePolling();
    if (currentUser.role === 'admin') {
        document.getElementById('admin-filters').style.display = 'flex';
    }
    navigateTo('create');
}

function logout() {
    authToken = null;
    currentUser = null;
    localStorage.removeItem('pixal3d_token');
    localStorage.removeItem('pixal3d_user');
    if (queueInterval) clearInterval(queueInterval);
    showAuthPage();
}

// Auth tabs
document.querySelectorAll('.auth-tab').forEach(tab => {
    tab.onclick = () => {
        document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
        document.getElementById(tab.dataset.tab + '-form').classList.add('active');
    };
});

// Login form
document.getElementById('login-form').onsubmit = async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('login-error');
    errEl.textContent = '';
    try {
        const resp = await fetch(API + '/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: document.getElementById('login-username').value,
                password: document.getElementById('login-password').value,
            })
        });
        if (!resp.ok) {
            const data = await resp.json();
            throw new Error(data.detail || 'Login failed');
        }
        const data = await resp.json();
        authToken = data.access_token;
        currentUser = { username: data.username, role: data.role };
        localStorage.setItem('pixal3d_token', authToken);
        localStorage.setItem('pixal3d_user', JSON.stringify(currentUser));
        showMainApp();
    } catch (err) {
        errEl.textContent = err.message;
    }
};

// ===== Navigation =====
function navigateTo(view) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const viewEl = document.getElementById('view-' + view);
    if (viewEl) viewEl.classList.add('active');

    if (view === 'history') loadTaskHistory();
    if (view === 'admin') loadAdminUsers();
}

// ===== Parameters =====
async function loadParameters() {
    try {
        parameterDefs = await apiFetch('/parameters');
        renderParameters();
    } catch (err) {
        showToast(t('msg.params_failed') + err.message);
    }
}

function renderParameters() {
    const panel = document.getElementById('param-panel');
    const groups = {};
    for (const [key, def] of Object.entries(parameterDefs)) {
        const g = def.group || 'Other';
        if (!groups[g]) groups[g] = [];
        groups[g].push({ key, ...def });
    }

    let html = `<h3 style="font-size:0.8rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--text-dim);margin-bottom:1rem;">${t('create.params')}</h3>`;

    for (const [groupName, params] of Object.entries(groups)) {
        html += `<div class="param-group"><div class="param-group-title">${tGroup(groupName)}</div>`;
        for (const p of params) {
            html += renderParamItem(p);
        }
        html += '</div>';
    }

    panel.innerHTML = html;
    attachParamListeners();
}

function renderParamItem(p) {
    const label = tParam(p.label);
    const tooltip = tParamTooltip(p.key);
    const tooltipHtml = `<span class="tooltip-icon">?<span class="tooltip-text">${tooltip}</span></span>`;
    let controlHtml = '';

    if (p.type === 'select') {
        const opts = p.options.map(o => `<option value="${o.value}" ${o.value === p.default ? 'selected' : ''}>${o.label}</option>`).join('');
        controlHtml = `<select id="param-${p.key}">${opts}</select>`;
    } else if (p.type === 'bool') {
        controlHtml = `<div class="param-checkbox-row"><input type="checkbox" id="param-${p.key}" ${p.default ? 'checked' : ''}></div>`;
    } else if (p.type === 'int' || p.type === 'float') {
        const isRange = (p.max - p.min) <= 100 || p.type === 'float';
        if (isRange && p.key !== 'seed' && p.key !== 'max_num_tokens' && p.key !== 'decimation_target' && p.key !== 'texture_size' && p.key !== 'image_resolution') {
            controlHtml = `
                <input type="range" id="param-${p.key}" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.default}" oninput="updateParamDisplay('${p.key}')">
                <div class="param-value" id="param-${p.key}-val">${p.default}</div>
            `;
        } else {
            controlHtml = `<input type="number" id="param-${p.key}" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.default}">`;
        }
    }

    if (p.key === 'manual_fov') {
        return `
            <div class="param-item">
                <div class="param-label-row">
                    <span class="param-label">${label} ${tooltipHtml}</span>
                    <label style="display:flex;align-items:center;gap:4px;font-size:0.75rem;color:var(--text-dim);cursor:pointer;">
                        <input type="checkbox" id="fov-auto" checked onchange="toggleFovAuto()" style="accent-color:var(--primary);width:14px;height:14px;"> Auto
                    </label>
                </div>
                <div class="param-row-2">
                    <input type="number" id="param-manual_fov" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.default}" disabled style="opacity:0.4;">
                    <select id="param-fov_unit" disabled style="opacity:0.4;">
                        <option value="deg">deg</option>
                        <option value="rad">rad</option>
                    </select>
                </div>
            </div>
        `;
    }
    if (p.key === 'fov_unit') return '';

    return `
        <div class="param-item">
            <div class="param-label-row">
                <span class="param-label">${label} ${tooltipHtml}</span>
            </div>
            ${controlHtml}
        </div>
    `;
}

function toggleFovAuto() {
    const auto = document.getElementById('fov-auto').checked;
    const fovInput = document.getElementById('param-manual_fov');
    const fovUnit = document.getElementById('param-fov_unit');
    fovInput.disabled = auto;
    fovUnit.disabled = auto;
    fovInput.style.opacity = auto ? '0.4' : '1';
    fovUnit.style.opacity = auto ? '0.4' : '1';
    if (auto) {
        fovInput.value = '-1';
    } else {
        fovInput.value = '11.5';
    }
}

function updateParamDisplay(key) {
    const input = document.getElementById('param-' + key);
    const valEl = document.getElementById('param-' + key + '-val');
    if (input && valEl) {
        valEl.textContent = input.value;
    }
}

function attachParamListeners() {}

function collectParameters() {
    const params = {};
    for (const key of Object.keys(parameterDefs)) {
        if (key === 'fov_unit') {
            const el = document.getElementById('param-fov_unit');
            params[key] = el ? el.value : 'deg';
            continue;
        }
        if (key === 'manual_fov') {
            const auto = document.getElementById('fov-auto').checked;
            if (auto) {
                params[key] = -1.0;
            } else {
                const el = document.getElementById('param-manual_fov');
                const unit = document.getElementById('param-fov_unit').value;
                let val = parseFloat(el.value);
                if (unit === 'rad') val = val * 180 / Math.PI;
                params[key] = val;
            }
            continue;
        }
        const el = document.getElementById('param-' + key);
        if (!el) {
            params[key] = parameterDefs[key].default;
            continue;
        }
        const def = parameterDefs[key];
        if (def.type === 'bool') {
            params[key] = el.checked;
        } else if (def.type === 'int') {
            params[key] = parseInt(el.value);
        } else if (def.type === 'float') {
            params[key] = parseFloat(el.value);
        } else {
            params[key] = el.value;
        }
    }
    return params;
}

// ===== Upload =====
const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');

uploadZone.ondragover = (e) => { e.preventDefault(); uploadZone.style.borderColor = 'var(--primary)'; };
uploadZone.ondragleave = () => uploadZone.style.borderColor = 'var(--border)';
uploadZone.ondrop = (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files[0]);
};
fileInput.onchange = (e) => { if (e.target.files.length) handleFileUpload(e.target.files[0]); };

async function handleFileUpload(file) {
    selectedFile = file;
    originalFile = file;
    uploadedImageFilename = null;
    const reader = new FileReader();
    reader.onload = (e) => {
        const preview = document.getElementById('source-preview');
        if (preview) {
            preview.src = e.target.result;
            preview.style.display = 'block';
        }
        const hint = document.getElementById('upload-hint');
        if (hint) hint.style.display = 'none';
    };
    reader.readAsDataURL(file);
    const cropToolbar = document.getElementById('crop-toolbar');
    if (cropToolbar) cropToolbar.style.display = 'flex';
    document.getElementById('submit-btn').disabled = false;
}

// ===== Image Cropping =====
function enterCropMode() {
    if (!selectedFile) return;
    const reader = new FileReader();
    reader.onload = (e) => {
        cropImg = new Image();
        cropImg.onload = () => {
            const maxW = 600, maxH = 400;
            let w = cropImg.width, h = cropImg.height;
            const scale = Math.min(maxW / w, maxH / h, 1);
            w = Math.round(w * scale);
            h = Math.round(h * scale);

            cropCanvas = document.getElementById('crop-canvas');
            cropCanvas.width = w;
            cropCanvas.height = h;
            cropCtx = cropCanvas.getContext('2d');
            cropCtx.drawImage(cropImg, 0, 0, w, h);

            cropRect = null;
            cropDragging = false;

            cropCanvas.onmousedown = onCropMouseDown;
            cropCanvas.onmousemove = onCropMouseMove;
            cropCanvas.onmouseup = onCropMouseUp;
            cropCanvas.onmouseleave = onCropMouseUp;

            document.getElementById('upload-zone').style.display = 'none';
            document.getElementById('crop-toolbar').style.display = 'none';
            document.getElementById('crop-editor').style.display = 'block';
        };
        cropImg.src = e.target.result;
    };
    reader.readAsDataURL(selectedFile);
}

function getCanvasPos(e) {
    const rect = cropCanvas.getBoundingClientRect();
    return {
        x: Math.round((e.clientX - rect.left) * (cropCanvas.width / rect.width)),
        y: Math.round((e.clientY - rect.top) * (cropCanvas.height / rect.height))
    };
}

function onCropMouseDown(e) {
    const pos = getCanvasPos(e);
    cropDragging = true;
    cropStartX = pos.x;
    cropStartY = pos.y;
    cropRect = { x: pos.x, y: pos.y, w: 0, h: 0 };
}

function onCropMouseMove(e) {
    if (!cropDragging) return;
    const pos = getCanvasPos(e);
    cropRect = {
        x: Math.min(cropStartX, pos.x),
        y: Math.min(cropStartY, pos.y),
        w: Math.abs(pos.x - cropStartX),
        h: Math.abs(pos.y - cropStartY)
    };
    drawCropOverlay();
}

function onCropMouseUp(e) {
    cropDragging = false;
}

function drawCropOverlay() {
    cropCtx.clearRect(0, 0, cropCanvas.width, cropCanvas.height);
    cropCtx.drawImage(cropImg, 0, 0, cropCanvas.width, cropCanvas.height);
    if (cropRect && cropRect.w > 0 && cropRect.h > 0) {
        cropCtx.fillStyle = 'rgba(0,0,0,0.55)';
        cropCtx.fillRect(0, 0, cropCanvas.width, cropCanvas.height);
        cropCtx.drawImage(cropImg,
            cropRect.x * (cropImg.width / cropCanvas.width),
            cropRect.y * (cropImg.height / cropCanvas.height),
            cropRect.w * (cropImg.width / cropCanvas.width),
            cropRect.h * (cropImg.height / cropCanvas.height),
            cropRect.x, cropRect.y, cropRect.w, cropRect.h);
        cropCtx.strokeStyle = '#818cf8';
        cropCtx.lineWidth = 2;
        cropCtx.strokeRect(cropRect.x, cropRect.y, cropRect.w, cropRect.h);
    }
}

function applyCrop() {
    if (!cropRect || cropRect.w < 10 || cropRect.h < 10) {
        showToast(t('msg.selection_small'));
        return;
    }
    const scaleX = cropImg.width / cropCanvas.width;
    const scaleY = cropImg.height / cropCanvas.height;
    const sx = Math.round(cropRect.x * scaleX);
    const sy = Math.round(cropRect.y * scaleY);
    const sw = Math.round(cropRect.w * scaleX);
    const sh = Math.round(cropRect.h * scaleY);

    const out = document.createElement('canvas');
    out.width = sw;
    out.height = sh;
    out.getContext('2d').drawImage(cropImg, sx, sy, sw, sh, 0, 0, sw, sh);

    out.toBlob((blob) => {
        selectedFile = new File([blob], 'cropped.png', { type: 'image/png' });
        const reader = new FileReader();
        reader.onload = (e) => {
            document.getElementById('source-preview').src = e.target.result;
        };
        reader.readAsDataURL(selectedFile);
        exitCropMode();
        showToast(t('msg.cropped'));
    }, 'image/png');
}

function cancelCrop() {
    exitCropMode();
}

function exitCropMode() {
    document.getElementById('crop-editor').style.display = 'none';
    document.getElementById('upload-zone').style.display = 'flex';
    document.getElementById('crop-toolbar').style.display = 'flex';
    cropRect = null;
}

function resetCrop() {
    if (!originalFile) return;
    selectedFile = originalFile;
    const reader = new FileReader();
    reader.onload = (e) => {
        document.getElementById('source-preview').src = e.target.result;
    };
    reader.readAsDataURL(selectedFile);
    showToast(t('msg.reverted'));
}

// ===== Submit Task =====
async function submitTask() {
    if (!selectedFile) return;
    const params = collectParameters();

    try {
        showProgress();
        document.getElementById('progress-stage').textContent = t('create.uploading');

        const formData = new FormData();
        formData.append('file', selectedFile);
        const uploadData = await apiFetch('/tasks/upload', { method: 'POST', body: formData });
        uploadedImageFilename = uploadData.filename;

        document.getElementById('progress-stage').textContent = t('create.submitting');
        const taskFormData = new FormData();
        taskFormData.append('image_filename', uploadedImageFilename);
        taskFormData.append('parameters', JSON.stringify(params));
        const data = await apiFetch('/tasks', { method: 'POST', body: taskFormData });

        startProgressPolling(data.id);
        showToast(t('msg.submitted'));
    } catch (err) {
        hideProgress();
        showToast(t('msg.submit_failed') + err.message);
    }
}

// ===== Progress =====
function showProgress() {
    document.getElementById('progress-overlay').style.display = 'flex';
    document.getElementById('progress-stage').textContent = t('msg.submitting');
    document.getElementById('progress-fill').style.width = '0%';
    document.getElementById('progress-step').textContent = '';
}

function hideProgress() {
    document.getElementById('progress-overlay').style.display = 'none';
}

function startProgressPolling(taskId) {
    if (progressInterval) clearInterval(progressInterval);
    progressInterval = setInterval(async () => {
        try {
            const q = await apiFetch('/queue/status');
            if (q.your_position > 0) {
                document.getElementById('progress-stage').textContent = t('msg.queue_ahead') + q.your_position + t('msg.queue_ahead_suffix');
                document.getElementById('progress-step').textContent = t('msg.waiting');
                document.getElementById('progress-fill').style.width = '0%';
                return;
            }

            const task = await apiFetch(`/tasks/${taskId}`);
            if (task.status === 'completed') {
                clearInterval(progressInterval);
                hideProgress();
                showToast(t('msg.completed'));
                loadTaskDetail(taskId);
                return;
            }
            if (task.status === 'failed') {
                clearInterval(progressInterval);
                hideProgress();
                showToast(t('msg.task_failed') + task.error_message);
                return;
            }

            document.getElementById('progress-stage').textContent = task.progress || t('msg.processing');
            if (task.progress_total > 0) {
                document.getElementById('progress-step').textContent = `${task.progress_step}/${task.progress_total}`;
                document.getElementById('progress-fill').style.width = Math.min(100, (task.progress_step / task.progress_total) * 100) + '%';
            } else {
                document.getElementById('progress-step').textContent = '';
                document.getElementById('progress-fill').style.width = '0%';
            }
        } catch (e) { /* ignore */ }
    }, 1000);
}

// ===== Queue polling =====
function startQueuePolling() {
    async function poll() {
        try {
            const q = await apiFetch('/queue/status');
            const badge = document.getElementById('queue-badge');
            const count = q.total_waiting + (q.gpu_busy ? 1 : 0);
            document.getElementById('queue-count').textContent = count;
            badge.classList.toggle('busy', count > 0);
        } catch (e) { /* ignore */ }
    }
    poll();
    queueInterval = setInterval(poll, 3000);
}

// ===== Task History =====
async function loadTaskHistory() {
    try {
        if (currentUser.role === 'admin') {
            const filterSelect = document.getElementById('filter-user');
            if (filterSelect && filterSelect.options.length <= 1) {
                try {
                    const users = await apiFetch('/users');
                    filterSelect.innerHTML = `<option value="">${t('history.all_users')}</option>` +
                        users.map(u => `<option value="${u.id}">${u.username} (#${u.id})</option>`).join('');
                } catch (e) { /* ignore */ }
            }
        }

        let path = '/tasks';
        const params = new URLSearchParams();
        const filterUser = document.getElementById('filter-user')?.value;
        const filterFrom = document.getElementById('filter-date-from')?.value;
        const filterTo = document.getElementById('filter-date-to')?.value;
        if (filterUser) params.set('user_id', filterUser);
        if (filterFrom) params.set('date_from', filterFrom + 'T00:00:00');
        if (filterTo) params.set('date_to', filterTo + 'T23:59:59');
        if (params.toString()) path += '?' + params.toString();

        const tasks = await apiFetch(path);
        const list = document.getElementById('task-list');
        if (tasks.length === 0) {
            list.innerHTML = `<div class="empty-state"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 3L2 8l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/></svg><p>${t('history.empty')}</p></div>`;
            return;
        }
        list.innerHTML = tasks.map(task => {
            const statusBadge = `<span class="status-badge status-${task.status}">${tStatus(task.status)}</span>`;
            const userLabel = currentUser.role === 'admin' ? ` (${t('history.user')} #${task.user_id})` : '';
            return `
            <div class="task-card" onclick="loadTaskDetail(${task.id})">
                <img class="task-thumb" src="${authUrl('/api/tasks/' + task.id + '/image')}" alt="" onerror="this.style.display='none'">
                <div class="task-info">
                    <h4>Task #${task.id}${userLabel}</h4>
                    <div class="task-meta">
                        ${statusBadge}
                        <span>${new Date(task.created_at).toLocaleString()}</span>
                        <span>${t('history.res')}: ${task.parameters?.resolution || '-'}</span>
                        <span>${t('history.seed')}: ${task.parameters?.seed || '-'}</span>
                    </div>
                    ${task.status === 'processing' && task.progress ? `<div style="font-size:0.72rem;color:var(--text-dim);margin-top:0.3rem;">${task.progress} ${task.progress_total > 0 ? '(' + task.progress_step + '/' + task.progress_total + ')' : ''}</div>` : ''}
                    ${task.status === 'failed' ? `<div style="font-size:0.72rem;color:var(--danger);margin-top:0.3rem;">${task.error_message}</div>` : ''}
                </div>
            </div>
        `}).join('');
    } catch (err) {
        showToast(t('msg.tasks_failed') + err.message);
    }
}

// ===== Task Detail =====
async function loadTaskDetail(taskId) {
    try {
        const task = await apiFetch(`/tasks/${taskId}`);
        currentDetailView = taskId;
        navigateTo('detail');
        document.getElementById('detail-title').textContent = `Task #${taskId}`;

        const content = document.getElementById('detail-content');
        let html = '';

        html += '<div class="detail-section">';
        html += `<h3>${t('detail.result')}</h3>`;
        if (task.status === 'completed' && task.output_glb_path) {
            const imgUrl = authUrl('/api/tasks/' + taskId + '/image');
            const glbUrl = authUrl('/api/tasks/' + taskId + '/download');
            html += `
                <div class="compare-layout">
                    <div class="compare-panel">
                        <div class="compare-label">${t('detail.source_image')}</div>
                        <img class="compare-image" src="${imgUrl}" alt="Source">
                    </div>
                    <div class="compare-panel">
                        <div class="compare-label">${t('detail.3d_model')}</div>
                        <div class="viewer-wrapper">
                            <model-viewer src="${glbUrl}" camera-controls auto-rotate shadow-intensity="1.5" environment-image="neutral" exposure="1.2"></model-viewer>
                        </div>
                    </div>
                </div>
                <div style="display:flex;gap:0.5rem;margin-top:0.75rem;">
                    <a href="${glbUrl}" download class="btn btn-primary btn-sm">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        ${t('detail.download_glb')}
                    </a>
                    <button class="btn btn-danger btn-sm" onclick="deleteTask(${taskId})">${t('detail.delete')}</button>
                </div>
            `;
        } else if (task.status === 'failed') {
            html += `<div class="empty-state" style="padding:2rem;"><p style="color:var(--danger)">${t('detail.task_failed')}${task.error_message}</p></div>`;
        } else if (task.status === 'processing') {
            html += `<div class="empty-state" style="padding:2rem;"><div class="loader-ring" style="margin:0 auto 1rem;"></div><p>${t('detail.processing')}${task.progress}</p></div>`;
        } else {
            html += `<div class="empty-state" style="padding:2rem;"><p>${t('detail.task_is')}${tStatus(task.status)}</p></div>`;
        }
        html += '</div>';

        html += '<div class="detail-section">';
        html += `<h3>${t('detail.renders')}</h3>`;
        if (task.render_paths && Object.keys(task.render_paths).length > 0) {
            for (const [mode, frames] of Object.entries(task.render_paths)) {
                html += `<div style="margin-bottom:0.75rem;"><div style="font-size:0.75rem;color:var(--text-dim);margin-bottom:0.3rem;">${mode}</div><div class="render-gallery">`;
                for (let i = 0; i < frames.length; i++) {
                    html += `<img src="${authUrl('/api/tasks/' + taskId + '/render/' + mode + '/' + i)}" alt="${mode} ${i}">`;
                }
                html += '</div></div>';
            }
        } else {
            html += `<p style="color:var(--text-dim);font-size:0.8rem;">${t('detail.no_renders')}</p>`;
        }

        html += `<h3 style="margin-top:1.5rem;">${t('detail.params')}</h3>`;
        html += '<div class="param-summary">';
        if (task.camera_angle_x) {
            html += `<span class="key">${t('detail.fov')}</span><span class="val">${task.camera_angle_x}</span>`;
            html += `<span class="key">${t('detail.distance')}</span><span class="val">${task.camera_distance}</span>`;
        }
        if (task.parameters) {
            for (const [k, v] of Object.entries(task.parameters)) {
                html += `<span class="key">${k}</span><span class="val">${v}</span>`;
            }
        }
        html += '</div>';
        html += '</div>';

        content.innerHTML = html;

        if (task.status === 'processing' || task.status === 'queued') {
            setTimeout(() => { if (currentDetailView === taskId) loadTaskDetail(taskId); }, 3000);
        }
    } catch (err) {
        showToast(t('msg.task_failed_load') + err.message);
    }
}

async function deleteTask(taskId) {
    if (!confirm(t('msg.delete_task'))) return;
    try {
        await apiFetch(`/tasks/${taskId}`, { method: 'DELETE' });
        showToast(t('msg.task_deleted'));
        navigateTo('history');
        loadTaskHistory();
    } catch (err) {
        showToast(t('msg.delete_failed') + err.message);
    }
}

// ===== Admin =====
async function loadAdminUsers() {
    try {
        const users = await apiFetch('/users');

        const filterSelect = document.getElementById('filter-user');
        if (filterSelect) {
            const currentVal = filterSelect.value;
            filterSelect.innerHTML = `<option value="">${t('history.all_users')}</option>` +
                users.map(u => `<option value="${u.id}">${u.username} (#${u.id})</option>`).join('');
            filterSelect.value = currentVal;
        }

        const tbody = document.getElementById('admin-users-tbody');
        tbody.innerHTML = users.map(u => `
            <tr>
                <td>${u.id}</td>
                <td>${u.username}</td>
                <td>${u.email || '-'}</td>
                <td>
                    <select class="role-select" onchange="updateUserRole(${u.id}, this.value)" ${u.username === currentUser.username ? 'disabled' : ''}>
                        <option value="user" ${u.role === 'user' ? 'selected' : ''}>user</option>
                        <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>admin</option>
                    </select>
                </td>
                <td>
                    <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
                        <input type="checkbox" ${u.is_active ? 'checked' : ''} onchange="toggleUserActive(${u.id}, this.checked)" style="accent-color:var(--accent);" ${u.username === currentUser.username ? 'disabled' : ''}>
                        ${u.is_active ? t('admin.yes') : t('admin.no')}
                    </label>
                </td>
                <td>${new Date(u.created_at).toLocaleDateString()}</td>
                <td>
                    ${u.username === currentUser.username ? '' : `<button class="btn btn-danger btn-sm" onclick="deleteUser(${u.id})">${t('admin.delete')}</button>`}
                </td>
            </tr>
        `).join('');
    } catch (err) {
        showToast(t('msg.users_failed') + err.message);
    }
}

const createUserForm = document.getElementById('create-user-form');
if (createUserForm) {
    createUserForm.onsubmit = async (e) => {
        e.preventDefault();
        const errEl = document.getElementById('create-user-error');
        errEl.textContent = '';
        try {
            await apiFetch('/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: document.getElementById('new-user-username').value,
                    email: document.getElementById('new-user-email').value,
                    password: document.getElementById('new-user-password').value,
                })
            });
            document.getElementById('new-user-username').value = '';
            document.getElementById('new-user-email').value = '';
            document.getElementById('new-user-password').value = '';
            showToast(t('msg.user_created'));
            loadAdminUsers();
        } catch (err) {
            errEl.textContent = err.message;
        }
    };
}

async function updateUserRole(userId, role) {
    try {
        await apiFetch(`/users/${userId}/role?role=${role}`, { method: 'PATCH' });
        showToast(t('msg.role_updated'));
    } catch (err) {
        showToast(t('msg.update_failed') + err.message);
        loadAdminUsers();
    }
}

async function toggleUserActive(userId, isActive) {
    try {
        await apiFetch(`/users/${userId}/active?is_active=${isActive}`, { method: 'PATCH' });
        showToast(t('msg.user_updated'));
    } catch (err) {
        showToast(t('msg.update_failed') + err.message);
        loadAdminUsers();
    }
}

async function deleteUser(userId) {
    if (!confirm(t('msg.delete_user'))) return;
    try {
        await apiFetch(`/users/${userId}`, { method: 'DELETE' });
        showToast(t('msg.user_deleted'));
        loadAdminUsers();
    } catch (err) {
        showToast(t('msg.delete_failed') + err.message);
    }
}

// ===== Init =====
initAuth();
