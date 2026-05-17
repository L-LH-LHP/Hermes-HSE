// Hermes Web 前端（登录分流：reader / writer）

const API_BASE = (typeof window !== 'undefined' && window.location && window.location.protocol === 'file:')
    ? 'http://127.0.0.1:5000'
    : '';

let currentSearchKeyword = '';
let currentSearchResults = [];
let currentSearchTimeMs = null;
let currentSearchDiffInfo = null;
let currentWriterFilter = null; // writer_id in backend response (1-based)
let currentWriterDistributionRows = [];
let currentTopNRows = [];
let currentGraphRows = [];
let currentRiskRows = [];
const FILE_ID_PREVIEW_LIMIT = 30;
const GRAPH_FILE_HIT_LIMIT_PER_KEYWORD = 120;
let expandedWriterIds = new Set();
let activeBatchEpoch = 1;
let epochTrendChartInstance = null;

const CASE_STORAGE_KEY = 'hermes_reader_cases_v1';
const SEARCH_SNAPSHOT_STORAGE_KEY = 'hermes_search_snapshots_v1';
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

const FUZZY_EXPANSION_MAP = {
    bribe: ['bribe', 'bribes', 'bribed', 'bribing', 'bribery'],
    bribery: ['bribery', 'bribe', 'bribes', 'bribing'],
    corrupt: ['corrupt', 'corrupts', 'corrupted', 'corrupting', 'corruption', 'corruptly'],
    corruption: ['corruption', 'corrupt', 'corrupted', 'corrupting'],
    fraud: ['fraud', 'frauds', 'fraudulent', 'fraudulently'],
    launder: ['launder', 'launders', 'laundered', 'laundering'],
    leak: ['leak', 'leaks', 'leaked', 'leaking', 'leakage'],
    embezzle: ['embezzle', 'embezzles', 'embezzled', 'embezzling', 'embezzlement'],
    extort: ['extort', 'extorts', 'extorted', 'extorting', 'extortion'],
    kickback: ['kickback', 'kickbacks'],
    payoff: ['payoff', 'payoffs'],
    insider: ['insider', 'insiders', 'inside'],
    trade: ['trade', 'trades', 'traded', 'trading'],
    deal: ['deal', 'deals', 'dealing', 'dealt'],
    transfer: ['transfer', 'transfers', 'transferred', 'transferring'],
    guarantee: ['guarantee', 'guarantees', 'guaranteed', 'guaranteeing'],
    offshore: ['offshore', 'offshoring'],
};

function getEl(id) {
    return document.getElementById(id);
}

function switchReaderPanel(panelId) {
    document.querySelectorAll('.app-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === panelId);
    });
    document.querySelectorAll('.app-nav-item[data-panel]').forEach(item => {
        item.classList.toggle('active', item.dataset.panel === panelId);
    });
}

function switchTab(tabName) {
    const legacyTarget = getEl(`${tabName}-tab`);
    if (legacyTarget) {
        document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(panel => panel.classList.remove('active'));
        legacyTarget.classList.add('active');
        const trigger = document.querySelector(`.tab-btn[onclick*="'${tabName}'"], .tab-btn[onclick*="${tabName}"]`);
        if (trigger) trigger.classList.add('active');
        return;
    }

    const contentTarget = getEl(`content-${tabName}`);
    if (!contentTarget) return;
    document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(panel => panel.classList.remove('active'));
    const tab = getEl(`tab-${tabName}`);
    if (tab) tab.classList.add('active');
    contentTarget.classList.add('active');
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
    bindBooleanKeywordPreview();
    bindFuzzyExpansionPreview();
    refreshAuditBatch();
    renderCaseList();
}

async function refreshAuditBatch() {
    const badge = getEl('active-batch-badge');
    const hint = getEl('batch-switch-hint');
    if (badge) badge.textContent = '检索轮次: 自动记录';
    if (hint) hint.textContent = '再次执行相同查询时，新增命中文件会以红色高亮显示。';
}

async function switchAuditBatch() {
    const hint = getEl('batch-switch-hint');
    if (hint) hint.textContent = '当前版本不再手动切换 Epoch；检索轮次由同一查询的连续搜索自动形成。';
    showToast('检索轮次会自动记录，无需手动切换', 'info');
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

function bindBooleanKeywordPreview() {
    const textarea = getEl('boolean-keywords');
    if (!textarea) return;
    textarea.addEventListener('input', updateBooleanKeywordCount);
    updateBooleanKeywordCount();
}

function bindFuzzyExpansionPreview() {
    const input = getEl('fuzzy-keyword');
    if (!input) return;
    input.addEventListener('input', updateFuzzyExpansionPreview);
    updateFuzzyExpansionPreview();
}

function updateTopNKeywordCount() {
    const textarea = getEl('topn-keywords');
    const hint = getEl('topn-keyword-count');
    if (!textarea || !hint) return;
    const count = parseBatchKeywords(textarea.value).length;
    hint.textContent = `当前将分析 ${count} 个唯一关键词`;
}

function updateBooleanKeywordCount() {
    const textarea = getEl('boolean-keywords');
    const hint = getEl('boolean-keyword-count');
    if (!textarea || !hint) return;
    const count = parseBooleanKeywords(textarea.value).length;
    hint.textContent = `当前将检索 ${count} 个唯一关键词`;
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
    const usernameInput = getEl('login-username');
    const form = getEl('login-form');

    function refreshLoginMode() {
        const role = roleSelect.value;
        if (role === 'writer') {
            readerGroup.style.display = 'none';
            writerGroup.style.display = 'block';
            loginHint.textContent = '默认写者密码规则: writer{writer_id+1}，例如 writer_id=0 的密码是 writer1';
            // 写者角色不需要用户名框，无需设置值
        } else if (role === 'admin') {
            readerGroup.style.display = 'block';
            writerGroup.style.display = 'none';
            loginHint.textContent = '默认管理员账号: admin / admin123';
            if (usernameInput) usernameInput.value = 'admin';   // 关键：自动填充 admin
        } else { // reader
            readerGroup.style.display = 'block';
            writerGroup.style.display = 'none';
            loginHint.textContent = '默认读者账号：reader / reader123';
            if (usernameInput) usernameInput.value = 'reader';  // 自动填充 reader
        }
    }

    roleSelect.addEventListener('change', refreshLoginMode);
    refreshLoginMode();   // 页面加载时执行一次，使初始状态正确

    // 只添加一次提交监听
    form.addEventListener('submit', async function(event) {
        event.preventDefault();

        const role = roleSelect.value;
        const username = (usernameInput?.value || '').trim();
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
            if (role === 'reader' || role === 'admin') {
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

function buildKeywordExpansions(rawKeyword) {
    const base = String(rawKeyword || '').trim().toLowerCase();
    if (!base) return [];

    const forms = new Set([base]);
    const mapped = FUZZY_EXPANSION_MAP[base];
    if (mapped) {
        mapped.forEach(term => forms.add(term.toLowerCase()));
    }

    // For phrases, avoid unsafe suffix expansion; exact phrase variants should be explicit in the map.
    if (/\s/.test(base)) {
        return Array.from(forms).slice(0, 12);
    }

    if (base.length > 3) {
        forms.add(`${base}s`);
        if (base.endsWith('e')) {
            forms.add(`${base}d`);
            forms.add(`${base.slice(0, -1)}ing`);
        } else if (base.endsWith('y')) {
            forms.add(`${base.slice(0, -1)}ies`);
            forms.add(`${base.slice(0, -1)}ied`);
        } else {
            forms.add(`${base}ed`);
            forms.add(`${base}ing`);
        }
    }

    return Array.from(forms)
        .filter(term => term.length > 1)
        .slice(0, 12);
}

function updateFuzzyExpansionPreview() {
    const input = getEl('fuzzy-keyword');
    const preview = getEl('fuzzy-expansion-preview');
    if (!input || !preview) return;
    const terms = buildKeywordExpansions(input.value);
    if (!terms.length) {
        preview.textContent = '输入后将自动生成扩展词。';
        return;
    }
    preview.innerHTML = `将检索 ${terms.length} 个精确关键词：${terms.map(term => `<span class="term-chip">${escapeHtml(term)}</span>`).join('')}`;
}

async function searchCurrentEpochKeyword(keyword, writerIdsParam) {
    const data = await apiFetchJson('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            keyword: keyword,
            writer_ids: writerIdsParam
        })
    });
    if (!data || !data.success) {
        throw new Error((data && data.error) ? data.error : `关键词 ${keyword} 检索失败`);
    }
    return data;
}

function mergeSearchResponses(responses) {
    const writerMap = new Map();
    responses.forEach(item => {
        const term = item.term;
        ((item.data && item.data.results) || []).forEach(result => {
            const writerId = parseInt(result && result.writer_id, 10);
            if (isNaN(writerId)) return;
            if (!writerMap.has(writerId)) writerMap.set(writerId, new Map());
            const fileMap = writerMap.get(writerId);
            (result.file_ids || []).forEach(fileId => {
                const fid = parseInt(fileId, 10);
                if (isNaN(fid)) return;
                if (!fileMap.has(fid)) fileMap.set(fid, new Set());
                fileMap.get(fid).add(term);
            });
        });
    });

    return Array.from(writerMap.entries())
        .map(([writerId, fileMap]) => ({
            writer_id: writerId,
            file_ids: Array.from(fileMap.keys()).sort((a, b) => a - b),
            term_hits: fileMap,
        }))
        .filter(row => row.file_ids.length > 0)
        .sort((a, b) => b.file_ids.length - a.file_ids.length || a.writer_id - b.writer_id);
}

function collectResponseFileMaps(responses) {
    return responses.map(item => {
        const writerMap = new Map();
        ((item.data && item.data.results) || []).forEach(result => {
            const writerId = parseInt(result && result.writer_id, 10);
            if (isNaN(writerId)) return;
            if (!writerMap.has(writerId)) writerMap.set(writerId, new Set());
            const fileSet = writerMap.get(writerId);
            (result.file_ids || []).forEach(fileId => {
                const fid = parseInt(fileId, 10);
                if (!isNaN(fid)) fileSet.add(fid);
            });
        });
        return { term: item.term, writerMap: writerMap };
    });
}

function combineBooleanSearchResponses(responses, mode) {
    const normalizedMode = mode === 'OR' ? 'OR' : 'AND';
    const termMaps = collectResponseFileMaps(responses);
    if (!termMaps.length) return [];

    const writerIds = new Set();
    termMaps.forEach(termMap => {
        termMap.writerMap.forEach((_, writerId) => writerIds.add(writerId));
    });

    const rows = [];
    writerIds.forEach(writerId => {
        let combined = null;
        if (normalizedMode === 'AND') {
            for (const termMap of termMaps) {
                const files = termMap.writerMap.get(writerId) || new Set();
                if (combined === null) {
                    combined = new Set(files);
                } else {
                    combined = new Set(Array.from(combined).filter(fileId => files.has(fileId)));
                }
                if (combined.size === 0) break;
            }
        } else {
            combined = new Set();
            termMaps.forEach(termMap => {
                const files = termMap.writerMap.get(writerId) || new Set();
                files.forEach(fileId => combined.add(fileId));
            });
        }

        const fileIds = Array.from(combined || []).sort((a, b) => a - b);
        if (fileIds.length > 0) {
            rows.push({ writer_id: writerId, file_ids: fileIds });
        }
    });

    return rows.sort((a, b) => b.file_ids.length - a.file_ids.length || a.writer_id - b.writer_id);
}

async function handleBooleanSearch(event) {
    event.preventDefault();

    const textarea = getEl('boolean-keywords');
    const modeInput = document.querySelector('input[name="boolean-mode"]:checked');
    const mode = modeInput && modeInput.value === 'OR' ? 'OR' : 'AND';
    const keywords = parseBooleanKeywords(textarea ? textarea.value : '');
    const panel = getEl('boolean-search-results');
    const progressEl = getEl('boolean-search-progress');
    const meta = getEl('boolean-search-meta');
    const termsEl = getEl('boolean-search-terms');
    const detailEl = getEl('boolean-search-detail');

    if (keywords.length < 2) {
        showToast('请至少输入 2 个关键词用于联合检索', 'error');
        return;
    }

    const writerIdsParam = getWriterIdsParamForSearch();
    setSubmitLoading(event.target, true);
    if (panel) panel.style.display = 'block';
    if (progressEl) progressEl.textContent = `正在并发执行 ${keywords.length} 个独立关键词检索...`;
    if (meta) meta.innerHTML = `<p class="search-meta-text">正在生成 ${keywords.length} 个独立搜索请求，并在客户端计算 ${mode}。</p>`;
    if (termsEl) termsEl.innerHTML = keywords.map(term => `<span class="term-chip">${escapeHtml(term)}</span>`).join('');
    if (detailEl) detailEl.innerHTML = '';

    try {
        const startedAt = performance.now();
        const responses = await Promise.all(keywords.map(async keyword => ({
            term: keyword,
            data: await searchCurrentEpochKeyword(keyword, writerIdsParam)
        })));
        const elapsedMs = Number((performance.now() - startedAt).toFixed(2));
        const combined = combineBooleanSearchResponses(responses, mode);
        const total = getTotalFileCount(combined);
        const perTermRows = responses.map(item => ({
            term: item.term,
            total: getTotalFileCount((item.data && item.data.results) || []),
            writersHit: ((item.data && item.data.results) || []).filter(r => (r.file_ids || []).length > 0).length,
            timeMs: item.data && typeof item.data.search_time_ms === 'number' ? item.data.search_time_ms : null,
        }));

        renderBooleanSearchSummary(keywords, mode, combined, perTermRows, elapsedMs);
        const label = `${mode}(${keywords.join(', ')})`;
        displaySearchResults(label, combined, elapsedMs, activeBatchEpoch);
        const collapse = getEl('boolean-search-collapse');
        if (collapse) collapse.open = true;
        showToast(`${mode} 联合检索完成: 找到 ${total} 封匹配邮件`, 'success');
    } catch (error) {
        if (meta) {
            meta.innerHTML = `<div class="result-message error"><strong>联合检索失败</strong><br>${escapeHtml(error.message || String(error))}</div>`;
        }
        if (detailEl) detailEl.innerHTML = '';
        showToast('联合检索失败: ' + (error.message || String(error)), 'error');
    } finally {
        if (progressEl && progressEl.textContent.startsWith('正在并发')) progressEl.textContent = '';
        setSubmitLoading(event.target, false);
    }
}

function renderBooleanSearchSummary(keywords, mode, combinedRows, perTermRows, elapsedMs) {
    const panel = getEl('boolean-search-results');
    const progressEl = getEl('boolean-search-progress');
    const meta = getEl('boolean-search-meta');
    const detailEl = getEl('boolean-search-detail');
    if (!panel || !meta || !detailEl) return;

    const total = getTotalFileCount(combinedRows);
    const writersHit = (combinedRows || []).filter(row => (row.file_ids || []).length > 0).length;
    const modeText = mode === 'AND' ? '交集 AND' : '并集 OR';
    meta.innerHTML = `
        <p class="search-meta-text">
            ${modeText} · ${keywords.length} 个独立关键词 Token ·
            命中 <strong>${total}</strong> 封，覆盖 <strong>${writersHit}</strong> 位写者 · 前端总耗时 <strong>${elapsedMs}</strong> ms
        </p>
    `;

    detailEl.innerHTML = `
        <div class="boolean-term-table">
            ${perTermRows.map(row => `
                <div class="boolean-term-row">
                    <div class="boolean-term-name">${escapeHtml(row.term)}</div>
                    <div class="boolean-term-stat">命中 ${row.total} 封</div>
                    <div class="boolean-term-stat">覆盖 ${row.writersHit} 位写者</div>
                    <div class="boolean-term-stat">${typeof row.timeMs === 'number' ? `${row.timeMs} ms` : '-'}</div>
                </div>
            `).join('')}
        </div>
    `;
    if (progressEl) progressEl.textContent = total > 0 ? '联合结果已同步显示在上方“检索结果”区域。' : '联合检索完成，但没有文件满足当前条件。';
    panel.style.display = 'block';
}

async function handleFuzzySearch(event) {
    event.preventDefault();

    const input = getEl('fuzzy-keyword');
    const summary = getEl('fuzzy-search-summary');
    const meta = getEl('fuzzy-search-meta');
    const termsEl = getEl('fuzzy-search-terms');
    const keyword = input ? input.value.trim() : '';
    const terms = buildKeywordExpansions(keyword);
    if (!terms.length) {
        showToast('请输入要扩展检索的关键词', 'error');
        return;
    }

    const writerIdsParam = getWriterIdsParamForSearch();
    setSubmitLoading(event.target, true);
    if (summary) summary.style.display = 'block';
    if (meta) meta.innerHTML = `<p class="search-meta-text">正在执行 ${terms.length} 个扩展词检索，请稍候...</p>`;
    if (termsEl) termsEl.innerHTML = terms.map(term => `<span class="term-chip">${escapeHtml(term)}</span>`).join('');

    try {
        const responses = await Promise.all(terms.map(async term => ({
            term: term,
            data: await searchCurrentEpochKeyword(term, writerIdsParam)
        })));
        const merged = mergeSearchResponses(responses);
        const total = getTotalFileCount(merged);
        const okCount = responses.filter(item => item.data && item.data.success).length;
        const timeMs = responses
            .map(item => item.data && typeof item.data.search_time_ms === 'number' ? item.data.search_time_ms : 0)
            .reduce((sum, value) => sum + value, 0);
        const label = `${keyword}（扩展: ${terms.join(', ')}）`;

        if (meta) {
            meta.innerHTML = `<p class="search-meta-text">原词 "<strong>${escapeHtml(keyword)}</strong>" 扩展为 <strong>${terms.length}</strong> 个精确关键词；成功 <strong>${okCount}</strong> 个；合并后命中 <strong>${total}</strong> 封。</p>`;
        }
        displaySearchResults(label, merged, Number(timeMs.toFixed(2)), activeBatchEpoch);
        const collapse = getEl('fuzzy-search-collapse');
        if (collapse) collapse.open = true;
        showToast(`扩展检索完成: 合并后找到 ${total} 封匹配邮件`, 'success');
    } catch (error) {
        if (meta) {
            meta.innerHTML = `<div class="result-message error"><strong>扩展检索失败</strong><br>${escapeHtml(error.message || String(error))}</div>`;
        }
        showToast('扩展检索失败: ' + (error.message || String(error)), 'error');
    } finally {
        setSubmitLoading(event.target, false);
    }
}

function getWriterIdsParamForSearch() {
    const ids = getCurrentSelectedWriterIds();
    return ids.length > 0 ? ids : null;
}

async function setAuditBatchEpochForAnalysis(epoch) {
    const data = await apiFetchJson('/api/audit-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ epoch: epoch }),
    });
    if (!data || !data.success) {
        throw new Error((data && data.error) ? data.error : `切换到 Epoch ${epoch} 失败`);
    }
    return parseInt(data.active_epoch, 10) || epoch;
}

async function restoreAuditBatchAfterAnalysis(epoch) {
    try {
        const restoredEpoch = await setAuditBatchEpochForAnalysis(epoch);
        activeBatchEpoch = restoredEpoch;
        const badge = getEl('active-batch-badge');
        const input = getEl('batch-epoch-input');
        if (badge) badge.textContent = `当前批次 Epoch: ${activeBatchEpoch}`;
        if (input) input.value = String(activeBatchEpoch);
    } catch (_) {
        refreshAuditBatch();
    }
}

async function searchKeywordAtEpoch(keyword, epoch, writerIdsParam) {
    await setAuditBatchEpochForAnalysis(epoch);
    const data = await apiFetchJson('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            keyword: keyword,
            writer_ids: writerIdsParam
        })
    });
    if (!data || !data.success) {
        throw new Error((data && data.error) ? data.error : `Epoch ${epoch} 检索失败`);
    }
    return data;
}

function flattenResultMap(results) {
    const map = new Map();
    (results || []).forEach(item => {
        const writerId = parseInt(item && item.writer_id, 10);
        if (isNaN(writerId)) return;
        (item.file_ids || []).forEach(fileId => {
            const fid = parseInt(fileId, 10);
            if (!isNaN(fid)) {
                map.set(`${writerId}:${fid}`, { writerId: writerId, fileId: fid });
            }
        });
    });
    return map;
}

function groupFilesByWriter(files) {
    const grouped = {};
    (files || []).forEach(item => {
        if (!grouped[item.writerId]) grouped[item.writerId] = [];
        grouped[item.writerId].push(item.fileId);
    });
    return Object.keys(grouped)
        .map(writerId => ({
            writerId: parseInt(writerId, 10),
            fileIds: grouped[writerId].sort((a, b) => a - b)
        }))
        .sort((a, b) => b.fileIds.length - a.fileIds.length || a.writerId - b.writerId);
}

function getWriterScopeText() {
    const ids = getCurrentSelectedWriterIds();
    if (!ids.length) return '全部已授权写者';
    return ids.map(id => `员工 ${id + 1}`).join('、');
}

function setSubmitLoading(form, loading) {
    if (!form) return;
    const submitBtn = form.querySelector('button[type="submit"]');
    if (!submitBtn) return;
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoading = submitBtn.querySelector('.btn-loading');
    submitBtn.disabled = loading;
    if (btnText) btnText.style.display = loading ? 'none' : 'inline';
    if (btnLoading) btnLoading.style.display = loading ? 'inline' : 'none';
}

async function handleEpochDiff(event) {
    event.preventDefault();

    const keyword = (getEl('epoch-diff-keyword') && getEl('epoch-diff-keyword').value || '').trim();
    const oldEpoch = parseInt(getEl('epoch-diff-old') ? getEl('epoch-diff-old').value : '', 10);
    const newEpoch = parseInt(getEl('epoch-diff-new') ? getEl('epoch-diff-new').value : '', 10);
    const panel = getEl('epoch-diff-results');
    const meta = getEl('epoch-diff-meta');
    const content = getEl('epoch-diff-content');
    const form = event.target;

    if (!keyword) {
        showToast('请输入要对比的关键词', 'error');
        return;
    }
    if (isNaN(oldEpoch) || isNaN(newEpoch) || oldEpoch < 1 || newEpoch < 1) {
        showToast('请输入合法的 Epoch（>=1）', 'error');
        return;
    }
    if (newEpoch <= oldEpoch) {
        showToast('对比 Epoch 必须大于基准 Epoch，才能计算新增文件集合', 'error');
        return;
    }

    const originalEpoch = activeBatchEpoch;
    const writerIdsParam = getWriterIdsParamForSearch();
    setSubmitLoading(form, true);
    if (panel) panel.style.display = 'block';
    if (meta) {
        meta.innerHTML = `<p class="search-meta-text">正在对比 Epoch ${oldEpoch} 与 Epoch ${newEpoch} 的关键词 "<strong>${escapeHtml(keyword)}</strong>"...</p>`;
    }
    if (content) content.innerHTML = '<div class="empty-state" style="padding: 16px;">正在执行两次检索并计算差集...</div>';

    try {
        const oldData = await searchKeywordAtEpoch(keyword, oldEpoch, writerIdsParam);
        const newData = await searchKeywordAtEpoch(keyword, newEpoch, writerIdsParam);
        renderEpochDiffResults(keyword, oldEpoch, newEpoch, oldData.results || [], newData.results || [], oldData.search_time_ms, newData.search_time_ms);
        showToast('跨 Epoch 增量审计完成', 'success');
    } catch (error) {
        if (content) {
            content.innerHTML = `<div class="result-message error"><strong>增量审计失败</strong><br>${escapeHtml(error.message || String(error))}</div>`;
        }
        showToast('增量审计失败: ' + (error.message || String(error)), 'error');
    } finally {
        await restoreAuditBatchAfterAnalysis(originalEpoch);
        setSubmitLoading(form, false);
    }
}

function renderEpochDiffResults(keyword, oldEpoch, newEpoch, oldResults, newResults, oldTimeMs, newTimeMs) {
    const panel = getEl('epoch-diff-results');
    const meta = getEl('epoch-diff-meta');
    const content = getEl('epoch-diff-content');
    if (!panel || !meta || !content) return;

    const oldMap = flattenResultMap(oldResults);
    const newMap = flattenResultMap(newResults);
    const added = [];
    const removed = [];

    newMap.forEach((value, key) => {
        if (!oldMap.has(key)) added.push(value);
    });
    oldMap.forEach((value, key) => {
        if (!newMap.has(key)) removed.push(value);
    });

    const addedRows = groupFilesByWriter(added);
    const timeText = [
        typeof oldTimeMs === 'number' ? `Epoch ${oldEpoch}: ${oldTimeMs} ms` : null,
        typeof newTimeMs === 'number' ? `Epoch ${newEpoch}: ${newTimeMs} ms` : null,
    ].filter(Boolean).join('；');

    meta.innerHTML = `
        <p class="search-meta-text">
            关键词 "<strong>${escapeHtml(keyword)}</strong>" · ${escapeHtml(getWriterScopeText())} ·
            Epoch ${oldEpoch} 命中 <strong>${oldMap.size}</strong> 封，Epoch ${newEpoch} 命中 <strong>${newMap.size}</strong> 封，
            新增 <strong>${added.length}</strong> 封，减少 <strong>${removed.length}</strong> 封${timeText ? ` · ${timeText}` : ''}
        </p>
    `;

    if (addedRows.length === 0) {
        content.innerHTML = `
            <div class="empty-state" style="padding: 16px;">
                未发现 Epoch ${newEpoch} 相对 Epoch ${oldEpoch} 的新增命中文件。
            </div>
        `;
        panel.style.display = 'block';
        return;
    }

    content.innerHTML = addedRows.map(row => `
        <div class="epoch-diff-row">
            <div class="epoch-diff-row-head">
                <strong>员工 ${row.writerId}</strong>
                <span class="epoch-new-count">新增 ${row.fileIds.length} 封</span>
            </div>
            <div class="file-ids epoch-new-files">
                ${row.fileIds.map(fileId =>
                    `<span class="file-id-badge epoch-new-file" onclick="viewDocument(${row.writerId - 1}, ${fileId})" title="查看加密存储信息">${fileId}</span>`
                ).join('')}
            </div>
        </div>
    `).join('');
    panel.style.display = 'block';
}

async function handleEpochTrend(event) {
    event.preventDefault();

    const keyword = (getEl('epoch-trend-keyword') && getEl('epoch-trend-keyword').value || '').trim();
    const startEpoch = parseInt(getEl('epoch-trend-start') ? getEl('epoch-trend-start').value : '', 10);
    const endEpoch = parseInt(getEl('epoch-trend-end') ? getEl('epoch-trend-end').value : '', 10);
    const panel = getEl('epoch-trend-results');
    const meta = getEl('epoch-trend-meta');
    const table = getEl('epoch-trend-table');
    const form = event.target;

    if (!keyword) {
        showToast('请输入要追踪的关键词', 'error');
        return;
    }
    if (isNaN(startEpoch) || isNaN(endEpoch) || startEpoch < 1 || endEpoch < 1 || startEpoch > endEpoch) {
        showToast('请输入合法的 Epoch 范围', 'error');
        return;
    }
    if (endEpoch - startEpoch + 1 > 30) {
        showToast('一次最多分析 30 个 Epoch', 'error');
        return;
    }

    const originalEpoch = activeBatchEpoch;
    const writerIdsParam = getWriterIdsParamForSearch();
    const rows = [];
    setSubmitLoading(form, true);
    if (panel) panel.style.display = 'block';
    if (table) table.innerHTML = '<div class="empty-state" style="padding: 16px;">正在逐批次检索，请稍候...</div>';

    try {
        for (let epoch = startEpoch; epoch <= endEpoch; epoch += 1) {
            if (meta) {
                meta.innerHTML = `<p class="search-meta-text">正在分析关键词 "<strong>${escapeHtml(keyword)}</strong>"：Epoch ${epoch} / ${endEpoch}</p>`;
            }
            const data = await searchKeywordAtEpoch(keyword, epoch, writerIdsParam);
            rows.push({
                epoch: epoch,
                total: getTotalFileCount(data.results || []),
                writersHit: (data.results || []).filter(r => (r.file_ids || []).length > 0).length,
                timeMs: data.search_time_ms
            });
        }
        renderEpochTrendResults(keyword, startEpoch, endEpoch, rows);
        showToast('Epoch 趋势追踪完成', 'success');
    } catch (error) {
        if (table) {
            table.innerHTML = `<div class="result-message error"><strong>趋势追踪失败</strong><br>${escapeHtml(error.message || String(error))}</div>`;
        }
        showToast('趋势追踪失败: ' + (error.message || String(error)), 'error');
    } finally {
        await restoreAuditBatchAfterAnalysis(originalEpoch);
        setSubmitLoading(form, false);
    }
}

function renderEpochTrendResults(keyword, startEpoch, endEpoch, rows) {
    const meta = getEl('epoch-trend-meta');
    const table = getEl('epoch-trend-table');
    const panel = getEl('epoch-trend-results');
    if (!meta || !table || !panel) return;

    const maxRow = rows.reduce((best, row) => row.total > best.total ? row : best, { epoch: '-', total: 0 });
    meta.innerHTML = `
        <p class="search-meta-text">
            关键词 "<strong>${escapeHtml(keyword)}</strong>" · ${escapeHtml(getWriterScopeText())} ·
            Epoch ${startEpoch} 至 ${endEpoch} · 峰值 Epoch <strong>${maxRow.epoch}</strong>，命中 <strong>${maxRow.total}</strong> 封
        </p>
    `;

    renderEpochTrendChart(rows, keyword);
    table.innerHTML = rows.map((row, idx) => {
        const prev = idx > 0 ? rows[idx - 1].total : row.total;
        const delta = row.total - prev;
        const deltaText = idx === 0 ? '-' : (delta >= 0 ? `+${delta}` : `${delta}`);
        const deltaClass = delta > 0 ? 'up' : (delta < 0 ? 'down' : 'flat');
        return `
            <div class="epoch-trend-row">
                <div class="epoch-trend-cell strong">Epoch ${row.epoch}</div>
                <div class="epoch-trend-cell">命中 ${row.total} 封</div>
                <div class="epoch-trend-cell">覆盖 ${row.writersHit} 位写者</div>
                <div class="epoch-trend-cell epoch-delta ${deltaClass}">较前批次 ${deltaText}</div>
            </div>
        `;
    }).join('');
    panel.style.display = 'block';
}

function renderEpochTrendChart(rows, keyword) {
    const canvas = getEl('epoch-trend-chart');
    if (!canvas) return;

    if (typeof Chart !== 'undefined') {
        if (epochTrendChartInstance && typeof epochTrendChartInstance.destroy === 'function') {
            epochTrendChartInstance.destroy();
        }
        epochTrendChartInstance = new Chart(canvas, {
            type: 'line',
            data: {
                labels: rows.map(row => `Epoch ${row.epoch}`),
                datasets: [{
                    label: keyword,
                    data: rows.map(row => row.total),
                    borderColor: '#2563eb',
                    backgroundColor: 'rgba(37, 99, 235, 0.12)',
                    tension: 0.25,
                    fill: true,
                    pointRadius: 4,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: { beginAtZero: true, ticks: { precision: 0 } }
                }
            }
        });
        return;
    }

    drawEpochTrendCanvas(canvas, rows);
}

function drawEpochTrendCanvas(canvas, rows) {
    const parentWidth = canvas.parentElement ? canvas.parentElement.clientWidth : 800;
    const width = Math.max(parentWidth, 320);
    const height = 260;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const pad = { left: 46, right: 18, top: 18, bottom: 36 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const maxValue = Math.max(1, ...rows.map(row => row.total));
    const stepX = rows.length > 1 ? plotW / (rows.length - 1) : 0;

    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    ctx.font = '12px sans-serif';
    ctx.fillStyle = '#64748b';
    for (let i = 0; i <= 4; i += 1) {
        const y = pad.top + plotH - (plotH * i / 4);
        const value = Math.round(maxValue * i / 4);
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(width - pad.right, y);
        ctx.stroke();
        ctx.fillText(String(value), 8, y + 4);
    }

    const points = rows.map((row, idx) => ({
        x: pad.left + (rows.length > 1 ? stepX * idx : plotW / 2),
        y: pad.top + plotH - (row.total / maxValue) * plotH,
        row: row
    }));

    ctx.strokeStyle = '#2563eb';
    ctx.lineWidth = 3;
    ctx.beginPath();
    points.forEach((point, idx) => {
        if (idx === 0) ctx.moveTo(point.x, point.y);
        else ctx.lineTo(point.x, point.y);
    });
    ctx.stroke();

    points.forEach(point => {
        ctx.fillStyle = '#ffffff';
        ctx.strokeStyle = '#2563eb';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(point.x, point.y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = '#334155';
        ctx.fillText(String(point.row.total), point.x - 6, point.y - 10);
        ctx.fillStyle = '#64748b';
        ctx.fillText(String(point.row.epoch), point.x - 8, height - 12);
    });
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
                const fileHits = [];
                (data.results || []).forEach(item => {
                    const count = item && item.file_ids ? item.file_ids.length : 0;
                    const writerId = parseInt(item && item.writer_id, 10);
                    if (count > 0) writerCounts[item.writer_id] = count;
                    (item.file_ids || []).forEach(fileId => {
                        if (fileHits.length >= GRAPH_FILE_HIT_LIMIT_PER_KEYWORD) return;
                        const fid = parseInt(fileId, 10);
                        if (!isNaN(writerId) && !isNaN(fid)) {
                            fileHits.push({ keyword: keyword, writerId: writerId, fileId: fid });
                        }
                    });
                });
                return {
                    keyword: keyword,
                    total: total,
                    writersHit: (data.results || []).filter(r => (r.file_ids || []).length > 0).length,
                    timeMs: data.search_time_ms,
                    writerCounts: writerCounts,
                    fileHits: fileHits,
                    ok: true
                };
            }
            return {
                keyword: keyword,
                total: 0,
                writersHit: 0,
                timeMs: null,
                writerCounts: {},
                fileHits: [],
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
        const graphRows = sorted.filter(row => row.ok && row.total > 0);
        currentTopNRows = topList;
        currentGraphRows = graphRows;
        renderTopNResults(keywords.length, topN, topList);
        renderRiskProfileFromTopN(topList);
        await renderCollusionGraph(graphRows);
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

function parseBooleanKeywords(rawText) {
    const text = String(rawText || '').trim();
    if (!text) return [];

    const hasStrongDelimiter = /[,，;；\n\r]/.test(text);
    const rawParts = hasStrongDelimiter
        ? text.split(/[,，;；\n\r]+/)
        : text.split(/\s+/);
    const uniq = [];
    const seen = new Set();

    rawParts
        .map(s => s.trim().replace(/^["'“”‘’]+|["'“”‘’]+$/g, '').toLowerCase())
        .filter(Boolean)
        .forEach(keyword => {
            if (!seen.has(keyword)) {
                seen.add(keyword);
                uniq.push(keyword);
            }
        });

    return uniq.slice(0, 20);
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
    meta.innerHTML = `<p class="search-meta-text">已分析 <strong>${totalKeywords}</strong> 个关键词，展示 Top <strong>${Math.min(topN, rows.length)}</strong>；成功 <strong>${okCount}</strong> 个。</p>`;

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
    const progressEl = getEl('topn-progress');
    if (panel) panel.style.display = 'none';
    if (progressEl) progressEl.textContent = '';
    currentTopNRows = [];
    currentGraphRows = [];
    renderRiskProfileFromTopN([]);
    renderCollusionGraph([]);
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

function collectGraphEdgesFromTopN(rows) {
    const edgeMap = new Map();
    (rows || []).forEach((row, rankIdx) => {
        const hits = Array.isArray(row.fileHits) ? row.fileHits : [];
        hits.forEach(hit => {
            const writerId = parseInt(hit.writerId, 10);
            const fileId = parseInt(hit.fileId, 10);
            if (isNaN(writerId) || isNaN(fileId)) return;
            const key = `${writerId}:${fileId}`;
            if (!edgeMap.has(key)) {
                edgeMap.set(key, {
                    fileKey: key,
                    writerId: writerId,
                    fileId: fileId,
                    keywords: new Set(),
                    rankScore: 0,
                });
            }
            const edge = edgeMap.get(key);
            edge.keywords.add(hit.keyword || row.keyword);
            edge.rankScore += Math.max(1, (rows.length - rankIdx));
        });
    });

    return Array.from(edgeMap.values())
        .map(edge => ({
            fileKey: edge.fileKey,
            writerId: edge.writerId,
            fileId: edge.fileId,
            keywords: Array.from(edge.keywords).sort(),
            rankScore: edge.rankScore,
        }))
        .sort((a, b) => b.rankScore - a.rankScore || a.writerId - b.writerId || a.fileId - b.fileId);
}

async function fetchGraphEmailMetadata(edges) {
    const unique = [];
    const seen = new Set();
    (edges || []).forEach(edge => {
        const writerId = parseInt(edge.writerId, 10);
        const fileId = parseInt(edge.fileId, 10);
        if (isNaN(writerId) || isNaN(fileId)) return;
        const key = `${writerId}:${fileId}`;
        if (seen.has(key)) return;
        seen.add(key);
        unique.push({ writer_id: writerId - 1, file_id: fileId });
    });
    if (!unique.length) return new Map();
    try {
        const data = await apiFetchJson('/api/email-metadata/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files: unique.slice(0, 300) }),
        });
        if (!data || !data.success) return new Map();
        const map = new Map();
        (data.metadata || []).forEach(item => {
            const writerId = parseInt(item.writer_id, 10);
            const fileId = parseInt(item.file_id, 10);
            if (!isNaN(writerId) && !isNaN(fileId)) {
                map.set(`${writerId}:${fileId}`, item);
            }
        });
        return map;
    } catch (_) {
        return new Map();
    }
}

function summarizeGraphExchanges(edges) {
    const pairMap = new Map();
    (edges || []).forEach(edge => {
        const meta = edge.metadata || {};
        const owner = parseInt(edge.writerId, 10);
        const sender = meta.from_writer_id == null ? null : parseInt(meta.from_writer_id, 10);
        const recipients = Array.isArray(meta.recipient_writer_ids)
            ? meta.recipient_writer_ids.map(x => parseInt(x, 10)).filter(x => !isNaN(x))
            : [];
        const pairs = [];

        if (sender && recipients.length) {
            recipients.forEach(recipient => {
                if (recipient !== sender) pairs.push([sender, recipient]);
            });
        } else if (sender && sender !== owner) {
            pairs.push([sender, owner]);
        } else if (sender && sender === owner && recipients.length) {
            recipients.forEach(recipient => {
                if (recipient !== owner) pairs.push([owner, recipient]);
            });
        }

        pairs.forEach(([fromWriter, toWriter]) => {
            const key = `${fromWriter}->${toWriter}`;
            if (!pairMap.has(key)) {
                pairMap.set(key, {
                    fromWriter: fromWriter,
                    toWriter: toWriter,
                    count: 0,
                    keywords: new Set(),
                    files: [],
                    subjects: new Set(),
                });
            }
            const row = pairMap.get(key);
            row.count += 1;
            (edge.keywords || []).forEach(keyword => row.keywords.add(keyword));
            row.files.push(`员工 ${edge.writerId}/文件 ${edge.fileId}`);
            if (meta.subject) row.subjects.add(meta.subject);
        });
    });

    return Array.from(pairMap.values())
        .map(row => ({
            fromWriter: row.fromWriter,
            toWriter: row.toWriter,
            count: row.count,
            keywords: Array.from(row.keywords).sort(),
            files: row.files.slice(0, 8),
            subjects: Array.from(row.subjects).slice(0, 3),
        }))
        .sort((a, b) => b.count - a.count || a.fromWriter - b.fromWriter || a.toWriter - b.toWriter);
}

function renderExchangeSummary(edges) {
    const container = getEl('collusion-exchange-summary');
    if (!container) return;
    const rows = summarizeGraphExchanges(edges).slice(0, 12);
    if (!rows.length) {
        container.innerHTML = `
            <div class="exchange-empty">
                未能从当前命中文件的邮件头中解析出内部员工之间的发送/接收关系；图谱仍展示写者与高危文件的命中关系。
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="exchange-title">疑似员工通信线索（基于命中文件邮件头）</div>
        <div class="exchange-table">
            ${rows.map(row => `
                <div class="exchange-row">
                    <div class="exchange-pair">员工 ${row.fromWriter} -> 员工 ${row.toWriter}</div>
                    <div class="exchange-count">${row.count} 封高危命中邮件</div>
                    <div class="exchange-keywords">${row.keywords.slice(0, 8).map(k => `<span class="term-chip">${escapeHtml(k)}</span>`).join('')}</div>
                    <div class="exchange-files">${escapeHtml(row.files.join('，'))}</div>
                </div>
            `).join('')}
        </div>
    `;
}

async function renderCollusionGraph(rows) {
    const panel = getEl('collusion-graph-panel');
    const meta = getEl('collusion-graph-meta');
    const legend = getEl('collusion-graph-legend');
    const exchangeSummary = getEl('collusion-exchange-summary');
    const collapse = getEl('collusion-graph-collapse');
    if (!panel || !meta) return;

    const sourceRows = Array.isArray(rows) ? rows : (currentGraphRows.length ? currentGraphRows : currentTopNRows);
    const allEdges = collectGraphEdgesFromTopN(sourceRows);
    if (!allEdges.length) {
        panel.innerHTML = '<div class="empty-state" style="padding: 16px;">暂无图谱数据。请先执行一次多关键词 Top-N 或审计词包扫描。</div>';
        meta.textContent = '先执行一次多关键词 Top-N 或一键扫描词包后生成图谱。';
        if (exchangeSummary) exchangeSummary.innerHTML = '';
        if (legend) legend.style.display = 'none';
        return;
    }

    meta.textContent = '正在读取命中文件邮件头并生成关联图谱...';
    const metadataMap = await fetchGraphEmailMetadata(allEdges);
    allEdges.forEach(edge => {
        edge.metadata = metadataMap.get(edge.fileKey) || null;
    });

    const fileScore = {};
    const fileInfo = {};
    allEdges.forEach(edge => {
        fileScore[edge.fileKey] = (fileScore[edge.fileKey] || 0) + edge.rankScore;
        fileInfo[edge.fileKey] = {
            fileKey: edge.fileKey,
            writerId: edge.writerId,
            fileId: edge.fileId,
        };
    });
    const allFileRows = Object.keys(fileScore)
        .map(fileKey => ({ ...fileInfo[fileKey], score: fileScore[fileKey] }))
        .sort((a, b) => b.score - a.score || a.writerId - b.writerId || a.fileId - b.fileId);
    const selectedFileKeys = [];
    const selectedFileKeySet = new Set();
    const byWriter = new Map();
    allFileRows.forEach(item => {
        if (!byWriter.has(item.writerId)) byWriter.set(item.writerId, []);
        byWriter.get(item.writerId).push(item);
    });
    Array.from(byWriter.keys()).sort((a, b) => a - b).forEach(writerId => {
        byWriter.get(writerId).slice(0, 4).forEach(item => {
            if (!selectedFileKeySet.has(item.fileKey)) {
                selectedFileKeySet.add(item.fileKey);
                selectedFileKeys.push(item.fileKey);
            }
        });
    });
    allFileRows.forEach(item => {
        if (selectedFileKeys.length >= 42) return;
        if (!selectedFileKeySet.has(item.fileKey)) {
            selectedFileKeySet.add(item.fileKey);
            selectedFileKeys.push(item.fileKey);
        }
    });
    const selectedFiles = selectedFileKeys.slice(0, 42);
    const selectedFileSet = new Set(selectedFiles);
    const edges = allEdges
        .filter(edge => selectedFileSet.has(edge.fileKey))
        .slice(0, 90);

    const writers = Array.from(new Set(edges.map(edge => edge.writerId))).sort((a, b) => a - b);
    const files = Array.from(new Map(edges.map(edge => [edge.fileKey, {
        fileKey: edge.fileKey,
        writerId: edge.writerId,
        fileId: edge.fileId,
        score: fileScore[edge.fileKey] || 0,
        keywords: edge.keywords,
        metadata: edge.metadata,
    }])).values())
        .sort((a, b) => b.score - a.score || a.writerId - b.writerId || a.fileId - b.fileId);
    const height = Math.max(380, Math.min(1320, Math.max(writers.length, files.length) * 42 + 90));
    const writerY = {};
    const fileY = {};
    writers.forEach((writerId, idx) => {
        writerY[writerId] = 60 + idx * ((height - 120) / Math.max(1, writers.length - 1));
    });
    files.forEach((file, idx) => {
        fileY[file.fileKey] = 60 + idx * ((height - 120) / Math.max(1, files.length - 1));
    });

    const svgEdges = edges.map(edge => {
        const metaInfo = edge.metadata
            ? ` | ${edge.metadata.from || 'unknown'} -> ${(edge.metadata.to || []).slice(0, 3).join(', ')} | ${edge.metadata.subject || ''}`
            : '';
        const title = `员工 ${edge.writerId} -> 员工 ${edge.writerId} / 文件 ${edge.fileId}: ${edge.keywords.join(', ')}${metaInfo}`;
        return `
            <line class="graph-edge-line" x1="180" y1="${writerY[edge.writerId].toFixed(2)}" x2="790" y2="${fileY[edge.fileKey].toFixed(2)}">
                <title>${escapeHtml(title)}</title>
            </line>
        `;
    }).join('');

    const exchangeRowsForSvg = summarizeGraphExchanges(edges)
        .filter(row => writerY[row.fromWriter] != null && writerY[row.toWriter] != null)
        .slice(0, 20);
    const svgExchangeEdges = exchangeRowsForSvg.map((row, idx) => {
        const y1 = writerY[row.fromWriter];
        const y2 = writerY[row.toWriter];
        const offset = 34 + (idx % 4) * 16;
        const title = `员工 ${row.fromWriter} -> 员工 ${row.toWriter}: ${row.count} 封命中邮件；关键词 ${row.keywords.join(', ')}`;
        return `
            <path class="graph-exchange-line" d="M 122 ${y1.toFixed(2)} C ${offset} ${y1.toFixed(2)}, ${offset} ${y2.toFixed(2)}, 122 ${y2.toFixed(2)}">
                <title>${escapeHtml(title)}</title>
            </path>
        `;
    }).join('');

    const writerNodes = writers.map(writerId => `
        <g class="graph-node writer-node" transform="translate(145 ${writerY[writerId].toFixed(2)})">
            <circle r="17"></circle>
            <text x="-54" y="5">员工 ${writerId}</text>
        </g>
    `).join('');

    const fileNodes = files.map(file => {
        const keywords = Array.isArray(file.keywords) ? file.keywords : [];
        const metaInfo = file.metadata
            ? `${file.metadata.from || 'unknown'} -> ${(file.metadata.to || []).slice(0, 3).join(', ')}`
            : '未解析到邮件头';
        const title = `员工 ${file.writerId} / 文件 ${file.fileId}: ${keywords.join(', ')} | ${metaInfo}`;
        return `
            <g class="graph-node file-node" transform="translate(825 ${fileY[file.fileKey].toFixed(2)})">
                <title>${escapeHtml(title)}</title>
                <rect x="-22" y="-13" width="44" height="26" rx="7"></rect>
                <text x="32" y="5">员工 ${file.writerId} / 文件 ${file.fileId} · ${keywords.length} 词</text>
            </g>
        `;
    }).join('');

    panel.innerHTML = `
        <svg class="collusion-graph-svg" viewBox="0 0 1000 ${height}" role="img" aria-label="跨写者关联图谱">
            <text class="graph-axis-label" x="95" y="26">写者</text>
            <text class="graph-axis-label" x="760" y="26">高危文件（写者内编号）</text>
            ${svgExchangeEdges}
            ${svgEdges}
            ${writerNodes}
            ${fileNodes}
        </svg>
    `;
    const truncatedText = allEdges.length > edges.length ? `，已抽样展示 ${edges.length}/${allEdges.length} 条边` : '';
    const narrowHint = writers.length <= 2
        ? ' 当前图谱只出现少量写者，通常表示当前写者筛选范围较窄，或本次词包实际只在这些写者邮箱中命中。'
        : '';
    meta.textContent = `图谱基于本次扫描中 ${sourceRows.length} 个有命中关键词生成，包含 ${writers.length} 位写者、${files.length} 个高危文件节点、${edges.length} 条命中关系${truncatedText}。文件节点使用“员工/文件ID”；下方通信线索来自命中文件邮件头。${narrowHint}`;
    renderExchangeSummary(edges);
    if (legend) legend.style.display = 'flex';
    if (collapse && allEdges.length > 0) collapse.open = true;
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

function setCaseApplyHint(message) {
    const hint = getEl('case-apply-hint');
    if (!hint) return;
    hint.textContent = message || '';
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
            rows: (currentTopNRows || []).map(r => ({
                keyword: r.keyword,
                total: r.total,
                writersHit: r.writersHit,
                writerCounts: r.writerCounts || {},
                fileHits: Array.isArray(r.fileHits) ? r.fileHits.slice(0, GRAPH_FILE_HIT_LIMIT_PER_KEYWORD) : []
            }))
        },
        risk_profile: (currentRiskRows || []).map(r => ({ writerId: r.writerId, score: r.score, hits: r.hits }))
    };

    const cases = getStoredCases();
    cases.unshift(newCase);
    setStoredCases(cases.slice(0, 50));
    renderCaseList();
    setCaseApplyHint(`已保存任务单 "${caseName}"。`);
    showToast('任务单已保存', 'success');
}

function restoreTopNCaseSnapshot(target) {
    const topn = target && target.topn ? target.topn : {};
    const rows = Array.isArray(topn.rows)
        ? topn.rows.map(row => ({
            keyword: row && row.keyword ? String(row.keyword) : '',
            total: parseInt(row && row.total, 10) || 0,
            writersHit: parseInt(row && row.writersHit, 10) || 0,
            writerCounts: row && row.writerCounts && typeof row.writerCounts === 'object' ? row.writerCounts : {},
            fileHits: Array.isArray(row && row.fileHits) ? row.fileHits : [],
            ok: true
        })).filter(row => row.keyword)
        : [];

    if (!rows.length) {
        hideTopNResults();
        return false;
    }

    currentTopNRows = rows;
    currentGraphRows = rows;
    const totalKeywords = parseBatchKeywords(topn.keywords_text || '').length || rows.length;
    let topNLimit = parseInt(topn.limit, 10);
    if (isNaN(topNLimit) || topNLimit < 1) topNLimit = rows.length;
    renderTopNResults(totalKeywords, topNLimit, rows);

    const progressEl = getEl('topn-progress');
    if (progressEl) {
        progressEl.textContent = '已恢复任务单中的 Top-N 快照；如需当前最新结果，请重新执行分析。';
    }
    return true;
}

function renderRiskProfileSnapshot(rows) {
    const content = getEl('risk-profile-content');
    const collapse = getEl('risk-profile-collapse');
    if (!content) return false;

    const normalizedRows = Array.isArray(rows)
        ? rows.map(row => ({
            writerId: parseInt(row && row.writerId, 10) || 0,
            score: parseInt(row && row.score, 10) || 0,
            hits: parseInt(row && row.hits, 10) || 0,
        })).filter(row => row.writerId > 0)
        : [];

    currentRiskRows = normalizedRows;

    if (normalizedRows.length === 0) {
        content.innerHTML = '<div class="empty-state" style="padding: 16px;">当前任务单没有可恢复的风险画像快照。</div>';
        return false;
    }

    content.innerHTML = normalizedRows.map((row, idx) => `
        <div class="risk-row">
            <div class="risk-writer">#${idx + 1} 员工 ${row.writerId}</div>
            <div class="risk-metric">风险分值 ${row.score}</div>
            <div class="risk-metric">累计命中 ${row.hits} 封</div>
        </div>
    `).join('');

    if (collapse) collapse.open = true;
    return true;
}

function applyCase(caseId) {
    const cases = getStoredCases();
    const target = cases.find(c => c.id === caseId);
    if (!target) {
        showToast('任务单不存在', 'error');
        return;
    }
    const caseEpoch = parseInt(target.epoch || 1, 10);
    const crossEpoch = caseEpoch !== activeBatchEpoch;
    const keywordEl = getEl('keyword');
    const topnKeywordsEl = getEl('topn-keywords');
    const topnLimitEl = getEl('topn-limit');
    const caseNameInput = getEl('case-name');

    if (caseNameInput) caseNameInput.value = target.name || '';
    if (keywordEl) keywordEl.value = target.search && target.search.keyword ? target.search.keyword : '';
    if (topnKeywordsEl) topnKeywordsEl.value = target.topn && target.topn.keywords_text ? target.topn.keywords_text : '';
    if (topnLimitEl) topnLimitEl.value = (target.topn && target.topn.limit) ? target.topn.limit : 10;
    updateTopNKeywordCount();

    const writerSelect = getEl('writer-select');
    if (writerSelect && target.search && Array.isArray(target.search.selected_writer_ids)) {
        const selectedSet = new Set(target.search.selected_writer_ids);
        Array.from(writerSelect.options).forEach(option => {
            option.selected = selectedSet.has(parseInt(option.value, 10));
        });
    }
    updateWriterSelectionCount();

    hideSearchResults();

    const caseCollapse = getEl('case-collapse');
    if (caseCollapse) caseCollapse.open = true;

    const topnCollapse = getEl('topn-collapse');
    if (topnCollapse && topnKeywordsEl && topnKeywordsEl.value.trim()) topnCollapse.open = true;

    let restoredSnapshot = false;
    if (crossEpoch) {
        hideTopNResults();
        setCaseApplyHint(`任务单 "${target.name}" 来自 Epoch ${caseEpoch}，当前批次是 Epoch ${activeBatchEpoch}。已恢复检索条件，但不会恢复旧批次分析结果，请在当前批次重新检索与分析。`);
        showToast(`任务单来自 Epoch ${caseEpoch}，当前是 Epoch ${activeBatchEpoch}。仅恢复条件，请重新检索。`, 'info');
    } else {
        restoredSnapshot = restoreTopNCaseSnapshot(target);
        if (!restoredSnapshot) {
            hideTopNResults();
        }
        renderRiskProfileSnapshot(target.risk_profile || []);
        renderCollusionGraph(currentTopNRows);
        setCaseApplyHint(
            restoredSnapshot
                ? `已应用任务单 "${target.name}"，并恢复同批次分析快照。`
                : `已应用任务单 "${target.name}"，并恢复检索条件。`
        );
        showToast(
            restoredSnapshot
                ? `已应用任务单: ${target.name}（已恢复快照）`
                : `已应用任务单: ${target.name}`,
            'success'
        );
    }

    if (keywordEl) {
        keywordEl.focus();
        keywordEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
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
        setCaseApplyHint('');
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

function getSearchSnapshots() {
    try {
        const raw = localStorage.getItem(SEARCH_SNAPSHOT_STORAGE_KEY);
        if (!raw) return {};
        const data = JSON.parse(raw);
        return data && typeof data === 'object' ? data : {};
    } catch (_) {
        return {};
    }
}

function setSearchSnapshots(snapshots) {
    try {
        localStorage.setItem(SEARCH_SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshots || {}));
    } catch (_) {}
}

function clearSearchSnapshots() {
    try {
        localStorage.removeItem(SEARCH_SNAPSHOT_STORAGE_KEY);
    } catch (_) {}
    currentSearchDiffInfo = null;
    const hint = getEl('batch-switch-hint');
    if (hint) {
        hint.textContent = '已清空检索轮次基线；下一次同条件检索会作为新的对比基线。';
    }
    showToast('已清空检索轮次基线', 'success');
}

function getCurrentWriterScopeKey() {
    const ids = getCurrentSelectedWriterIds();
    if (!ids.length) return 'all-authorized';
    return ids.slice().sort((a, b) => a - b).join(',');
}

function buildSearchSnapshotKey(queryLabel) {
    return [
        String(queryLabel || '').trim().toLowerCase(),
        getCurrentWriterScopeKey(),
    ].join('|writers=');
}

function flattenSearchResultKeys(results) {
    const keys = [];
    (results || []).forEach(result => {
        const writerId = parseInt(result && result.writer_id, 10);
        if (isNaN(writerId)) return;
        (result.file_ids || []).forEach(fileId => {
            const fid = parseInt(fileId, 10);
            if (!isNaN(fid)) keys.push(`${writerId}:${fid}`);
        });
    });
    return Array.from(new Set(keys)).sort();
}

function updateSearchSnapshot(queryLabel, results) {
    const key = buildSearchSnapshotKey(queryLabel);
    const snapshots = getSearchSnapshots();
    const previous = snapshots[key] || null;
    const currentKeys = flattenSearchResultKeys(results);
    const previousKeys = Array.isArray(previous && previous.fileKeys) ? previous.fileKeys : [];
    const previousSet = new Set(previousKeys);
    const newKeys = previous
        ? currentKeys.filter(item => !previousSet.has(item))
        : [];
    const searchRound = previous ? ((parseInt(previous.searchRound, 10) || 1) + 1) : 1;

    snapshots[key] = {
        query: queryLabel,
        writerScope: getCurrentWriterScopeKey(),
        fileKeys: currentKeys,
        searchRound: searchRound,
        savedAt: new Date().toISOString(),
    };

    const entries = Object.entries(snapshots)
        .sort((a, b) => String(b[1].savedAt || '').localeCompare(String(a[1].savedAt || '')))
        .slice(0, 120);
    setSearchSnapshots(Object.fromEntries(entries));

    return {
        key: key,
        hasPrevious: !!previous,
        searchRound: searchRound,
        previousTotal: previousKeys.length,
        currentTotal: currentKeys.length,
        newFileKeys: new Set(newKeys),
        newCount: newKeys.length,
    };
}

function getSearchTimeLabel(keyword) {
    return /^(AND|OR)\(/.test(String(keyword || ''))
        ? '前端联合检索总耗时'
        : '亚线性检索耗时';
}

function getSearchDiffText(diffInfo) {
    if (!diffInfo) return '';
    if (!diffInfo.hasPrevious) {
        return ` · 第 <strong>${diffInfo.searchRound}</strong> 次同条件检索，已保存为后续对比基线`;
    }
    return ` · 第 <strong>${diffInfo.searchRound}</strong> 次同条件检索，较上次新增 <strong>${diffInfo.newCount}</strong> 封`;
}

function displaySearchResults(keyword, results, searchTimeMs, epochUsed, diffInfo = null) {
    const resultsContainer = getEl('search-results');
    const resultsContent = getEl('results-content');
    const searchMeta = getEl('search-meta');

    if (!resultsContainer || !resultsContent || !searchMeta) return;

    currentSearchKeyword = keyword;
    currentSearchResults = Array.isArray(results) ? results : [];
    currentSearchTimeMs = searchTimeMs;
    currentSearchDiffInfo = diffInfo || updateSearchSnapshot(keyword, currentSearchResults);
    currentWriterFilter = null;
    expandedWriterIds = new Set();
    if (epochUsed != null) {
        activeBatchEpoch = parseInt(epochUsed, 10) || activeBatchEpoch;
        const badge = getEl('active-batch-badge');
        if (badge) badge.textContent = '检索轮次: 自动记录';
    }

    if (typeof searchTimeMs === 'number') {
        const timeLabel = getSearchTimeLabel(keyword);
        searchMeta.innerHTML = `<p class="search-meta-text">关键字 "<strong>${escapeHtml(keyword)}</strong>" · ${timeLabel} <strong>${searchTimeMs}</strong> ms${getSearchDiffText(currentSearchDiffInfo)}</p>`;
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
        const timeLabel = getSearchTimeLabel(currentSearchKeyword);
        searchMeta.innerHTML = `<p class="search-meta-text">关键字 "<strong>${escapeHtml(currentSearchKeyword)}</strong>" · ${timeLabel} <strong>${currentSearchTimeMs}</strong> ms${getSearchDiffText(currentSearchDiffInfo)}${filterText}</p>`;
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
            const newFileKeys = currentSearchDiffInfo && currentSearchDiffInfo.newFileKeys
                ? currentSearchDiffInfo.newFileKeys
                : new Set();
            html += `
                <div class="result-item">
                    <h4>员工 ${writerId}</h4>
                    <p>匹配 ${result.file_ids.length} 封邮件，读者端仅展示文件ID与加密存储信息:</p>
                    <div class="file-ids">
                        ${visibleIds.map(id =>
                            `<span class="file-id-badge ${newFileKeys.has(`${writerId}:${id}`) ? 'search-new-file' : ''}" onclick="viewDocument(${writerId - 1}, ${id})" title="${newFileKeys.has(`${writerId}:${id}`) ? '本次新增命中。' : ''}查看加密存储信息">${id}</span>`
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
                <h3>加密存储信息 - 员工 ${writerId + 1}, 文件ID: ${fileId}</h3>
                <button class="document-modal-close" onclick="closeDocumentModal()">&times;</button>
            </div>
            <div class="document-modal-body">
                <div id="document-loading" style="text-align: center; padding: 20px;">
                    <div class="loading"></div>
                    <p>正在获取加密存储信息...</p>
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
                encryptedPreview += '\n\n... (密文预览已截断，读者端不提供明文解密)';
            }
            contentDiv.innerHTML = `
                <div class="document-info">
                    <p><strong>状态:</strong> <span class="encrypted-badge">已加密</span></p>
                    <p><strong>访问边界:</strong> 读者端仅允许查看文件ID和密文状态，不提供明文解密。</p>
                    ${data.size != null ? `<p><strong>密文大小:</strong> ${data.size} 字节</p>` : ''}
                </div>
                <pre class="document-text encrypted-preview" style="max-height: 320px; overflow: auto; background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 6px; font-size: 13px;">${encryptedPreview}</pre>
            `;
        } else if (data.success && !data.encrypted) {
            const contentDiv = getEl('document-content');
            contentDiv.style.display = 'block';
            contentDiv.innerHTML = '<div class="result-message error"><strong>已阻止明文展示</strong><br>读者端不允许查看邮件原文。</div>';
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
    currentSearchDiffInfo = null;
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
                <div class="status-item" style="margin-top: 15px;"><strong>底层启动 Epoch:</strong> ${data.epoch != null ? data.epoch : '-'}（只读）</div>
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
