// Hermes 多写作者邮件合规审计 - 审计员前端

// 若通过 file:// 打开页面，fetch 会发往错误地址导致 NetworkError，此处强制指向 Flask 默认端口
const API_BASE = (typeof window !== 'undefined' && window.location && window.location.protocol === 'file:')
    ? 'http://127.0.0.1:5000'
    : '';

document.addEventListener('DOMContentLoaded', function() {
    loadWriters();
    refreshStatus();
    updateClientStatus();
});

// 标签页切换
function switchTab(tabName) {
    // 隐藏所有标签页内容
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    
    // 移除所有按钮的active类
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    
    // 显示选中的标签页
    document.getElementById(`${tabName}-tab`).classList.add('active');
    
    // 激活对应的按钮
    event.target.classList.add('active');
    
    if (tabName === 'update') updateClientStatus();
}

// 加载写入者列表
async function loadWriters() {
    try {
        const response = await fetch(`${API_BASE}/api/writers`);
        const data = await response.json();
        
        if (data.success) {
            const searchSelect = document.getElementById('writer-select');
            const docUpdateSelect = document.getElementById('doc-update-writer-id');
            
            searchSelect.innerHTML = '';
            if (docUpdateSelect) docUpdateSelect.innerHTML = '';
            
            data.writers.forEach(writer => {
                const option1 = document.createElement('option');
                option1.value = writer.id;
                option1.textContent = '员工 ' + (writer.id + 1);
                searchSelect.appendChild(option1);
                
                if (docUpdateSelect) {
                    const option2 = document.createElement('option');
                    option2.value = writer.id;
                    option2.textContent = '员工 ' + (writer.id + 1);
                    docUpdateSelect.appendChild(option2);
                }
            });
        }
    } catch (error) {
        console.error('Failed to load writers:', error);
        const msg = (error && error.message && /fetch|network|Failed to fetch/i.test(error.message))
            ? '无法连接后端。请通过 http://127.0.0.1:5000 打开页面并确保已运行 python app.py'
            : '加载写入者列表失败';
        showToast(msg, 'error');
    }
}

// 处理搜索表单提交
async function handleSearch(event) {
    event.preventDefault();
    
    const keyword = document.getElementById('keyword').value.trim();
    const writerSelect = document.getElementById('writer-select');
    const selectedOptions = Array.from(writerSelect.selectedOptions);
    const writerIds = selectedOptions.map(option => parseInt(option.value));
    
    // 如果未选择任何写入者，则搜索所有
    const writerIdsParam = writerIds.length > 0 ? writerIds : null;
    
    const submitBtn = event.target.querySelector('button[type="submit"]');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoading = submitBtn.querySelector('.btn-loading');
    
    // 显示加载状态
    submitBtn.disabled = true;
    btnText.style.display = 'none';
    btnLoading.style.display = 'inline';
    
    try {
        const response = await fetch(`${API_BASE}/api/search`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                keyword: keyword,
                writer_ids: writerIdsParam
            })
        });
        
        const data = await response.json();
        
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
        console.error('Search error:', error);
        const msg = (error && error.message && /fetch|network|Failed to fetch/i.test(error.message))
            ? '无法连接后端。请确保通过 http://127.0.0.1:5000 或 http://localhost:5000 打开页面，且已运行 python app.py'
            : ('搜索请求失败: ' + (error && error.message ? error.message : String(error)));
        showToast(msg, 'error');
        hideSearchResults();
    } finally {
        // 恢复按钮状态
        submitBtn.disabled = false;
        btnText.style.display = 'inline';
        btnLoading.style.display = 'none';
    }
}

// 显示检索结果（员工 = 写作者，展示为“员工 1、员工 2…”）
function displaySearchResults(keyword, results, searchTimeMs) {
    const resultsContainer = document.getElementById('search-results');
    const resultsContent = document.getElementById('results-content');
    const searchMeta = document.getElementById('search-meta');
    
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

// 查看文档内容：先显示加密内容，点击「解密」后再显示原文
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
        // 先请求加密内容（不解密）
        const response = await fetch(`${API_BASE}/api/document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                writer_id: writerId,
                file_id: fileId,
                decrypt: false
            })
        });
        const data = await response.json();
        document.getElementById('document-loading').style.display = 'none';

        if (data.success && data.encrypted) {
            const contentDiv = document.getElementById('document-content');
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
            // 接口直接返回了明文（兼容旧行为）
            const contentDiv = document.getElementById('document-content');
            contentDiv.style.display = 'block';
            contentDiv.innerHTML = buildDecryptedContentHtml(data, writerId, fileId);
        } else {
            showDocumentError(data);
        }
    } catch (error) {
        document.getElementById('document-loading').style.display = 'none';
        document.getElementById('document-error').style.display = 'block';
        document.getElementById('document-error').innerHTML = `
            <div class="result-message error">
                <strong>错误</strong><br>请求失败: ${escapeHtml(error.message)}
            </div>
        `;
    }
}

// 点击「解密」后请求原文并替换展示
async function requestDecryptDocument(writerId, fileId) {
    const btn = document.getElementById('btn-decrypt-doc');
    const contentDiv = document.getElementById('document-content');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '解密中...';
    }
    try {
        const response = await fetch(`${API_BASE}/api/document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                writer_id: writerId,
                file_id: fileId,
                decrypt: true
            })
        });
        const data = await response.json();
        if (btn) {
            btn.disabled = false;
            btn.textContent = '解密';
        }
        if (data.success && contentDiv) {
            contentDiv.innerHTML = buildDecryptedContentHtml(data, writerId, fileId);
        } else {
            if (contentDiv) {
                contentDiv.innerHTML = `
                    <div class="result-message error">
                        <strong>解密失败</strong><br>${escapeHtml(data.error || '未知错误')}
                    </div>
                `;
            }
        }
    } catch (error) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '解密';
        }
        if (contentDiv) {
            contentDiv.innerHTML = `
                <div class="result-message error">
                    <strong>错误</strong><br>请求失败: ${escapeHtml(error.message)}
                </div>
            `;
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
    const errDiv = document.getElementById('document-error');
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

// 关闭文档模态框
function closeDocumentModal() {
    const modal = document.querySelector('.document-modal');
    if (modal) {
        modal.remove();
    }
}

// HTML转义函数
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

// 下载解密后的文档
async function downloadDecryptedDocument(writerId, fileId) {
    try {
        const response = await fetch(`${API_BASE}/api/document`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                writer_id: writerId,
                file_id: fileId
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // 如果是base64编码，解码后下载
            let blob;
            if (data.encoding === 'base64') {
                const binaryString = atob(data.content);
                const bytes = new Uint8Array(binaryString.length);
                for (let i = 0; i < binaryString.length; i++) {
                    bytes[i] = binaryString.charCodeAt(i);
                }
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
        showToast('下载失败: ' + error.message, 'error');
    }
}

// 更新「索引更新」页中的 C++ 连接状态显示
async function updateClientStatus() {
    const span = document.getElementById('client-connection-status');
    const hint = document.getElementById('client-load-error-hint');
    if (!span) return;
    try {
        const response = await fetch(`${API_BASE}/api/client-status`);
        const data = await response.json();
        if (data.connected) {
            span.textContent = '已连接';
            span.className = 'client-status connected';
            if (hint) { hint.textContent = ''; hint.style.display = 'none'; }
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
        if (hint) { hint.style.display = 'none'; }
    }
}

// 重试连接 C++ server（解决 server 先启动、app 后启动导致未连接的情况）
async function requestReinitClient() {
    const resultDiv = document.getElementById('update-result');
    const statusSpan = document.getElementById('client-connection-status');
    try {
        const response = await fetch(`${API_BASE}/api/reinit-client`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await response.json();
        if (data.success && data.connected) {
            showToast(data.message || '已连接 C++ server', 'success');
            if (resultDiv) {
                resultDiv.className = 'result-message success';
                resultDiv.innerHTML = '<strong>✓ ' + (data.message || '已连接 C++ server') + '</strong><br><small>可点击「从 database 重新加载索引」</small>';
                resultDiv.style.display = 'block';
            }
            if (statusSpan) {
                statusSpan.textContent = '已连接';
                statusSpan.className = 'client-status connected';
            }
        } else {
            showToast(data.error || '连接失败', 'error');
            if (resultDiv) {
                resultDiv.className = 'result-message error';
                resultDiv.innerHTML = '<strong>重试连接失败</strong><br>' + escapeHtml(data.error || '未知错误') + '<br><button type="button" class="btn btn-primary" style="margin-top:8px" onclick="requestReinitClient()">再次重试</button>';
                resultDiv.style.display = 'block';
            }
        }
    } catch (error) {
        showToast('请求失败: ' + error.message, 'error');
        if (resultDiv) {
            resultDiv.className = 'result-message error';
            resultDiv.innerHTML = '<strong>请求失败</strong><br>' + escapeHtml(error.message);
            resultDiv.style.display = 'block';
        }
    }
}

// 从 database 重新加载索引（使检索反映已更新的 database 文件）
async function requestReloadIndex() {
    const btn = document.getElementById('btn-reload-index');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '加载中...';
    }
    try {
        const response = await fetch(`${API_BASE}/api/reload-index`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await response.json();
        if (btn) {
            btn.disabled = false;
            btn.textContent = '从 database 重新加载索引';
        }
        if (data.success) {
            showToast(data.message || '索引已重新加载', 'success');
            const resultDiv = document.getElementById('update-result') || document.getElementById('doc-update-result');
            if (resultDiv) {
                resultDiv.className = 'result-message success';
                resultDiv.innerHTML = '<strong>✓ ' + (data.message || '索引已从 database 重新加载') + '</strong>';
                resultDiv.style.display = 'block';
            }
        } else {
            showToast(data.error || '重新加载失败', 'error');
            const resultDiv = document.getElementById('update-result') || document.getElementById('doc-update-result');
            if (resultDiv) {
                resultDiv.className = 'result-message error';
                let html = '<strong>重新加载失败</strong><br>' + escapeHtml(data.error || '未知错误');
                if (data.need_reinit) {
                    html += '<br><button type="button" class="btn btn-primary" style="margin-top:8px" onclick="requestReinitClient()">重试连接</button>';
                }
                resultDiv.innerHTML = html;
                resultDiv.style.display = 'block';
            }
        }
    } catch (error) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '从 database 重新加载索引';
        }
        showToast('请求失败: ' + error.message, 'error');
    }
}

// 隐藏搜索结果
function hideSearchResults() {
    document.getElementById('search-results').style.display = 'none';
}

// 计算总文件数
function getTotalFileCount(results) {
    return results.reduce((total, result) => {
        return total + (result.file_ids ? result.file_ids.length : 0);
    }, 0);
}

// 文档内容更新：从 database_paths 解析路径并加载原文
async function loadDocumentContent() {
    const writerId = parseInt(document.getElementById('doc-update-writer-id').value);
    const fileIdInput = document.getElementById('doc-update-file-id');
    const fileId = fileIdInput.value.trim() ? parseInt(fileIdInput.value) : null;
    if (fileId == null || isNaN(fileId)) {
        showToast('请先输入文件 ID', 'error');
        return;
    }
    const pathHint = document.getElementById('doc-path-hint');
    const contentArea = document.getElementById('doc-update-content');
    const loadBtn = document.getElementById('doc-load-btn');
    loadBtn.disabled = true;
    pathHint.textContent = '加载中...';
    try {
        const response = await fetch(`${API_BASE}/api/document-content?writer_id=${writerId}&file_id=${fileId}`);
        const data = await response.json();
        if (data.success) {
            pathHint.textContent = data.path || '';
            contentArea.value = data.content || '';
            showToast('原文已加载', 'success');
        } else {
            pathHint.textContent = '';
            contentArea.value = '';
            showToast(data.error || '加载失败', 'error');
        }
    } catch (e) {
        pathHint.textContent = '';
        showToast('请求失败: ' + (e && e.message ? e.message : String(e)), 'error');
    } finally {
        loadBtn.disabled = false;
    }
}

// 文档内容更新：用文本框内容覆盖原文件并重建该用户 database
async function handleSaveDocument() {
    const writerId = parseInt(document.getElementById('doc-update-writer-id').value);
    const fileIdInput = document.getElementById('doc-update-file-id');
    const fileId = fileIdInput.value.trim() ? parseInt(fileIdInput.value) : null;
    if (fileId == null || isNaN(fileId)) {
        showToast('请先输入文件 ID', 'error');
        return;
    }
    const newContent = document.getElementById('doc-update-content').value;
    const resultDiv = document.getElementById('doc-update-result');
    const saveBtn = document.getElementById('doc-save-btn');
    saveBtn.disabled = true;
    resultDiv.style.display = 'none';
    try {
        const response = await fetch(`${API_BASE}/api/update-document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ writer_id: writerId, file_id: fileId, new_content: newContent })
        });
        const data = await response.json();
        if (data.success) {
            resultDiv.className = 'result-message success';
            let msg = '<strong>✓ 更新文件成功</strong><br>' + (data.message || '');
            if (data.index_updated_on_server) {
                msg += '<br><small>可直接在检索页用新关键字查询。</small>';
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

// 刷新系统状态（含 Epoch、授权员工数）
async function refreshStatus() {
    const statusContent = document.getElementById('status-content');
    
    try {
        const response = await fetch(`${API_BASE}/api/status`);
        const data = await response.json();
        
        if (data.status === 'online') {
            const allowedCount = data.allowed_writers_count != null ? data.allowed_writers_count : data.num_writers;
            statusContent.innerHTML = `
                <div class="status-item">
                    <strong>系统状态:</strong> 
                    <span style="color: var(--success-color);">● 在线</span>
                </div>
                <div class="status-item" style="margin-top: 15px;">
                    <strong>云服务器地址:</strong> ${escapeHtml(data.server_address)}
                </div>
                <div class="status-item" style="margin-top: 15px;">
                    <strong>云端员工（写作者）数量:</strong> ${data.num_writers}
                </div>
                <div class="status-item" style="margin-top: 15px;">
                    <strong>当前审计员可检索员工数:</strong> ${allowedCount}
                </div>
                <div class="status-item" style="margin-top: 15px;">
                    <strong>审计阶段 (Epoch):</strong> ${data.epoch != null ? data.epoch : '-'}
                </div>
                <div class="status-item" style="margin-top: 15px;">
                    <strong>检索模式:</strong> ${data.search_mode === 'cpp' ? 'C++ 库' : (data.search_mode === 'cli_fallback' ? 'CLI 回退' : '-')}
                </div>
            `;
        } else {
            statusContent.innerHTML = `
                <div class="status-item">
                    <strong>系统状态:</strong> 
                    <span style="color: var(--danger-color);">● 离线</span>
                </div>
            `;
        }
    } catch (error) {
        console.error('Failed to fetch status:', error);
        statusContent.innerHTML = `
            <div class="status-item" style="color: var(--danger-color);">
                <strong>错误:</strong> 无法连接到服务器
            </div>
        `;
    }
}

// 显示Toast消息
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type}`;
    toast.classList.add('show');
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}


