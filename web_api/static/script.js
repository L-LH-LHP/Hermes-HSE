// Hermes Web 前端（登录分流：reader / writer）

const API_BASE = (typeof window !== 'undefined' && window.location && window.location.protocol === 'file:')
    ? 'http://127.0.0.1:5000'
    : '';

function getEl(id) {
    return document.getElementById(id);
}

async function apiFetchJson(url, options = {}) {
    const response = await fetch(`${API_BASE}${url}`, options);
    let data = {};
    try {
        data = await response.json();
    } catch (_) {
        data = {};
    }
    if (response.status === 401) {
        window.location.href = '/login';
        return { success: false, error: '登录已失效，请重新登录' };
    }
    return data;
}

document.addEventListener('DOMContentLoaded', function() {
    if (getEl('login-form')) {
        initLoginPage();
    }
    if (getEl('search-form')) {
        loadWriters();
        refreshStatus();
    }
    if (getEl('doc-update-writer-id')) {
        loadWriters();
        updateClientStatus();
    }
});

function initLoginPage() {
    const roleSelect = getEl('login-role');
    const readerGroup = getEl('reader-username-group');
    const writerGroup = getEl('writer-id-group');
    const loginHint = getEl('login-hint');
    const form = getEl('login-form');

    function refreshLoginMode() {
        const role = roleSelect.value;
        if (role === 'writer') {
            readerGroup.style.display = 'none';
            writerGroup.style.display = 'block';
            loginHint.textContent = '默认写者密码规则: writer{writer_id+1}，例如 writer_id=0 的密码是 writer1';
        } else {
            readerGroup.style.display = 'block';
            writerGroup.style.display = 'none';
            loginHint.textContent = '默认读者账号：reader / reader123';
        }
    }

    roleSelect.addEventListener('change', refreshLoginMode);
    refreshLoginMode();

    form.addEventListener('submit', async function(event) {
        event.preventDefault();

        const role = roleSelect.value;
        const username = (getEl('login-username').value || '').trim();
        const password = getEl('login-password').value || '';
        const writerIdRaw = (getEl('login-writer-id').value || '').trim();

        const submitBtn = form.querySelector('button[type="submit"]');
        const btnText = submitBtn.querySelector('.btn-text');
        const btnLoading = submitBtn.querySelector('.btn-loading');
        const resultDiv = getEl('login-result');

        submitBtn.disabled = true;
        btnText.style.display = 'none';
        btnLoading.style.display = 'inline';
        resultDiv.style.display = 'none';

        try {
            const body = { role, password };
            if (role === 'reader') {
                body.username = username;
            } else {
                body.writer_id = writerIdRaw === '' ? null : parseInt(writerIdRaw, 10);
            }

            const data = await apiFetchJson('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            if (data.success) {
                window.location.href = data.redirect || '/';
                return;
            }

            resultDiv.className = 'result-message error';
            resultDiv.innerHTML = `<strong>登录失败</strong><br>${escapeHtml(data.error || '未知错误')}`;
            resultDiv.style.display = 'block';
            showToast(data.error || '登录失败', 'error');
        } catch (error) {
            resultDiv.className = 'result-message error';
            resultDiv.innerHTML = `<strong>请求失败</strong><br>${escapeHtml(error.message || String(error))}`;
            resultDiv.style.display = 'block';
            showToast('登录请求失败', 'error');
        } finally {
            submitBtn.disabled = false;
            btnText.style.display = 'inline';
            btnLoading.style.display = 'none';
        }
    });
}

async function logout() {
    try {
        const data = await apiFetchJson('/api/auth/logout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        if (data.success) {
            window.location.href = data.redirect || '/login';
            return;
        }
        showToast(data.error || '退出失败', 'error');
    } catch (error) {
        showToast('退出失败: ' + (error.message || String(error)), 'error');
    }
}

async function loadWriters() {
    try {
        const data = await apiFetchJson('/api/writers');
        if (!data.success) {
            showToast(data.error || '加载写者失败', 'error');
            return;
        }

        const searchSelect = getEl('writer-select');
        const docUpdateSelect = getEl('doc-update-writer-id');

        if (searchSelect) searchSelect.innerHTML = '';
        if (docUpdateSelect) docUpdateSelect.innerHTML = '';

        data.writers.forEach(writer => {
            if (searchSelect) {
                const option = document.createElement('option');
                option.value = writer.id;
                option.textContent = '员工 ' + (writer.id + 1);
                searchSelect.appendChild(option);
            }

            if (docUpdateSelect) {
                const option = document.createElement('option');
                option.value = writer.id;
                option.textContent = '员工 ' + (writer.id + 1);
                docUpdateSelect.appendChild(option);
            }
        });

        if (docUpdateSelect && data.writers.length === 1) {
            docUpdateSelect.value = String(data.writers[0].id);
            docUpdateSelect.disabled = true;
        }
    } catch (error) {
        const msg = (error && error.message && /fetch|network|Failed to fetch/i.test(error.message))
            ? '无法连接后端。请通过 http://127.0.0.1:5000 打开页面并确保已运行 python app.py'
            : '加载写者列表失败';
        showToast(msg, 'error');
    }
}

async function handleSearch(event) {
    event.preventDefault();

    const keyword = (getEl('keyword').value || '').trim();
    const writerSelect = getEl('writer-select');
    const selectedOptions = writerSelect ? Array.from(writerSelect.selectedOptions) : [];
    const writerIds = selectedOptions.map(option => parseInt(option.value, 10));
    const writerIdsParam = writerIds.length > 0 ? writerIds : null;

    const submitBtn = event.target.querySelector('button[type="submit"]');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoading = submitBtn.querySelector('.btn-loading');

    submitBtn.disabled = true;
    btnText.style.display = 'none';
    btnLoading.style.display = 'inline';

    try {
        const data = await apiFetchJson('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                keyword: keyword,
                writer_ids: writerIdsParam
            })
        });

        if (data.success) {
            displaySearchResults(keyword, data.results, data.search_time_ms);
            const total = getTotalFileCount(data.results);
            const timeMsg = typeof data.search_time_ms === 'number' ? `，检索耗时 ${data.search_time_ms} ms` : '';
            showToast(`检索完成: 找到 ${total} 封匹配邮件${timeMsg}`, 'success');
        } else {
            showToast(data.error || '检索失败', 'error');
            hideSearchResults();
        }
    } catch (error) {
        const msg = (error && error.message && /fetch|network|Failed to fetch/i.test(error.message))
            ? '无法连接后端。请确保已运行 python app.py'
            : ('搜索请求失败: ' + (error && error.message ? error.message : String(error)));
        showToast(msg, 'error');
        hideSearchResults();
    } finally {
        submitBtn.disabled = false;
        btnText.style.display = 'inline';
        btnLoading.style.display = 'none';
    }
}

function displaySearchResults(keyword, results, searchTimeMs) {
    const resultsContainer = getEl('search-results');
    const resultsContent = getEl('results-content');
    const searchMeta = getEl('search-meta');

    if (!resultsContainer || !resultsContent || !searchMeta) return;

    if (typeof searchTimeMs === 'number') {
        searchMeta.innerHTML = `<p class="search-meta-text">关键字 "<strong>${escapeHtml(keyword)}</strong>" · 亚线性检索耗时 <strong>${searchTimeMs}</strong> ms</p>`;
        searchMeta.style.display = 'block';
    } else {
        searchMeta.innerHTML = '';
        searchMeta.style.display = 'none';
    }

    if (!results || results.length === 0) {
        resultsContent.innerHTML = `
            <div class="empty-state">
                <p>未找到包含关键字 "${escapeHtml(keyword)}" 的邮件</p>
            </div>
        `;
    } else {
        let html = '';
        results.forEach(result => {
            if (result.file_ids && result.file_ids.length > 0) {
                html += `
                    <div class="result-item">
                        <h4>员工 ${result.writer_id}</h4>
                        <p>匹配 ${result.file_ids.length} 封邮件，点击邮件ID查看明文内容:</p>
                        <div class="file-ids">
                            ${result.file_ids.map(id =>
                                `<span class="file-id-badge" onclick="viewDocument(${result.writer_id - 1}, ${id})" title="点击查看邮件内容">${id}</span>`
                            ).join('')}
                        </div>
                    </div>
                `;
            } else {
                html += `
                    <div class="result-item">
                        <h4>员工 ${result.writer_id}</h4>
                        <p>未找到匹配的邮件</p>
                    </div>
                `;
            }
        });
        resultsContent.innerHTML = html;
    }

    resultsContainer.style.display = 'block';
    resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function viewDocument(writerId, fileId) {
    const modal = document.createElement('div');
    modal.className = 'document-modal';
    modal.innerHTML = `
        <div class="document-modal-content">
            <div class="document-modal-header">
                <h3>邮件内容 - 员工 ${writerId + 1}, 文件ID: ${fileId}</h3>
                <button class="document-modal-close" onclick="closeDocumentModal()">&times;</button>
            </div>
            <div class="document-modal-body">
                <div id="document-loading" style="text-align: center; padding: 20px;">
                    <div class="loading"></div>
                    <p>正在获取邮件内容...</p>
                </div>
                <div id="document-content" style="display: none;"></div>
                <div id="document-error" style="display: none;"></div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.style.display = 'flex';

    try {
        const data = await apiFetchJson('/api/document', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ writer_id: writerId, file_id: fileId, decrypt: false })
        });

        getEl('document-loading').style.display = 'none';

        if (data.success && data.encrypted) {
            const contentDiv = getEl('document-content');
            contentDiv.style.display = 'block';
            const previewLen = 800;
            let encryptedPreview = data.placeholder
                ? escapeHtml(data.message)
                : escapeHtml(data.content ? data.content.substring(0, previewLen) : '');
            if (!data.placeholder && data.content && data.content.length > previewLen) {
                encryptedPreview += '\n\n... (密文已截断，点击「解密」可查看全文原文)';
            }
            contentDiv.innerHTML = `
                <div class="document-info">
                    <p><strong>状态:</strong> <span class="encrypted-badge">已加密</span></p>
                    ${data.size != null ? `<p><strong>密文大小:</strong> ${data.size} 字节</p>` : ''}
                </div>
                <pre class="document-text encrypted-preview" style="max-height: 320px; overflow: auto; background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 6px; font-size: 13px;">${encryptedPreview}</pre>
                <div style="margin-top: 12px;">
                    <button type="button" class="btn btn-primary" id="btn-decrypt-doc" onclick="requestDecryptDocument(${writerId}, ${fileId})">解密</button>
                </div>
            `;
        } else if (data.success && !data.encrypted) {
            const contentDiv = getEl('document-content');
            contentDiv.style.display = 'block';
            contentDiv.innerHTML = buildDecryptedContentHtml(data, writerId, fileId);
        } else {
            showDocumentError(data);
        }
    } catch (error) {
        getEl('document-loading').style.display = 'none';
        getEl('document-error').style.display = 'block';
        getEl('document-error').innerHTML = `
            <div class="result-message error">
                <strong>错误</strong><br>请求失败: ${escapeHtml(error.message || String(error))}
            </div>
        `;
    }
}

async function requestDecryptDocument(writerId, fileId) {
    const btn = getEl('btn-decrypt-doc');
    const contentDiv = getEl('document-content');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '解密中...';
    }
    try {
        const data = await apiFetchJson('/api/document', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ writer_id: writerId, file_id: fileId, decrypt: true })
        });
        if (btn) {
            btn.disabled = false;
            btn.textContent = '解密';
        }
        if (data.success && contentDiv) {
            contentDiv.innerHTML = buildDecryptedContentHtml(data, writerId, fileId);
        } else if (contentDiv) {
            contentDiv.innerHTML = `<div class="result-message error"><strong>解密失败</strong><br>${escapeHtml(data.error || '未知错误')}</div>`;
        }
    } catch (error) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '解密';
        }
        if (contentDiv) {
            contentDiv.innerHTML = `<div class="result-message error"><strong>错误</strong><br>请求失败: ${escapeHtml(error.message || String(error))}</div>`;
        }
    }
}

function buildDecryptedContentHtml(data, writerId, fileId) {
    const enc = data.encoding || 'utf-8';
    const size = data.size != null ? data.size : (data.content ? data.content.length : 0);
    if (data.encoding === 'base64') {
        const snippet = data.content ? data.content.substring(0, 1000) : '';
        return `
            <div class="document-info">
                <p><strong>状态:</strong> <span class="decrypted-badge">已解密</span></p>
                <p><strong>文件大小:</strong> ${size} 字节</p>
                <p><strong>类型:</strong> 二进制文件</p>
                <button class="btn btn-primary" onclick="downloadDecryptedDocument(${writerId}, ${fileId})">下载解密后的文件</button>
            </div>
            <pre class="document-text" style="max-height: 400px; overflow: auto; background: #f5f5f5; padding: 15px; border-radius: 6px;">${escapeHtml(snippet)}${(data.content && data.content.length > 1000) ? '\n\n... (内容已截断，请下载查看完整文件)' : ''}</pre>
        `;
    }
    return `
        <div class="document-info">
            <p><strong>状态:</strong> <span class="decrypted-badge">已解密</span></p>
            <p><strong>文件大小:</strong> ${size} 字节</p>
            <p><strong>编码:</strong> ${enc}</p>
        </div>
        <pre class="document-text" style="max-height: 500px; overflow: auto; background: #f5f5f5; padding: 15px; border-radius: 6px; white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(data.content || '')}</pre>
    `;
}

function showDocumentError(data) {
    const errDiv = getEl('document-error');
    if (!errDiv) return;
    errDiv.style.display = 'block';
    let errorHtml = `
        <div class="result-message error">
            <strong>获取邮件内容失败</strong><br>
            ${escapeHtml(data.error || '未知错误')}
    `;
    if (data.hint) {
        errorHtml += `
            <br><br><strong>解决方案：</strong><br>
            <div style="background: #fff; padding: 10px; border-radius: 3px; margin-top: 10px; font-family: monospace;">
                <div># 若为真实邮件，请确保已运行 extract_database.go 或 enron_preprocess.py，并放置好 database_paths 与 maildir</div>
                <div># 若使用模拟文档，可扩展：python init_documents.py --files-per-writer 500</div>
            </div>
        `;
    }
    errorHtml += `</div>`;
    errDiv.innerHTML = errorHtml;
}

function closeDocumentModal() {
    const modal = document.querySelector('.document-modal');
    if (modal) modal.remove();
}

function escapeHtml(text) {
    return String(text || '').replace(/[&<>"']/g, function(m) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[m];
    });
}

async function downloadDecryptedDocument(writerId, fileId) {
    try {
        const data = await apiFetchJson('/api/document', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ writer_id: writerId, file_id: fileId })
        });

        if (data.success) {
            let blob;
            if (data.encoding === 'base64') {
                const binaryString = atob(data.content);
                const bytes = new Uint8Array(binaryString.length);
                for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
                blob = new Blob([bytes], { type: 'application/octet-stream' });
            } else {
                blob = new Blob([data.content], { type: 'text/plain;charset=utf-8' });
            }

            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `mail_employee_${writerId + 1}_file_${fileId}.txt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('文档下载成功', 'success');
        }
    } catch (error) {
        showToast('下载失败: ' + (error.message || String(error)), 'error');
    }
}

async function updateClientStatus() {
    const span = getEl('client-connection-status');
    const hint = getEl('client-load-error-hint');
    if (!span) return;
    try {
        const data = await apiFetchJson('/api/client-status');
        if (data.connected) {
            span.textContent = '已连接';
            span.className = 'client-status connected';
            if (hint) {
                hint.textContent = '';
                hint.style.display = 'none';
            }
        } else {
            span.textContent = '未连接';
            span.className = 'client-status disconnected';
            if (hint && data.library_load_error) {
                hint.textContent = '原因: ' + data.library_load_error + '。请先在 web_api 目录执行 make；若报错含缺少 .so，请安装依赖并设置 LD_LIBRARY_PATH 后重启 Flask。';
                hint.style.display = 'block';
            } else if (hint) {
                hint.textContent = '请点击「重试连接」或刷新页面。';
                hint.style.display = 'block';
            }
        }
    } catch (_) {
        span.textContent = '未知';
        span.className = 'client-status';
        if (hint) hint.style.display = 'none';
    }
}

async function requestReinitClient() {
    const statusSpan = getEl('client-connection-status');
    try {
        const data = await apiFetchJson('/api/reinit-client', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        if (data.success && data.connected) {
            showToast(data.message || '已连接 C++ server', 'success');
            if (statusSpan) {
                statusSpan.textContent = '已连接';
                statusSpan.className = 'client-status connected';
            }
        } else {
            showToast(data.error || '连接失败', 'error');
        }
    } catch (error) {
        showToast('请求失败: ' + (error.message || String(error)), 'error');
    }
}

function hideSearchResults() {
    const container = getEl('search-results');
    if (container) container.style.display = 'none';
}

function getTotalFileCount(results) {
    return (results || []).reduce((total, result) => total + (result.file_ids ? result.file_ids.length : 0), 0);
}

async function loadDocumentContent() {
    const writerIdEl = getEl('doc-update-writer-id');
    const fileIdInput = getEl('doc-update-file-id');
    if (!writerIdEl || !fileIdInput) return;

    const writerId = parseInt(writerIdEl.value, 10);
    const fileId = fileIdInput.value.trim() ? parseInt(fileIdInput.value, 10) : null;
    if (fileId == null || isNaN(fileId)) {
        showToast('请先输入文件 ID', 'error');
        return;
    }

    const pathHint = getEl('doc-path-hint');
    const contentArea = getEl('doc-update-content');
    const loadBtn = getEl('doc-load-btn');

    loadBtn.disabled = true;
    if (pathHint) pathHint.textContent = '加载中...';

    try {
        const data = await apiFetchJson(`/api/document-content?writer_id=${writerId}&file_id=${fileId}`);
        if (data.success) {
            if (pathHint) pathHint.textContent = data.path || '';
            if (contentArea) contentArea.value = data.content || '';
            showToast('原文已加载', 'success');
        } else {
            if (pathHint) pathHint.textContent = '';
            if (contentArea) contentArea.value = '';
            showToast(data.error || '加载失败', 'error');
        }
    } catch (e) {
        if (pathHint) pathHint.textContent = '';
        showToast('请求失败: ' + (e && e.message ? e.message : String(e)), 'error');
    } finally {
        loadBtn.disabled = false;
    }
}

async function handleSaveDocument() {
    const writerIdEl = getEl('doc-update-writer-id');
    const fileIdInput = getEl('doc-update-file-id');
    const contentArea = getEl('doc-update-content');
    const resultDiv = getEl('doc-update-result');
    const saveBtn = getEl('doc-save-btn');

    if (!writerIdEl || !fileIdInput || !contentArea || !resultDiv || !saveBtn) return;

    const writerId = parseInt(writerIdEl.value, 10);
    const fileId = fileIdInput.value.trim() ? parseInt(fileIdInput.value, 10) : null;
    if (fileId == null || isNaN(fileId)) {
        showToast('请先输入文件 ID', 'error');
        return;
    }

    const newContent = contentArea.value;
    saveBtn.disabled = true;
    resultDiv.style.display = 'none';

    try {
        const data = await apiFetchJson('/api/update-document', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ writer_id: writerId, file_id: fileId, new_content: newContent })
        });

        if (data.success) {
            resultDiv.className = 'result-message success';
            let msg = '<strong>✓ 更新文件成功</strong><br>' + (data.message || '');
            if (data.index_updated_on_server) {
                msg += '<br><small>可直接用新关键字查询。</small>';
            }
            resultDiv.innerHTML = msg;
            resultDiv.style.display = 'block';
            showToast('更新文件成功', 'success');
        } else {
            resultDiv.className = 'result-message error';
            resultDiv.innerHTML = '<strong>✗ 失败</strong><br>' + (data.error || '未知错误');
            resultDiv.style.display = 'block';
            showToast(data.error || '保存失败', 'error');
        }
    } catch (e) {
        resultDiv.className = 'result-message error';
        resultDiv.innerHTML = '<strong>✗ 请求失败</strong><br>' + (e && e.message ? e.message : String(e));
        resultDiv.style.display = 'block';
        showToast('请求失败', 'error');
    } finally {
        saveBtn.disabled = false;
    }
}

async function refreshStatus() {
    const statusContent = getEl('status-content');
    if (!statusContent) return;

    try {
        const data = await apiFetchJson('/api/status');

        if (data.status === 'online') {
            const allowedCount = data.allowed_writers_count != null ? data.allowed_writers_count : data.num_writers;
            statusContent.innerHTML = `
                <div class="status-item"><strong>系统状态:</strong> <span style="color: var(--success-color);">● 在线</span></div>
                <div class="status-item" style="margin-top: 15px;"><strong>云服务器地址:</strong> ${escapeHtml(data.server_address)}</div>
                <div class="status-item" style="margin-top: 15px;"><strong>云端写者数量:</strong> ${data.num_writers}</div>
                <div class="status-item" style="margin-top: 15px;"><strong>当前账号可访问写者数:</strong> ${allowedCount}</div>
                <div class="status-item" style="margin-top: 15px;"><strong>审计阶段 (Epoch):</strong> ${data.epoch != null ? data.epoch : '-'}</div>
                <div class="status-item" style="margin-top: 15px;"><strong>检索模式:</strong> ${data.search_mode === 'cpp' ? 'C++ 库' : (data.search_mode === 'cli_fallback' ? 'CLI 回退' : '-')}</div>
            `;
        } else {
            statusContent.innerHTML = `<div class="status-item"><strong>系统状态:</strong> <span style="color: var(--danger-color);">● 离线</span></div>`;
        }
    } catch (error) {
        statusContent.innerHTML = `<div class="status-item" style="color: var(--danger-color);"><strong>错误:</strong> 无法连接到服务器</div>`;
    }
}

function showToast(message, type = 'info') {
    const toast = getEl('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${type}`;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}
