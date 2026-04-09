// Hermes Web 前端（登录分流：reader / writer）

const API_BASE = (typeof window !== 'undefined' && window.location && window.location.protocol === 'file:')
    ? 'http://127.0.0.1:5000'
    : '';

let currentSearchKeyword = '';
let currentSearchResults = [];
let currentSearchTimeMs = null;
let currentWriterFilter = null; // writer_id in backend response (1-based)
let currentWriterDistributionRows = [];
let currentTopNRows = [];
let currentRiskRows = [];
const FILE_ID_PREVIEW_LIMIT = 30;
let expandedWriterIds = new Set();
let activeBatchEpoch = 1;

const CASE_STORAGE_KEY = 'hermes_reader_cases_v1';
const KEYWORD_PACKS = {
    financial_fraud: {
        label: '财务舞弊排查',
        keywords: ['deal', 'special purpose', 'offshore', 'hedging', 'guarantee', 'transfer', 'side letter', 'valuation', 'revenue', 'reserve']
    },
    insider_trading: {
        label: '内幕交易线索',
        keywords: ['confidential', 'earnings', 'guidance', 'acquisition', 'merger', 'announcement', 'material', 'nonpublic', 'board', 'rumor']
    },
    data_leakage: {
        label: '数据泄露风险',
        keywords: ['attachment', 'forward', 'external', 'download', 'client list', 'password', 'account', 'export', 'private', 'leak']
    }
};

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
        initReaderEnhancements();
    }
    if (getEl('doc-update-writer-id')) {
        loadWriters();
        updateClientStatus();
    }
});

function initReaderEnhancements() {
    initKeywordPackSelect();
    bindWriterSelectionUX();
    bindTopNInputPreview();
    refreshAuditBatch();
    renderCaseList();
}

async function refreshAuditBatch() {
    const badge = getEl('active-batch-badge');
    const input = getEl('batch-epoch-input');
    const hint = getEl('batch-switch-hint');
    try {
        const data = await apiFetchJson('/api/audit-batch');
        if (data && data.success) {
            activeBatchEpoch = parseInt(data.active_epoch, 10) || 1;
            if (badge) badge.textContent = `当前批次 Epoch: ${activeBatchEpoch}`;
            if (input) input.value = String(activeBatchEpoch);
            if (hint) hint.textContent = `默认批次为 ${data.default_epoch}。切换后请重新执行检索与分析。`;
            return;
        }
    } catch (_) {}
    if (badge) badge.textContent = `当前批次 Epoch: ${activeBatchEpoch}`;
}

async function switchAuditBatch() {
    const input = getEl('batch-epoch-input');
    const hint = getEl('batch-switch-hint');
    const epoch = input ? parseInt(input.value, 10) : NaN;
    if (isNaN(epoch) || epoch < 1) {
        showToast('请输入合法的批次 Epoch（>=1）', 'error');
        return;
    }
    const data = await apiFetchJson('/api/audit-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ epoch: epoch }),
    });
    if (!data || !data.success) {
        showToast((data && data.error) ? data.error : '切换批次失败', 'error');
        return;
    }
    activeBatchEpoch = epoch;
    const badge = getEl('active-batch-badge');
    if (badge) badge.textContent = `当前批次 Epoch: ${activeBatchEpoch}`;
    if (hint) hint.textContent = data.message || '';
    hideSearchResults();
    hideTopNResults();
    showToast(`已切换到 Epoch ${activeBatchEpoch}`, 'success');
}

function bindWriterSelectionUX() {
    const writerSelect = getEl('writer-select');
    if (!writerSelect) return;
    writerSelect.addEventListener('change', updateWriterSelectionCount);
}

function bindTopNInputPreview() {
    const textarea = getEl('topn-keywords');
    if (!textarea) return;
    textarea.addEventListener('input', updateTopNKeywordCount);
    updateTopNKeywordCount();
}

function updateTopNKeywordCount() {
    const textarea = getEl('topn-keywords');
    const hint = getEl('topn-keyword-count');
    if (!textarea || !hint) return;
    const count = parseBatchKeywords(textarea.value).length;
    hint.textContent = `当前将分析 ${count} 个唯一关键词`;
}

function initKeywordPackSelect() {
    const select = getEl('keyword-pack-select');
    if (!select) return;
    select.innerHTML = '';
    Object.keys(KEYWORD_PACKS).forEach(key => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = KEYWORD_PACKS[key].label;
        select.appendChild(option);
    });
}

function getSelectedKeywordPack() {
    const select = getEl('keyword-pack-select');
    if (!select) return null;
    return KEYWORD_PACKS[select.value] || null;
}

function loadKeywordPackToTopN() {
    const pack = getSelectedKeywordPack();
    const textarea = getEl('topn-keywords');
    if (!pack || !textarea) {
        showToast('未找到词包', 'error');
        return;
    }
    textarea.value = pack.keywords.join(', ');
    updateTopNKeywordCount();
    showToast(`已加载词包：${pack.label}`, 'success');
}

function runKeywordPackQuickScan() {
    const pack = getSelectedKeywordPack();
    const textarea = getEl('topn-keywords');
    const form = getEl('topn-form');
    if (!pack || !textarea || !form) {
        showToast('词包扫描失败', 'error');
        return;
    }
    textarea.value = pack.keywords.join(', ');
    updateTopNKeywordCount();
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
}

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
        updateWriterSelectionCount();
    } catch (error) {
        const msg = (error && error.message && /fetch|network|Failed to fetch/i.test(error.message))
            ? '无法连接后端。请通过 http://127.0.0.1:5000 打开页面并确保已运行 python app.py'
            : '加载写者列表失败';
        showToast(msg, 'error');
    }
}

function updateWriterSelectionCount() {
    const select = getEl('writer-select');
    const badge = getEl('writer-select-count');
    if (!select || !badge) return;
    const total = select.options.length;
    const selected = Array.from(select.selectedOptions).length;
    if (selected === 0) {
        badge.textContent = `当前: 全部已授权写者 (${total})`;
    } else {
        badge.textContent = `当前: 已选择 ${selected}/${total} 位写者`;
    }
}

function selectAllWriters() {
    const select = getEl('writer-select');
    if (!select) return;
    Array.from(select.options).forEach(option => { option.selected = true; });
    updateWriterSelectionCount();
}

function clearWritersSelection() {
    const select = getEl('writer-select');
    if (!select) return;
    Array.from(select.options).forEach(option => { option.selected = false; });
    updateWriterSelectionCount();
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
            displaySearchResults(keyword, data.results, data.search_time_ms, data.epoch);
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

async function handleTopNKeywords(event) {
    event.preventDefault();

    const raw = (getEl('topn-keywords') && getEl('topn-keywords').value) ? getEl('topn-keywords').value : '';
    const keywords = parseBatchKeywords(raw);
    if (keywords.length === 0) {
        showToast('请先输入至少一个关键词', 'error');
        hideTopNResults();
        return;
    }

    const topNInput = getEl('topn-limit');
    let topN = parseInt(topNInput ? topNInput.value : '10', 10);
    if (isNaN(topN) || topN < 1) topN = 10;
    if (topN > 100) topN = 100;

    const writerSelect = getEl('writer-select');
    const selectedOptions = writerSelect ? Array.from(writerSelect.selectedOptions) : [];
    const writerIds = selectedOptions.map(option => parseInt(option.value, 10));
    const writerIdsParam = writerIds.length > 0 ? writerIds : null;
    const progressEl = getEl('topn-progress');

    const submitBtn = event.target.querySelector('button[type="submit"]');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoading = submitBtn.querySelector('.btn-loading');
    submitBtn.disabled = true;
    btnText.style.display = 'none';
    btnLoading.style.display = 'inline';
    if (progressEl) progressEl.textContent = `正在分析 ${keywords.length} 个关键词，请稍候...`;

    try {
        const tasks = keywords.map(async keyword => {
            const data = await apiFetchJson('/api/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    keyword: keyword,
                    writer_ids: writerIdsParam
                })
            });
            if (data && data.success) {
                const total = getTotalFileCount(data.results);
                const writerCounts = {};
                (data.results || []).forEach(item => {
                    const count = item && item.file_ids ? item.file_ids.length : 0;
                    if (count > 0) writerCounts[item.writer_id] = count;
                });
                return {
                    keyword: keyword,
                    total: total,
                    writersHit: (data.results || []).filter(r => (r.file_ids || []).length > 0).length,
                    timeMs: data.search_time_ms,
                    writerCounts: writerCounts,
                    ok: true
                };
            }
            return {
                keyword: keyword,
                total: 0,
                writersHit: 0,
                timeMs: null,
                writerCounts: {},
                ok: false,
                error: (data && data.error) ? data.error : '检索失败'
            };
        });

        const all = await Promise.all(tasks);
        const sorted = all
            .sort((a, b) => {
                if (b.total !== a.total) return b.total - a.total;
                return a.keyword.localeCompare(b.keyword);
            });
        const topList = sorted.slice(0, topN);
        currentTopNRows = topList;
        renderTopNResults(keywords.length, topN, topList);
        renderRiskProfileFromTopN(topList);
        showToast(`Top-N 分析完成: 共分析 ${keywords.length} 个关键词`, 'success');
    } catch (error) {
        showToast('Top-N 分析失败: ' + (error.message || String(error)), 'error');
        hideTopNResults();
    } finally {
        submitBtn.disabled = false;
        btnText.style.display = 'inline';
        btnLoading.style.display = 'none';
        if (progressEl && progressEl.textContent.startsWith('正在分析')) progressEl.textContent = '';
    }
}

function parseBatchKeywords(rawText) {
    const parts = String(rawText || '')
        .split(/[\s,\n\r\t;，；]+/)
        .map(s => s.trim().toLowerCase())
        .filter(Boolean);
    const uniq = [];
    const seen = new Set();
    parts.forEach(word => {
        if (!seen.has(word)) {
            seen.add(word);
            uniq.push(word);
        }
    });
    return uniq.slice(0, 100);
}

function renderTopNResults(totalKeywords, topN, rows) {
    const panel = getEl('topn-results');
    const collapse = getEl('topn-collapse');
    const meta = getEl('topn-meta');
    const content = getEl('topn-results-content');
    if (!panel || !meta || !content) return;

    if (!rows || rows.length === 0) {
        panel.style.display = 'none';
        return;
    }

    const okCount = rows.filter(r => r.ok).length;
    meta.innerHTML = `<p class="search-meta-text">[Epoch ${activeBatchEpoch}] 已分析 <strong>${totalKeywords}</strong> 个关键词，展示 Top <strong>${Math.min(topN, rows.length)}</strong>；成功 <strong>${okCount}</strong> 个。</p>`;

    content.innerHTML = rows.map((row, idx) => `
        <div class="topn-row">
            <div class="topn-keyword">#${idx + 1} ${escapeHtml(row.keyword)}</div>
            <div class="topn-value">命中 ${row.total} 封</div>
            <div class="topn-value">覆盖 ${row.writersHit} 位写者</div>
        </div>
    `).join('');

    panel.style.display = 'block';
    if (collapse) collapse.open = true;
}

function hideTopNResults() {
    const panel = getEl('topn-results');
    if (panel) panel.style.display = 'none';
    currentTopNRows = [];
    renderRiskProfileFromTopN([]);
}

function renderRiskProfileFromTopN(rows) {
    const content = getEl('risk-profile-content');
    const collapse = getEl('risk-profile-collapse');
    if (!content) return;

    if (!rows || rows.length === 0) {
        currentRiskRows = [];
        content.innerHTML = '<div class="empty-state" style="padding: 16px;">先执行一次多关键词 Top-N 分析后显示画像。</div>';
        return;
    }

    const writerAgg = {};
    const maxRank = rows.length;
    rows.forEach((row, idx) => {
        const weight = maxRank - idx;
        const counts = row.writerCounts || {};
        Object.keys(counts).forEach(wid => {
            const writerId = parseInt(wid, 10);
            const cnt = parseInt(counts[wid], 10) || 0;
            if (!writerAgg[writerId]) {
                writerAgg[writerId] = { writerId: writerId, score: 0, hits: 0 };
            }
            writerAgg[writerId].score += cnt * weight;
            writerAgg[writerId].hits += cnt;
        });
    });

    const riskRows = Object.values(writerAgg).sort((a, b) => b.score - a.score);
    currentRiskRows = riskRows;
    if (riskRows.length === 0) {
        content.innerHTML = '<div class="empty-state" style="padding: 16px;">当前 Top-N 无有效命中，无法生成风险画像。</div>';
        return;
    }

    content.innerHTML = riskRows.map((row, idx) => `
        <div class="risk-row">
            <div class="risk-writer">#${idx + 1} 员工 ${row.writerId}</div>
            <div class="risk-metric">风险分值 ${row.score}</div>
            <div class="risk-metric">累计命中 ${row.hits} 封</div>
        </div>
    `).join('');

    if (collapse) collapse.open = true;
}

function getStoredCases() {
    try {
        const raw = localStorage.getItem(CASE_STORAGE_KEY);
        if (!raw) return [];
        const arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
    } catch (_) {
        return [];
    }
}

function setStoredCases(cases) {
    localStorage.setItem(CASE_STORAGE_KEY, JSON.stringify(cases));
}

function getCurrentSelectedWriterIds() {
    const writerSelect = getEl('writer-select');
    if (!writerSelect) return [];
    return Array.from(writerSelect.selectedOptions).map(option => parseInt(option.value, 10));
}

function saveCurrentCase() {
    const caseNameInput = getEl('case-name');
    const caseName = (caseNameInput && caseNameInput.value ? caseNameInput.value.trim() : '') || `Case-${new Date().toISOString().slice(0, 19)}`;
    const topnKeywords = getEl('topn-keywords') ? getEl('topn-keywords').value : '';
    const topnLimit = getEl('topn-limit') ? parseInt(getEl('topn-limit').value || '10', 10) : 10;
    const keyword = getEl('keyword') ? getEl('keyword').value.trim() : '';

    const newCase = {
        id: Date.now(),
        name: caseName,
        created_at: new Date().toLocaleString(),
        epoch: activeBatchEpoch,
        search: {
            keyword: keyword,
            selected_writer_ids: getCurrentSelectedWriterIds()
        },
        topn: {
            keywords_text: topnKeywords,
            limit: isNaN(topnLimit) ? 10 : topnLimit,
            rows: (currentTopNRows || []).map(r => ({ keyword: r.keyword, total: r.total, writersHit: r.writersHit }))
        },
        risk_profile: (currentRiskRows || []).map(r => ({ writerId: r.writerId, score: r.score, hits: r.hits }))
    };

    const cases = getStoredCases();
    cases.unshift(newCase);
    setStoredCases(cases.slice(0, 50));
    renderCaseList();
    showToast('任务单已保存', 'success');
}

function applyCase(caseId) {
    const cases = getStoredCases();
    const target = cases.find(c => c.id === caseId);
    if (!target) {
        showToast('任务单不存在', 'error');
        return;
    }
    const caseEpoch = parseInt(target.epoch || 1, 10);
    if (caseEpoch !== activeBatchEpoch) {
        showToast(`该任务单来自 Epoch ${caseEpoch}，当前为 Epoch ${activeBatchEpoch}。为符合前向隐私，仅回填条件，需要在当前批次重新检索。`, 'info');
    }

    if (getEl('keyword')) getEl('keyword').value = target.search && target.search.keyword ? target.search.keyword : '';
    if (getEl('topn-keywords')) getEl('topn-keywords').value = target.topn && target.topn.keywords_text ? target.topn.keywords_text : '';
    if (getEl('topn-limit')) getEl('topn-limit').value = (target.topn && target.topn.limit) ? target.topn.limit : 10;
    updateTopNKeywordCount();

    const writerSelect = getEl('writer-select');
    if (writerSelect && target.search && Array.isArray(target.search.selected_writer_ids)) {
        const selectedSet = new Set(target.search.selected_writer_ids);
        Array.from(writerSelect.options).forEach(option => {
            option.selected = selectedSet.has(parseInt(option.value, 10));
        });
    }

    showToast(`已应用任务单: ${target.name}`, 'success');
}

function deleteCase(caseId) {
    if (!window.confirm('确认删除这个任务单吗？此操作不可撤销。')) {
        return;
    }
    const next = getStoredCases().filter(c => c.id !== caseId);
    setStoredCases(next);
    renderCaseList();
    showToast('任务单已删除', 'success');
}

function renderCaseList() {
    const container = getEl('case-list');
    const countEl = getEl('case-count');
    if (!container) return;
    const cases = getStoredCases();
    if (countEl) countEl.textContent = `当前已保存 ${cases.length} 个任务单（本地浏览器）`;
    if (cases.length === 0) {
        container.innerHTML = '<div class="empty-state" style="padding: 12px;">暂无任务单，先保存一次当前分析。</div>';
        return;
    }
    container.innerHTML = cases.map(c => `
        <div class="case-item">
            <div class="case-item-head">
                <div>
                    <div class="case-item-title">${escapeHtml(c.name)}</div>
                    <div class="case-item-meta">Epoch ${escapeHtml((c.epoch != null ? c.epoch : 1).toString())} · ${escapeHtml(c.created_at || '-')} · 检索词: ${escapeHtml((c.search && c.search.keyword) || '-')}</div>
                </div>
                <div class="case-item-actions">
                    <button type="button" class="btn btn-secondary btn-sm" onclick="applyCase(${c.id})">应用</button>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="deleteCase(${c.id})">删除</button>
                </div>
            </div>
        </div>
    `).join('');
}

function exportCasesJson() {
    const cases = getStoredCases();
    if (!cases.length) {
        showToast('暂无可导出的任务单', 'error');
        return;
    }
    const blob = new Blob([JSON.stringify(cases, null, 2)], { type: 'application/json;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `hermes_cases_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('任务单 JSON 已导出', 'success');
}

function displaySearchResults(keyword, results, searchTimeMs, epochUsed) {
    const resultsContainer = getEl('search-results');
    const resultsContent = getEl('results-content');
    const searchMeta = getEl('search-meta');

    if (!resultsContainer || !resultsContent || !searchMeta) return;

    currentSearchKeyword = keyword;
    currentSearchResults = Array.isArray(results) ? results : [];
    currentSearchTimeMs = searchTimeMs;
    currentWriterFilter = null;
    expandedWriterIds = new Set();
    if (epochUsed != null) {
        activeBatchEpoch = parseInt(epochUsed, 10) || activeBatchEpoch;
        const badge = getEl('active-batch-badge');
        if (badge) badge.textContent = `当前批次 Epoch: ${activeBatchEpoch}`;
    }

    if (typeof searchTimeMs === 'number') {
        searchMeta.innerHTML = `<p class="search-meta-text">[Epoch ${activeBatchEpoch}] 关键字 "<strong>${escapeHtml(keyword)}</strong>" · 亚线性检索耗时 <strong>${searchTimeMs}</strong> ms</p>`;
        searchMeta.style.display = 'block';
    } else {
        searchMeta.innerHTML = '';
        searchMeta.style.display = 'none';
    }

    renderSearchResultsWithFilter();
    resultsContainer.style.display = 'block';
    renderWriterDistributionChart(keyword, currentSearchResults);
    resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderWriterDistributionChart(keyword, results) {
    const panel = getEl('writer-dist-panel');
    const collapse = getEl('writer-dist-collapse');
    const meta = getEl('writer-dist-meta');
    const chart = getEl('writer-dist-chart');
    if (!panel || !meta || !chart) return;

    const rows = (results || [])
        .map(item => {
            const count = item && item.file_ids ? item.file_ids.length : 0;
            return { writerId: item.writer_id, count: count };
        })
        .filter(item => item.count > 0)
        .sort((a, b) => b.count - a.count);
    currentWriterDistributionRows = rows;

    if (rows.length === 0) {
        panel.style.display = 'none';
        chart.innerHTML = '';
        meta.textContent = '';
        const clearBtn = getEl('writer-filter-clear-btn');
        if (clearBtn) clearBtn.style.display = 'none';
        return;
    }

    const maxCount = rows[0].count;
    const total = rows.reduce((sum, row) => sum + row.count, 0);
    meta.textContent = `关键字 "${keyword}" 在 ${rows.length} 位写者中有命中，总命中文件数 ${total}。`;

    chart.innerHTML = rows.map(row => {
        const pct = maxCount > 0 ? (row.count / maxCount) * 100 : 0;
        const isActive = currentWriterFilter === row.writerId;
        return `
            <div class="writer-dist-row ${isActive ? 'active' : ''}" onclick="applyWriterFilter(${row.writerId})" title="点击筛选为员工 ${row.writerId}">
                <div class="writer-dist-label">员工 ${row.writerId}</div>
                <div class="writer-dist-bar-wrap">
                    <div class="writer-dist-bar" style="width: ${pct.toFixed(2)}%;">${pct >= 25 ? row.count : ''}</div>
                </div>
                <div class="writer-dist-count">${row.count} 封</div>
            </div>
        `;
    }).join('');

    const clearBtn = getEl('writer-filter-clear-btn');
    if (clearBtn) clearBtn.style.display = currentWriterFilter == null ? 'none' : 'inline-flex';
    panel.style.display = 'block';
    if (collapse) collapse.open = true;
}

function renderSearchResultsWithFilter() {
    const resultsContent = getEl('results-content');
    const searchMeta = getEl('search-meta');
    if (!resultsContent || !searchMeta) return;

    const allResults = Array.isArray(currentSearchResults) ? currentSearchResults : [];
    const filteredResults = currentWriterFilter == null
        ? allResults
        : allResults.filter(result => result.writer_id === currentWriterFilter);

    if (typeof currentSearchTimeMs === 'number') {
        const filterText = currentWriterFilter == null ? '' : ` · 当前筛选: 员工 ${currentWriterFilter}`;
        searchMeta.innerHTML = `<p class="search-meta-text">[Epoch ${activeBatchEpoch}] 关键字 "<strong>${escapeHtml(currentSearchKeyword)}</strong>" · 亚线性检索耗时 <strong>${currentSearchTimeMs}</strong> ms${filterText}</p>`;
        searchMeta.style.display = 'block';
    }

    if (!filteredResults || filteredResults.length === 0) {
        const msg = currentWriterFilter == null
            ? `未找到包含关键字 "${escapeHtml(currentSearchKeyword)}" 的邮件`
            : `筛选后员工 ${currentWriterFilter} 无匹配邮件`;
        resultsContent.innerHTML = `<div class="empty-state"><p>${msg}</p></div>`;
        return;
    }

    let html = '';
    filteredResults.forEach(result => {
        if (result.file_ids && result.file_ids.length > 0) {
            const writerId = result.writer_id;
            const allIds = result.file_ids;
            const isExpanded = expandedWriterIds.has(writerId);
            const visibleIds = isExpanded ? allIds : allIds.slice(0, FILE_ID_PREVIEW_LIMIT);
            html += `
                <div class="result-item">
                    <h4>员工 ${writerId}</h4>
                    <p>匹配 ${result.file_ids.length} 封邮件，点击邮件ID查看明文内容:</p>
                    <div class="file-ids">
                        ${visibleIds.map(id =>
                            `<span class="file-id-badge" onclick="viewDocument(${writerId - 1}, ${id})" title="点击查看邮件内容">${id}</span>`
                        ).join('')}
                    </div>
                    ${allIds.length > FILE_ID_PREVIEW_LIMIT ? `
                        <div class="file-ids-toggle">
                            <button type="button" class="btn btn-secondary btn-sm" onclick="toggleWriterFileIds(${writerId})">
                                ${isExpanded ? '收起' : `展开剩余 ${allIds.length - FILE_ID_PREVIEW_LIMIT} 个`}
                            </button>
                        </div>
                    ` : ''}
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

function applyWriterFilter(writerId) {
    if (currentWriterFilter === writerId) {
        currentWriterFilter = null;
    } else {
        currentWriterFilter = writerId;
    }
    renderSearchResultsWithFilter();
    renderWriterDistributionChart(currentSearchKeyword, currentSearchResults);
}

function clearWriterFilter() {
    currentWriterFilter = null;
    renderSearchResultsWithFilter();
    renderWriterDistributionChart(currentSearchKeyword, currentSearchResults);
}

function toggleWriterFileIds(writerId) {
    if (expandedWriterIds.has(writerId)) {
        expandedWriterIds.delete(writerId);
    } else {
        expandedWriterIds.add(writerId);
    }
    renderSearchResultsWithFilter();
}

function exportWriterDistributionCsv() {
    if (!currentWriterDistributionRows || currentWriterDistributionRows.length === 0) {
        showToast('当前没有可导出的统计数据', 'error');
        return;
    }
    const lines = ['keyword,writer_id,matched_files_count'];
    currentWriterDistributionRows.forEach(row => {
        lines.push(`${csvEscape(currentSearchKeyword)},${row.writerId},${row.count}`);
    });
    const csvContent = lines.join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const safeKeyword = (currentSearchKeyword || 'keyword').replace(/[^a-zA-Z0-9_-]/g, '_');
    a.download = `writer_distribution_${safeKeyword}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('统计 CSV 已导出', 'success');
}

function csvEscape(value) {
    const s = String(value == null ? '' : value);
    if (/[",\n]/.test(s)) {
        return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
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
    const chartPanel = getEl('writer-dist-panel');
    if (chartPanel) chartPanel.style.display = 'none';
    currentWriterFilter = null;
    currentWriterDistributionRows = [];
    expandedWriterIds = new Set();
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
