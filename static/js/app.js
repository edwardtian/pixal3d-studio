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
let queuePanelOpen = false;
let queueTasksCache = [];
let gpuEnabledSet = new Set();

// Crop state
let cropRect = null;
let cropDragging = false;
let cropStartX = 0, cropStartY = 0;
let cropCanvas = null;
let cropCtx = null;
let cropImg = null;

// Compare state
let selectedTaskIds = new Set();
let compareSyncActive = false;
let compareFsActive = false;
let compareLayout = 'vertical';

// Preset & copied params state
let copiedParams = null;
let presetList = [];
let currentDetailParams = null;

// Re-submit state
let sourceParams = null;
let sourceImageFilename = null;

// Zoom state for source image
let imgZoom = 1, imgPanX = 0, imgPanY = 0, imgDragging = false, imgLastX = 0, imgLastY = 0;

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
    const displayName = currentUser.username.length > 8
        ? currentUser.username.slice(0, 8) + '…'
        : currentUser.username;
    badge.textContent = displayName;
    badge.title = currentUser.username;
    badge.classList.toggle('admin', currentUser.role === 'admin');
    if (currentUser.role === 'admin') {
        badge.textContent = displayName + t('auth.admin_suffix');
        document.getElementById('admin-nav-btn').style.display = 'flex';
        document.getElementById('gpus-nav-btn').style.display = 'flex';
    }

    loadParameters();
    startQueuePolling();
    if (currentUser.role === 'admin') {
        document.getElementById('admin-filters').style.display = 'flex';
    }
    navigateTo('create');
    setTimeout(() => applyDefaultPreset(), 500);
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

    // Update nav button highlight — 'detail' keeps 'history' highlighted
    const navTarget = view === 'detail' ? 'history' : view;
    document.querySelectorAll('.sidebar .btn-full').forEach(btn => {
        const target = btn.dataset.navTarget;
        if (target === navTarget) {
            btn.classList.remove('btn-outline');
            btn.classList.add('btn-primary');
        } else if (target) {
            btn.classList.remove('btn-primary');
            btn.classList.add('btn-outline');
        }
    });

    if (view === 'history') loadTaskHistory();
    if (view === 'admin') loadAdminUsers();
    if (view === 'gpus') loadGpusView();
    if (view === 'compare') loadCompareView();
    if (view === 'presets') loadPresetsView();
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

    // Preset bar
    html += `<div class="preset-bar">
        <select id="preset-select" onchange="applyPresetFromDropdown()">
            <option value="">${t('create.apply_preset')}</option>
        </select>
        <button class="btn btn-outline btn-sm" onclick="savePresetPrompt()" data-i18n="create.save_preset">${t('create.save_preset')}</button>
    </div>`;

    // Copied/re-submit params indicator
    if (sourceParams) {
        html += `<div style="font-size:0.72rem;color:var(--accent);margin-bottom:0.75rem;">${t('detail.params_copied')}</div>`;
    } else if (copiedParams) {
        html += `<div style="font-size:0.72rem;color:var(--accent);margin-bottom:0.75rem;">${t('detail.params_copied')}</div>`;
    }

    for (const [groupName, params] of Object.entries(groups)) {
        html += `<div class="param-group"><div class="param-group-title">${tGroup(groupName)}</div>`;
        for (const p of params) {
            html += renderParamItem(p);
        }
        html += '</div>';
    }

    panel.innerHTML = html;
    attachParamListeners();
    refreshPresetDropdown();

    // Apply copied or source params
    const paramsToApply = sourceParams || copiedParams;
    if (paramsToApply) {
        applyParamsToUI(paramsToApply);
        if (sourceParams) {
            setupParamChangeTracking();
        }
        copiedParams = null;
    }
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
            <div class="param-item" id="param-item-${p.key}">
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
        <div class="param-item" id="param-item-${p.key}">
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

function attachParamListeners() {
    // Add change listeners for re-submit tracking
    if (sourceParams) {
        setupParamChangeTracking();
    }
}

function setupParamChangeTracking() {
    const onChange = () => checkParamDiff();
    document.querySelectorAll('#param-panel input, #param-panel select').forEach(el => {
        el.addEventListener('input', onChange);
        el.addEventListener('change', onChange);
    });
    checkParamDiff();
}

function checkParamDiff() {
    if (!sourceParams) return;
    const current = collectParameters();
    let hasDiff = false;
    for (const key of Object.keys(parameterDefs)) {
        const item = document.getElementById('param-item-' + key);
        if (!item) continue;
        const srcVal = sourceParams[key];
        const curVal = current[key];
        const diff = JSON.stringify(srcVal) !== JSON.stringify(curVal);
        item.classList.toggle('param-changed', diff);
        if (diff) hasDiff = true;
    }
    // Also check FOV auto toggle
    const fovAuto = document.getElementById('fov-auto');
    if (fovAuto) {
        const fovItem = document.getElementById('param-item-manual_fov');
        if (fovItem) {
            const wasAuto = sourceParams['manual_fov'] < 0;
            const isAuto = fovAuto.checked;
            const diff = wasAuto !== isAuto;
            fovItem.classList.toggle('param-changed', diff);
            if (diff) hasDiff = true;
        }
    }
    const submitBtn = document.getElementById('submit-btn');
    if (submitBtn) submitBtn.disabled = !hasDiff;
}

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

function applyParamsToUI(params) {
    for (const key of Object.keys(parameterDefs)) {
        if (key === 'fov_unit') {
            const el = document.getElementById('param-fov_unit');
            if (el && params[key] !== undefined) el.value = params[key];
            continue;
        }
        if (key === 'manual_fov') {
            const auto = (params[key] === undefined || params[key] < 0);
            const autoEl = document.getElementById('fov-auto');
            if (autoEl) {
                autoEl.checked = auto;
                toggleFovAuto();
            }
            if (!auto) {
                const el = document.getElementById('param-manual_fov');
                const unit = document.getElementById('param-fov_unit')?.value || 'deg';
                if (el) {
                    let val = params[key];
                    if (unit === 'rad') val = val * Math.PI / 180;
                    el.value = val;
                }
            }
            continue;
        }
        const el = document.getElementById('param-' + key);
        if (!el || params[key] === undefined) continue;
        const def = parameterDefs[key];
        if (def.type === 'bool') {
            el.checked = params[key];
        } else {
            el.value = params[key];
        }
        updateParamDisplay(key);
    }
}

// ===== Presets =====
async function refreshPresetDropdown() {
    try {
        presetList = await apiFetch('/presets');
    } catch (e) { return; }
    const sel = document.getElementById('preset-select');
    if (!sel) return;
    const defaultPreset = presetList.find(p => p.is_default);
    sel.innerHTML = `<option value="">${t('create.apply_preset')}</option>` +
        presetList.map(p => `<option value="${p.id}">${p.name}${p.is_default ? ' ★' : ''}</option>`).join('');
}

function applyPresetFromDropdown() {
    const sel = document.getElementById('preset-select');
    if (!sel || !sel.value) return;
    const preset = presetList.find(p => p.id == sel.value);
    if (preset) {
        applyParamsToUI(preset.parameters);
        showToast(t('preset.applied') + ': ' + preset.name);
        sel.value = '';
        if (sourceParams) checkParamDiff();
    }
}

async function savePresetPrompt() {
    const name = prompt(t('preset.name_prompt'));
    if (!name) return;
    const params = collectParameters();
    try {
        await apiFetch('/presets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, parameters: params })
        });
        showToast(t('preset.saved'));
        await refreshPresetDropdown();
    } catch (err) {
        showToast(t('preset.save_error') + err.message);
    }
}

async function loadPresetsView() {
    try {
        presetList = await apiFetch('/presets');
        const tbody = document.getElementById('presets-tbody');
        const empty = document.getElementById('presets-empty');
        if (presetList.length === 0) {
            tbody.innerHTML = '';
            empty.style.display = 'block';
            return;
        }
        empty.style.display = 'none';
        tbody.innerHTML = presetList.map(p => `
            <tr>
                <td>${p.name}</td>
                <td>${p.is_default ? '★' : ''}</td>
                <td>${new Date(p.created_at).toLocaleDateString()}</td>
                <td style="display:flex;gap:0.3rem;flex-wrap:wrap;">
                    <button class="btn btn-outline btn-sm" onclick="renamePreset(${p.id})">${t('preset.rename')}</button>
                    <button class="btn btn-outline btn-sm" onclick="toggleDefaultPreset(${p.id}, ${!p.is_default})">${p.is_default ? t('preset.unset_default') : t('preset.set_default')}</button>
                    <button class="btn btn-danger btn-sm" onclick="deletePreset(${p.id})">${t('preset.delete')}</button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        showToast(t('preset.save_error') + err.message);
    }
}

async function renamePreset(id) {
    const preset = presetList.find(p => p.id === id);
    const name = prompt(t('preset.rename_prompt'), preset?.name || '');
    if (!name) return;
    try {
        await apiFetch(`/presets/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        showToast(t('preset.renamed'));
        loadPresetsView();
        refreshPresetDropdown();
    } catch (err) {
        showToast(t('preset.save_error') + err.message);
    }
}

async function toggleDefaultPreset(id, isDefault) {
    try {
        await apiFetch(`/presets/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_default: isDefault })
        });
        showToast(isDefault ? t('preset.default_set') : t('preset.default_unset'));
        loadPresetsView();
        refreshPresetDropdown();
    } catch (err) {
        showToast(t('preset.save_error') + err.message);
    }
}

async function deletePreset(id) {
    if (!confirm(t('preset.confirm_delete'))) return;
    try {
        await apiFetch(`/presets/${id}`, { method: 'DELETE' });
        showToast(t('preset.deleted'));
        loadPresetsView();
        refreshPresetDropdown();
    } catch (err) {
        showToast(t('preset.save_error') + err.message);
    }
}

async function applyDefaultPreset() {
    try {
        presetList = await apiFetch('/presets');
        const defaultPreset = presetList.find(p => p.is_default);
        if (defaultPreset) {
            applyParamsToUI(defaultPreset.parameters);
            showToast(t('create.default_preset_applied') + ': ' + defaultPreset.name);
        }
    } catch (e) { /* ignore */ }
}

// ===== Copy Params & Re-submit =====
function copyParamsFromTask(params) {
    copiedParams = { ...params };
    sourceParams = null;
    sourceImageFilename = null;
    navigateTo('create');
    renderParameters();
    showToast(t('detail.params_copied'));
}

async function resubmitTask(taskId) {
    try {
        const task = await apiFetch(`/tasks/${taskId}`);
        sourceParams = { ...task.parameters };
        // Fetch the source image and set it as selectedFile
        const imgResp = await fetch(authUrl('/api/tasks/' + taskId + '/image'));
        const blob = await imgResp.blob();
        selectedFile = new File([blob], 'resubmit.png', { type: blob.type });
        originalFile = selectedFile;
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
        reader.readAsDataURL(selectedFile);
        const cropToolbar = document.getElementById('crop-toolbar');
        if (cropToolbar) cropToolbar.style.display = 'flex';
        navigateTo('create');
        renderParameters();
        showToast(t('detail.params_copied'));
    } catch (err) {
        showToast(t('msg.task_failed_load') + err.message);
    }
}

// ===== Rating =====
async function setTaskRating(taskId, rating) {
    try {
        await apiFetch(`/tasks/${taskId}/rating`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rating })
        });
        const container = document.querySelector('.star-rating[data-task="' + taskId + '"]');
        if (container) {
            container.innerHTML = '';
            for (let i = 1; i <= 5; i++) {
                const star = document.createElement('span');
                star.className = 'star' + (i <= rating ? ' filled' : '');
                star.innerHTML = '&#9733;';
                star.title = String(i);
                star.onclick = () => setTaskRating(taskId, i);
                container.appendChild(star);
            }
        }
    } catch (err) {
        showToast(t('msg.update_failed') + err.message);
    }
}

function renderStarRating(taskId, rating) {
    let html = `<div class="star-rating" data-task="${taskId}">`;
    for (let i = 1; i <= 5; i++) {
        const filled = i <= rating ? 'filled' : '';
        html += `<span class="star ${filled}" onclick="setTaskRating(${taskId}, ${i})" title="${i}">&#9733;</span>`;
    }
    html += '</div>';
    return html;
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
fileInput.onchange = (e) => {
    if (e.target.files.length) handleFileUpload(e.target.files[0]);
    // Reset source tracking when user manually selects a new file
    sourceParams = null;
    sourceImageFilename = null;
};

async function handleFileUpload(file) {
    selectedFile = file;
    originalFile = file;
    uploadedImageFilename = null;
    // Don't reset sourceParams here (resubmit sets file before calling this)
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
    const submitBtn = document.getElementById('submit-btn');
    if (submitBtn) submitBtn.disabled = false;
}

// ===== Image Cropping =====
function enterCropMode() {
    if (!selectedFile) return;
    const reader = new FileReader();
    reader.onload = (e) => {
        cropImg = new Image();
        cropImg.onload = () => {
            const maxW = 900, maxH = 700;
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
        if (sourceParams) checkParamDiff();
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
        // Clear re-submit state after successful submission
        sourceParams = null;
    } catch (err) {
        hideProgress();
        showToast(t('msg.submit_failed') + err.message);
    }
}

// ===== Progress =====
function showProgress() {
    document.getElementById('progress-overlay').style.display = 'flex';
    document.getElementById('progress-stage').textContent = t('msg.submitting');
    document.getElementById('progress-subtask-line').textContent = '';
    document.getElementById('progress-fill').style.width = '0%';
    document.getElementById('progress-step').textContent = '';
    document.getElementById('progress-subtask-fill').style.width = '0%';
    document.getElementById('progress-subtask-step').textContent = '';
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
                document.getElementById('progress-subtask-line').textContent = t('msg.waiting');
                document.getElementById('progress-fill').style.width = '0%';
                document.getElementById('progress-step').textContent = '';
                document.getElementById('progress-subtask-fill').style.width = '0%';
                document.getElementById('progress-subtask-step').textContent = '';
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
            if (task.status === 'cancelled' || task.status === 'cancelling') {
                clearInterval(progressInterval);
                hideProgress();
                showToast(t('msg.task_cancelled'));
                return;
            }

            // Overall progress
            const ov = task.overall_progress || 0;
            document.getElementById('progress-stage').textContent = task.progress || t('msg.processing');
            document.getElementById('progress-fill').style.width = Math.min(100, ov) + '%';
            document.getElementById('progress-step').textContent = ov + '%';

            // Sub-task progress
            const subLine = (task.subtask_total > 0)
                ? `${t('progress.subtask')} ${task.subtask_index}/${task.subtask_total}` +
                  (task.subtask_name ? ' · ' + task.subtask_name : '') +
                  (task.assigned_gpu != null ? ' · ' + t('progress.on_gpu') + task.assigned_gpu : '')
                : '';
            document.getElementById('progress-subtask-line').textContent = subLine;
            if (task.subtask_total_steps > 0) {
                document.getElementById('progress-subtask-fill').style.width =
                    Math.min(100, (task.subtask_step / task.subtask_total_steps) * 100) + '%';
                document.getElementById('progress-subtask-step').textContent =
                    task.subtask_step + '/' + task.subtask_total_steps;
            } else {
                document.getElementById('progress-subtask-fill').style.width = '0%';
                document.getElementById('progress-subtask-step').textContent = '';
            }
        } catch (e) { /* ignore */ }
    }, 1000);
}

// ===== Queue panel =====
function toggleQueuePanel() {
    queuePanelOpen = !queuePanelOpen;
    const panel = document.getElementById('queue-panel');
    panel.classList.toggle('open', queuePanelOpen);
}

function startQueuePolling() {
    async function poll() {
        try {
            const [status, tasks] = await Promise.all([
                apiFetch('/queue/status'),
                apiFetch('/queue/tasks'),
            ]);
            queueTasksCache = tasks || [];
            gpuEnabledSet = new Set(status.gpus ? status.gpus.filter(g => g.enabled).map(g => g.id) : []);

            // Count badge
            const count = status.total_waiting + (status.num_busy || 0);
            const countEl = document.getElementById('queue-count');
            countEl.textContent = count;
            countEl.classList.toggle('zero', count === 0);

            // GPU chips
            const gpuRow = document.getElementById('queue-gpu-row');
            if (status.gpus && status.gpus.length) {
                gpuRow.innerHTML = status.gpus.map(g => {
                    const busy = (status.active_tasks || []).some(a => a.gpu_id === g.id);
                    const cls = g.enabled ? (busy ? 'busy' : 'idle') : '';
                    const label = g.enabled ? (busy ? 'GPU ' + g.id + ' · ' + t('queue.busy') : 'GPU ' + g.id) : ('GPU ' + g.id + ' · ' + t('queue.off'));
                    return `<span class="queue-gpu-chip ${cls}">${label}</span>`;
                }).join('');
            } else {
                gpuRow.innerHTML = '';
            }

            // Task list
            const listEl = document.getElementById('queue-task-list');
            const emptyEl = document.getElementById('queue-empty');
            if (!queueTasksCache.length) {
                listEl.innerHTML = '';
                emptyEl.style.display = 'block';
            } else {
                emptyEl.style.display = 'none';
                listEl.innerHTML = queueTasksCache.map(task => renderQueueTaskItem(task)).join('');
            }
        } catch (e) { /* ignore */ }
    }
    poll();
    queueInterval = setInterval(poll, 2000);
}

function renderQueueTaskItem(task) {
    const isQueued = task.status === 'queued';
    const isCancelling = task.status === 'cancelling';
    const pos = isQueued && task.queue_position != null
        ? `${t('queue.position')} #${task.queue_position}` : '';
    const subLine = isQueued
        ? pos
        : `${t('progress.subtask')} ${task.subtask_index}/${task.subtask_total}` +
          (task.subtask_name ? ' · ' + task.subtask_name : '') +
          (task.assigned_gpu != null ? ' · GPU ' + task.assigned_gpu : '');
    const userLabel = currentUser.role === 'admin' ? ` (#${task.user_id} ${task.username || ''})` : '';
    const ov = task.overall_progress || 0;
    const subPct = task.subtask_total_steps > 0
        ? Math.min(100, (task.subtask_step / task.subtask_total_steps) * 100) : 0;
    const subBar = (task.status === 'processing')
        ? `<div class="queue-task-bar-bg"><div class="queue-task-bar-fill sub" style="width:${subPct}%"></div></div>` : '';
    const cancelLabel = isCancelling ? t('queue.cancelling') : '✕';
    const cancelDisabled = isCancelling ? 'disabled' : '';
    return `
    <div class="queue-task-item">
        <div class="queue-task-item-row">
            <img class="queue-task-thumb" src="${authUrl('/api/tasks/' + task.id + '/image')}" alt="" onerror="this.style.visibility='hidden'">
            <div class="queue-task-info">
                <div class="queue-task-title">${t('queue.task')} #${task.id}${userLabel}</div>
                <div class="queue-task-sub">${subLine}</div>
            </div>
            <button class="queue-task-cancel" title="${t('queue.cancel')}" ${cancelDisabled} onclick="cancelTask(${task.id})">${cancelLabel}</button>
        </div>
        <div class="queue-task-bars">
            <div class="queue-task-bar-bg"><div class="queue-task-bar-fill" style="width:${ov}%"></div></div>
            ${subBar}
        </div>
    </div>`;
}

async function cancelTask(taskId) {
    if (!confirm(t('queue.confirm_cancel'))) return;
    try {
        const res = await apiFetch(`/tasks/${taskId}/cancel`, { method: 'POST' });
        showToast(res.immediate ? t('queue.cancelled') : t('queue.cancel_requested'));
    } catch (err) {
        showToast(t('queue.cancel_failed') + err.message);
    }
}

// ===== Zoomable Image =====
function setupImageZoom(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const img = container.querySelector('img');
    if (!img) return;

    imgZoom = 1; imgPanX = 0; imgPanY = 0;
    updateImgTransform(img);

    container.onwheel = (e) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        imgZoom = Math.max(1, Math.min(5, imgZoom * delta));
        if (imgZoom === 1) { imgPanX = 0; imgPanY = 0; }
        updateImgTransform(img);
    };

    container.onmousedown = (e) => {
        if (imgZoom <= 1) return;
        imgDragging = true;
        imgLastX = e.clientX;
        imgLastY = e.clientY;
    };

    container.onmousemove = (e) => {
        if (!imgDragging) return;
        imgPanX += e.clientX - imgLastX;
        imgPanY += e.clientY - imgLastY;
        imgLastX = e.clientX;
        imgLastY = e.clientY;
        updateImgTransform(img);
    };

    container.onmouseup = () => { imgDragging = false; };
    container.onmouseleave = () => { imgDragging = false; };
}

function updateImgTransform(img) {
    img.style.transform = `translate(${imgPanX}px, ${imgPanY}px) scale(${imgZoom})`;
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

        let barHtml = `<div class="compare-bar">
            <button class="btn btn-primary btn-sm" onclick="navigateTo('compare')" id="compare-btn" disabled>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/></svg>
                <span data-i18n="history.compare_selected">${t('history.compare_selected')}</span>
            </button>
            <span id="select-count" style="font-size:0.8rem;color:var(--text-dim);">0 ${t('history.selected_count')}</span>
            <span style="font-size:0.72rem;color:var(--text-dim);">${t('history.max_select')}</span>
        </div>`;

        if (tasks.length === 0) {
            list.innerHTML = barHtml + `<div class="empty-state"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 3L2 8l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/></svg><p>${t('history.empty')}</p></div>`;
            return;
        }
        list.innerHTML = barHtml + tasks.map(task => {
            const statusBadge = `<span class="status-badge status-${task.status}">${tStatus(task.status)}</span>`;
            const userLabel = currentUser.role === 'admin' ? ` (${t('history.user')} #${task.user_id})` : '';
            const canSelect = task.status === 'completed' && task.output_glb_path;
            const isSelected = selectedTaskIds.has(task.id);
            const ratingHtml = task.rating > 0 ? `<span style="color:#fbbf24;">${'★'.repeat(task.rating)}${'☆'.repeat(5-task.rating)}</span>` : '';
            return `
            <div class="task-card" style="${canSelect ? '' : 'opacity:0.6;'}">
                <input type="checkbox" class="task-select" ${canSelect ? '' : 'disabled'} ${isSelected ? 'checked' : ''} onchange="toggleSelectTask(${task.id}, this.checked)" onclick="event.stopPropagation()">
                <img class="task-thumb" src="${authUrl('/api/tasks/' + task.id + '/image')}" alt="" onerror="this.style.display='none'" onclick="loadTaskDetail(${task.id})" style="cursor:pointer;">
                <div class="task-info" onclick="loadTaskDetail(${task.id})" style="cursor:pointer;">
                    <h4>Task #${task.id}${userLabel}</h4>
                    <div class="task-meta">
                        ${statusBadge}
                        ${ratingHtml}
                        <span>${new Date(task.created_at).toLocaleString()}</span>
                        <span>${t('history.res')}: ${task.parameters?.resolution || '-'}</span>
                        <span>${t('history.seed')}: ${task.parameters?.seed || '-'}</span>
                    </div>
                    ${task.status === 'processing' ? `<div style="font-size:0.72rem;color:var(--text-dim);margin-top:0.3rem;">${t('progress.subtask')} ${task.subtask_index}/${task.subtask_total}${task.subtask_name ? ' · ' + task.subtask_name : ''}${task.assigned_gpu != null ? ' · GPU ' + task.assigned_gpu : ''} (${task.overall_progress || 0}%)</div>` : ''}
                    ${task.status === 'cancelling' ? `<div style="font-size:0.72rem;color:var(--warning);margin-top:0.3rem;">${t('queue.cancelling')}</div>` : ''}
                    ${task.status === 'failed' ? `<div style="font-size:0.72rem;color:var(--danger);margin-top:0.3rem;">${task.error_message}</div>` : ''}
                </div>
            </div>
        `}).join('');
        updateSelectCount();
    } catch (err) {
        showToast(t('msg.tasks_failed') + err.message);
    }
}

// ===== Task Selection for Compare =====
function toggleSelectTask(taskId, checked) {
    if (checked) {
        if (selectedTaskIds.size >= 8) {
            showToast(t('history.max_select'));
            event.target.checked = false;
            return;
        }
        selectedTaskIds.add(taskId);
    } else {
        selectedTaskIds.delete(taskId);
    }
    updateSelectCount();
}

function updateSelectCount() {
    const countEl = document.getElementById('select-count');
    const btn = document.getElementById('compare-btn');
    if (countEl) countEl.textContent = `${selectedTaskIds.size} ${t('history.selected_count')}`;
    if (btn) btn.disabled = selectedTaskIds.size < 2;
}

// ===== Compare View =====
let compareTasks = [];

async function loadCompareView() {
    const content = document.getElementById('compare-content');
    const info = document.getElementById('compare-info');

    if (selectedTaskIds.size < 2) {
        content.innerHTML = `<div class="compare-empty"><p>${t('history.select')} ≥ 2</p></div>`;
        if (info) info.textContent = '';
        return;
    }

    if (info) info.textContent = `${selectedTaskIds.size} ${t('history.selected_count')}`;

    compareTasks = [];
    for (const id of selectedTaskIds) {
        try {
            const task = await apiFetch(`/tasks/${id}`);
            if (task.status === 'completed' && task.output_glb_path) {
                compareTasks.push(task);
            }
        } catch (e) { /* skip */ }
    }

    if (compareTasks.length < 2) {
        content.innerHTML = `<div class="compare-empty"><p>${t('history.select')} ≥ 2</p></div>`;
        return;
    }

    renderCompareContent(content);
    setupCompareSync();
}

function renderCompareContent(content) {
    let html = '';

    // Toolbar
    html += `<div style="display:flex;gap:0.5rem;margin-bottom:1rem;flex-wrap:wrap;">
        <button class="btn btn-outline btn-sm" onclick="toggleCompareFs()">${t('compare.fullscreen')}</button>
        <button class="btn btn-outline btn-sm" onclick="toggleCompareLayout()">${compareLayout === 'vertical' ? t('compare.layout_h') : t('compare.layout_v')}</button>
        <button class="btn btn-outline btn-sm" onclick="toggleCompareParams()">${t('compare.show_params')}</button>
    </div>`;

    // Params comparison (hidden by default)
    html += `<div id="compare-params-section" style="display:none;margin-bottom:1rem;">`;
    html += `<div class="detail-section"><h3>${t('compare.params_compare')}</h3>`;
    html += `<div style="overflow-x:auto;"><table class="params-compare-table"><thead><tr><th>Param</th>`;
    compareTasks.forEach(task => { html += `<th>Task #${task.id}</th>`; });
    html += `</tr></thead><tbody>`;
    if (compareTasks.length > 0 && compareTasks[0].parameters) {
        const allKeys = Object.keys(compareTasks[0].parameters);
        for (const key of allKeys) {
            html += `<tr><td>${key}</td>`;
            const vals = compareTasks.map(t => t.parameters?.[key]);
            const allSame = vals.every(v => JSON.stringify(v) === JSON.stringify(vals[0]));
            compareTasks.forEach((task, i) => {
                const cls = (!allSame && i > 0 && JSON.stringify(vals[i]) !== JSON.stringify(vals[0])) ? 'param-diff' : '';
                html += `<td class="${cls}">${vals[i] ?? '-'}</td>`;
            });
            html += `</tr>`;
        }
    }
    html += `</tbody></table></div></div></div>`;

    // Model viewers
    html += `<div id="compare-models" class="${compareLayout === 'horizontal' ? 'compare-horizontal-list' : ''}">`;
    compareTasks.forEach(task => {
        const glbUrl = authUrl('/api/tasks/' + task.id + '/download');
        html += `
        <div class="compare-item">
            <div class="compare-item-header">
                <div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;">
                    <h4>Task #${task.id}</h4>
                    ${renderStarRating(task.id, task.rating || 0)}
                    <button class="btn btn-outline btn-sm" onclick="copyParamsFromTask(${JSON.stringify(task.parameters).replace(/"/g, '"')})" style="padding:0.2rem 0.5rem;font-size:0.72rem;">${t('detail.copy_params')}</button>
                </div>
                <div class="compare-item-meta">
                    ${t('history.res')}: ${task.parameters?.resolution || '-'} ·
                    ${t('history.seed')}: ${task.parameters?.seed || '-'}
                </div>
            </div>
            <model-viewer
                src="${glbUrl}"
                camera-controls
                shadow-intensity="1.5"
                environment-image="neutral"
                exposure="1.2"
                data-compare-mv
            ></model-viewer>
        </div>`;
    });
    html += `</div>`;

    content.innerHTML = html;
    setupCompareSync();
}

function toggleCompareParams() {
    const section = document.getElementById('compare-params-section');
    if (!section) return;
    const isVisible = section.style.display !== 'none';
    section.style.display = isVisible ? 'none' : 'block';
}

function toggleCompareLayout() {
    compareLayout = compareLayout === 'vertical' ? 'horizontal' : 'vertical';
    const models = document.getElementById('compare-models');
    if (models) {
        models.className = compareLayout === 'horizontal' ? 'compare-horizontal-list' : '';
    }
    // Update button text
    const content = document.getElementById('compare-content');
    const btn = content?.querySelector('button:nth-child(2)');
    if (btn) btn.textContent = compareLayout === 'vertical' ? t('compare.layout_h') : t('compare.layout_v');
}

function toggleCompareFs() {
    compareFsActive = !compareFsActive;
    const overlay = document.getElementById('compare-fs-overlay');
    const sidebar = document.querySelector('.sidebar');
    if (compareFsActive) {
        // Clone content into overlay
        const content = document.getElementById('compare-content');
        overlay.querySelector('.compare-fs-content').innerHTML = content.innerHTML;
        overlay.style.display = 'flex';
        sidebar.style.display = 'none';
        // Re-setup sync in overlay
        setupCompareSyncInContainer(overlay);
    } else {
        overlay.style.display = 'none';
        overlay.querySelector('.compare-fs-content').innerHTML = '';
        sidebar.style.display = '';
    }
}

function setupCompareSyncInContainer(container) {
    const viewers = container.querySelectorAll('[data-compare-mv]');
    const initialOrbit = new Map();
    const initialTarget = new Map();

    function recordInitial(mv) {
        try {
            const o = mv.getCameraOrbit();
            const tg = mv.getCameraTarget();
            if (o.radius > 0 && !initialOrbit.has(mv)) {
                initialOrbit.set(mv, o);
                initialTarget.set(mv, tg);
            }
        } catch (e) {}
    }

    viewers.forEach(mv => {
        mv.addEventListener('load', () => setTimeout(() => recordInitial(mv), 200));
        setTimeout(() => recordInitial(mv), 500);
    });

    viewers.forEach(mv => {
        mv.addEventListener('camera-change', () => {
            if (compareSyncActive) return;
            compareSyncActive = true;
            const orbit = mv.getCameraOrbit();
            const target = mv.getCameraTarget();
            const srcInitR = initialOrbit.get(mv)?.radius || orbit.radius;
            const srcInitT = initialTarget.get(mv) || { x: 0, y: 0, z: 0 };
            const zoomRatio = orbit.radius / srcInitR;
            const panOffsetX = (target.x - srcInitT.x) / srcInitR;
            const panOffsetY = (target.y - srcInitT.y) / srcInitR;
            const panOffsetZ = (target.z - srcInitT.z) / srcInitR;

            viewers.forEach(other => {
                if (other === mv) return;
                const otherInitR = initialOrbit.get(other)?.radius || orbit.radius;
                const otherInitT = initialTarget.get(other) || { x: 0, y: 0, z: 0 };
                const otherRadius = otherInitR * zoomRatio;
                const otherTargetX = otherInitT.x + panOffsetX * otherInitR;
                const otherTargetY = otherInitT.y + panOffsetY * otherInitR;
                const otherTargetZ = otherInitT.z + panOffsetZ * otherInitR;
                other.cameraOrbit = `${(orbit.theta * 180 / Math.PI).toFixed(2)}deg ${(orbit.phi * 180 / Math.PI).toFixed(2)}deg ${otherRadius.toFixed(4)}m`;
                other.cameraTarget = `${otherTargetX.toFixed(4)}m ${otherTargetY.toFixed(4)}m ${otherTargetZ.toFixed(4)}m`;
                other.jumpCameraToGoal();
            });
            requestAnimationFrame(() => { compareSyncActive = false; });
        });
    });
}

function setupCompareSync() {
    const content = document.getElementById('compare-content');
    if (content) setupCompareSyncInContainer(content);
}

// ===== Task Detail =====
async function loadTaskDetail(taskId) {
    try {
        const task = await apiFetch(`/tasks/${taskId}`);
        currentDetailView = taskId;
        currentDetailParams = task.parameters || {};
        navigateTo('detail');
        document.getElementById('detail-title').textContent = `Task #${taskId}`;

        const content = document.getElementById('detail-content');
        let html = '';

        // Main: 3D Result
        html += '<div class="detail-section">';
        html += `<h3>${t('detail.result')}</h3>`;
        if (task.status === 'completed' && task.output_glb_path) {
            const imgUrl = authUrl('/api/tasks/' + taskId + '/image');
            const glbUrl = authUrl('/api/tasks/' + taskId + '/download');
            html += `
                <div class="compare-layout">
                    <div class="compare-panel">
                        <div class="compare-label">${t('detail.source_image')}</div>
                        <div class="zoom-img-container" id="source-img-zoom" style="flex:1;min-height:300px;max-height:500px;">
                            <img src="${imgUrl}" alt="Source">
                        </div>
                    </div>
                    <div class="compare-panel">
                        <div class="compare-label">${t('detail.3d_model')}</div>
                        <div class="viewer-wrapper" id="viewer-wrapper">
                            <model-viewer src="${glbUrl}" camera-controls auto-rotate shadow-intensity="1.5" environment-image="neutral" exposure="1.2"></model-viewer>
                        </div>
                    </div>
                </div>
                <div class="viewer-toolbar">
                    <a href="${glbUrl}" download class="btn btn-primary btn-sm">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        ${t('detail.download_glb')}
                    </a>
                    <button class="btn btn-outline btn-sm" onclick="copyParamsFromTask(currentDetailParams)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                        ${t('detail.copy_params')}
                    </button>
                    <button class="btn btn-outline btn-sm" onclick="resubmitTask(${taskId})">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
                        ${t('detail.resubmit')}
                    </button>
                    <button class="btn btn-outline btn-sm" onclick="toggleFullscreen()">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
                        <span id="fs-btn-label">${t('detail.fullscreen')}</span>
                    </button>
                    <button class="btn btn-danger btn-sm" onclick="deleteTask(${taskId})">${t('detail.delete')}</button>
                </div>
                <div style="margin-top:0.75rem;display:flex;align-items:center;gap:0.5rem;">
                    <span style="font-size:0.8rem;color:var(--text-dim);">${t('detail.rate')}:</span>
                    ${renderStarRating(taskId, task.rating || 0)}
                </div>
            `;
        } else if (task.status === 'failed') {
            html += `<div class="empty-state" style="padding:2rem;"><p style="color:var(--danger)">${t('detail.task_failed')}${task.error_message}</p></div>`;
            html += `<div style="margin-top:1rem;"><button class="btn btn-danger btn-sm" onclick="deleteTask(${taskId})">${t('detail.delete')}</button></div>`;
        } else if (task.status === 'processing') {
            const ov = task.overall_progress || 0;
            const subPct = task.subtask_total_steps > 0 ? Math.min(100, (task.subtask_step / task.subtask_total_steps) * 100) : 0;
            html += `<div class="empty-state" style="padding:2rem;">
                <div class="loader-ring" style="margin:0 auto 1rem;"></div>
                <p>${t('detail.processing')}${task.progress}</p>
                <div style="margin-top:0.75rem;font-size:0.78rem;color:var(--text-dim);">${t('progress.subtask')} ${task.subtask_index}/${task.subtask_total}${task.subtask_name ? ' · ' + task.subtask_name : ''}${task.assigned_gpu != null ? ' · GPU ' + task.assigned_gpu : ''}</div>
                <div class="progress-bar-bg" style="margin:0.6rem auto;max-width:420px;"><div class="progress-bar-fill" style="width:${ov}%"></div></div>
                <div style="font-size:0.72rem;color:var(--text-dim);">${ov}%</div>
                <div class="progress-bar-bg progress-subtask-bar" style="margin:0.4rem auto;max-width:420px;"><div class="progress-bar-fill progress-subtask-fill" style="width:${subPct}%"></div></div>
                ${task.subtask_total_steps > 0 ? `<div style="font-size:0.7rem;color:var(--text-dim);font-family:monospace;">${task.subtask_step}/${task.subtask_total_steps}</div>` : ''}
            </div>`;
            html += `<div style="margin-top:1rem;"><button class="btn btn-danger btn-sm" onclick="cancelTask(${taskId})">${t('queue.cancel')}</button></div>`;
        } else if (task.status === 'cancelling') {
            html += `<div class="empty-state" style="padding:2rem;"><div class="loader-ring" style="margin:0 auto 1rem;"></div><p>${t('queue.cancelling')}</p></div>`;
        } else if (task.status === 'cancelled') {
            html += `<div class="empty-state" style="padding:2rem;"><p>${tStatus('cancelled')}</p></div>`;
            html += `<div style="margin-top:1rem;"><button class="btn btn-danger btn-sm" onclick="deleteTask(${taskId})">${t('detail.delete')}</button></div>`;
        } else {
            html += `<div class="empty-state" style="padding:2rem;"><p>${t('detail.task_is')}${tStatus(task.status)}</p></div>`;
            html += `<div style="margin-top:1rem;"><button class="btn btn-danger btn-sm" onclick="deleteTask(${taskId})">${t('detail.delete')}</button></div>`;
        }
        html += '</div>';

        // Below: Preview Renders (collapsible, lazy load) + Parameters
        html += '<div class="detail-section">';
        html += `<h3 class="collapse-header" onclick="toggleRenders(${taskId})">
            <span class="collapse-arrow" id="renders-arrow">&#9654;</span>
            <span id="renders-toggle-text">${t('detail.expand_renders')}</span>
        </h3>`;
        html += '<div class="collapse-content" id="renders-content"></div>'; // empty, lazy loaded

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

        // Setup image zoom if completed
        if (task.status === 'completed' && task.output_glb_path) {
            setTimeout(() => setupImageZoom('source-img-zoom'), 100);
        }

        if (task.status === 'processing' || task.status === 'queued') {
            setTimeout(() => { if (currentDetailView === taskId) loadTaskDetail(taskId); }, 3000);
        }
    } catch (err) {
        showToast(t('msg.task_failed_load') + err.message);
    }
}

function toggleFullscreen() {
    const wrapper = document.getElementById('viewer-wrapper');
    if (!wrapper) return;
    const isFs = wrapper.classList.toggle('viewer-fullscreen');
    const label = document.getElementById('fs-btn-label');
    if (label) label.textContent = isFs ? t('detail.exit_fullscreen') : t('detail.fullscreen');

    // Show/hide exit button
    let exitBtn = document.getElementById('fs-exit-btn');
    if (isFs && !exitBtn) {
        exitBtn = document.createElement('button');
        exitBtn.id = 'fs-exit-btn';
        exitBtn.className = 'fs-exit-btn';
        exitBtn.textContent = t('detail.exit');
        exitBtn.onclick = toggleFullscreen;
        document.body.appendChild(exitBtn);
        exitBtn.style.display = 'block';
    } else if (exitBtn) {
        exitBtn.style.display = isFs ? 'block' : 'none';
    }
}

let rendersLoaded = false;
async function toggleRenders(taskId) {
    const content = document.getElementById('renders-content');
    const arrow = document.getElementById('renders-arrow');
    const text = document.getElementById('renders-toggle-text');
    if (!content) return;
    const isOpen = content.classList.toggle('open');
    if (arrow) arrow.classList.toggle('open', isOpen);
    if (text) text.textContent = isOpen ? t('detail.collapse_renders') : t('detail.expand_renders');

    // Lazy load renders when opening
    if (isOpen && !rendersLoaded && taskId) {
        rendersLoaded = true;
        try {
            const task = await apiFetch(`/tasks/${taskId}`);
            let html = '';
            if (task.render_paths && Object.keys(task.render_paths).length > 0) {
                for (const [mode, frames] of Object.entries(task.render_paths)) {
                    html += `<div style="margin-bottom:0.75rem;"><div style="font-size:0.75rem;color:var(--text-dim);margin-bottom:0.3rem;">${mode}</div><div class="render-gallery">`;
                    for (let i = 0; i < frames.length; i++) {
                        html += `<img src="${authUrl('/api/tasks/' + taskId + '/render/' + mode + '/' + i)}" alt="${mode} ${i}">`;
                    }
                    html += '</div></div>';
                }
            } else {
                html = `<p style="color:var(--text-dim);font-size:0.8rem;">${t('detail.no_renders')}</p>`;
            }
            content.innerHTML = html;
        } catch (e) {
            content.innerHTML = `<p style="color:var(--text-dim);font-size:0.8rem;">${t('detail.no_renders')}</p>`;
        }
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
        if (err.message && err.message.includes('Cancel it first')) {
            showToast(t('msg.delete_running'));
        } else {
            showToast(t('msg.delete_failed') + err.message);
        }
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

// ===== GPU Management (admin) =====
let gpuPollInterval = null;
let gpuPendingToggle = new Set();

async function loadGpusView() {
    if (gpuPollInterval) clearInterval(gpuPollInterval);
    await refreshGpusView();
    gpuPollInterval = setInterval(async () => {
        if (document.getElementById('view-gpus').classList.contains('active')) {
            await refreshGpusView();
        } else {
            clearInterval(gpuPollInterval);
            gpuPollInterval = null;
        }
    }, 3000);
}

async function refreshGpusView() {
    try {
        const data = await apiFetch('/system/gpus');
        const tbody = document.getElementById('gpus-tbody');
        tbody.innerHTML = data.physical.map(g => {
            const memPct = g.mem_total_mb > 0 ? (g.mem_used_mb / g.mem_total_mb) * 100 : 0;
            const memLabel = g.mem_total_mb > 0
                ? `${(g.mem_used_mb/1024).toFixed(1)}/${(g.mem_total_mb/1024).toFixed(0)} GB` : '-';
            const enabled = gpuPendingToggle.has(g.id) ? !data.enabled.includes(g.id) : data.enabled.includes(g.id);
            const statusLabel = !enabled ? t('gpu.disabled')
                : (g.utilization_pct >= 50 ? t('gpu.busy') : t('gpu.available'));
            const statusClass = !enabled ? 'status-queued'
                : (g.utilization_pct >= 50 ? 'status-processing' : 'status-completed');
            return `
            <tr>
                <td>${g.id}</td>
                <td style="font-size:0.78rem;">${g.name}</td>
                <td>
                    <div class="gpu-mem-bar"><div class="gpu-mem-bar-fill" style="width:${memPct}%"></div></div>
                    <span style="font-size:0.72rem;color:var(--text-dim);">${memLabel}</span>
                </td>
                <td>
                    <div class="gpu-util-bar"><div class="gpu-util-bar-fill" style="width:${g.utilization_pct}%"></div></div>
                    <span style="font-size:0.72rem;color:var(--text-dim);">${g.utilization_pct}%</span>
                </td>
                <td><span class="status-badge ${statusClass}">${statusLabel}</span></td>
                <td>
                    <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
                        <input type="checkbox" data-gpu-id="${g.id}" ${enabled ? 'checked' : ''} onchange="toggleGpu(${g.id}, this.checked)" style="accent-color:var(--accent);">
                        <span style="font-size:0.72rem;">${enabled ? t('admin.yes') : t('admin.no')}</span>
                    </label>
                </td>
            </tr>`;
        }).join('');

        const wbody = document.getElementById('gpu-workers-tbody');
        if (data.workers && data.workers.length) {
            wbody.innerHTML = data.workers.map(w => `
                <tr>
                    <td>${w.gpu_id}</td>
                    <td>${w.stop_requested ? t('gpu.stopping') : (w.busy ? t('gpu.busy') : t('gpu.idle'))}</td>
                    <td>${w.current_task_id != null ? '#' + w.current_task_id : '-'}</td>
                    <td>${w.pipeline_loaded ? t('admin.yes') : t('admin.no')}</td>
                </tr>
            `).join('');
        } else {
            wbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--text-dim);padding:1rem;">${t('gpu.no_workers')}</td></tr>`;
        }
    } catch (err) {
        showToast(t('msg.update_failed') + err.message);
    }
}

function toggleGpu(gpuId, enabled) {
    if (enabled) {
        gpuPendingToggle.add(gpuId);
    } else {
        gpuPendingToggle.delete(gpuId);
    }
    saveGpus();
}

async function saveGpus() {
    // Build the desired set from the current checkboxes
    const checkboxes = document.querySelectorAll('#gpus-tbody input[type=checkbox]');
    const desired = [];
    checkboxes.forEach(cb => {
        if (cb.checked) desired.push(parseInt(cb.dataset.gpuId, 10));
    });
    if (desired.length === 0) {
        showToast(t('gpu.need_one'));
        refreshGpusView();
        return;
    }
    try {
        await apiFetch('/system/gpus', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled_ids: desired }),
        });
        gpuPendingToggle.clear();
        showToast(t('gpu.saved'));
        refreshGpusView();
    } catch (err) {
        showToast(t('msg.update_failed') + err.message);
        refreshGpusView();
    }
}

// ===== Init =====
initAuth();
