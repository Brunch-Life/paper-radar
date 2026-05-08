// =====================================================================
// Paper Radar SPA — talks to FastAPI on the same origin (loopback).
// All state in memory. No build step. ~250 lines.
// =====================================================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const escape = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
}[c]));

// ---- API client -----------------------------------------------------
const api = {
  async dates() {
    const r = await fetch('/api/dates?limit=14');
    if (!r.ok) throw new Error(`dates ${r.status}`);
    return r.json();
  },
  async papers(date) {
    const url = date ? `/api/papers?date=${encodeURIComponent(date)}` : '/api/papers';
    const r = await fetch(url);
    if (!r.ok) throw new Error(`papers ${r.status}`);
    return r.json();
  },
  async paper(arxivId, date) {
    let url = `/api/paper?arxiv_id=${encodeURIComponent(arxivId)}`;
    if (date) url += `&date=${encodeURIComponent(date)}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`paper ${r.status}`);
    return r.json();
  },
  async search(q, limit = 30) {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
    if (!r.ok) throw new Error(`search ${r.status}`);
    return r.json();
  },
  async health() {
    const r = await fetch('/healthz');
    if (!r.ok) throw new Error(`healthz ${r.status}`);
    return r.json();
  },
};

// ---- State ----------------------------------------------------------
const state = {
  dates: [],
  annual: [],
  currentDate: null,
  currentPapers: [],   // papers shown in main list (date-filtered)
  searchMode: false,   // when true, papers come from /api/search
  paperCache: {},      // arxiv_id → full_md
  digestsDir: '',
};

// ---- Render helpers -------------------------------------------------
function setStatus(msg, isError = false) {
  const el = $('#status-bar');
  el.textContent = msg || '';
  el.classList.toggle('error', isError);
}

function renderDateSelector() {
  const sel = $('#date-select');
  sel.innerHTML = '';
  const addGroup = (label, items) => {
    if (!items.length) return;
    const og = document.createElement('optgroup');
    og.label = label;
    items.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d; opt.textContent = d;
      og.appendChild(opt);
    });
    sel.appendChild(og);
  };
  addGroup('Daily', state.dates);
  addGroup('Annual', state.annual);
  if (state.currentDate) sel.value = state.currentDate;
}

function renderPapers(papers, isSearch = false) {
  const main = $('#papers');
  main.innerHTML = '';
  const empty = $('#empty');
  if (!papers || papers.length === 0) {
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  papers.forEach((p, i) => {
    const rank = isSearch ? null : (p.rank || (i + 1));
    const aid = p.arxiv_id || '';
    const title = p.title || aid || '(无标题)';
    const tagline = p.tagline || '';

    const card = document.createElement('div');
    card.className = 'paper';
    card.dataset.aid = aid;
    if (isSearch && p.date) card.dataset.date = p.date;

    const rankHtml = rank
      ? `<span class="rank">#${rank}</span>`
      : `<span class="rank" style="background: var(--bg); color: var(--accent);">${escape(p.date || '')}</span>`;

    card.innerHTML = `
      <div class="paper-row">
        ${rankHtml}
        <div class="paper-text">
          <p class="paper-title">${escape(title)}</p>
          ${tagline ? `<p class="paper-tagline">${escape(tagline)}</p>` : ''}
          <p class="paper-meta">
            ${aid ? `<a href="https://arxiv.org/abs/${aid}" target="_blank" rel="noopener" onclick="event.stopPropagation()">arxiv:${aid}</a>` : ''}
          </p>
        </div>
        <span class="paper-arrow">›</span>
      </div>
    `;
    card.addEventListener('click', () => openPaper(aid, p.date || state.currentDate));
    main.appendChild(card);
  });
}

function renderMeta() {
  const meta = $('#meta');
  if (state.searchMode) {
    meta.textContent = `搜索结果 · ${state.currentPapers.length} hits`;
  } else if (state.currentDate) {
    meta.textContent = `${state.currentDate} · ${state.currentPapers.length} 篇 · ${state.digestsDir}`;
  } else {
    meta.textContent = state.digestsDir || '';
  }
}

// ---- Detail modal ---------------------------------------------------
async function openPaper(aid, date) {
  if (!aid) {
    setStatus('该论文没有 arxiv_id', true);
    return;
  }
  const dlg = $('#detail');
  $('#detail-title').textContent = `加载 ${aid}…`;
  $('#detail-body').innerHTML = '<span class="loading"></span>正在加载 6-section 全文…';
  $('#detail-arxiv').href = `https://arxiv.org/abs/${aid}`;
  $('#detail-ask').dataset.aid = aid;
  dlg.showModal();

  try {
    let md;
    if (state.paperCache[aid]) {
      md = state.paperCache[aid];
    } else {
      const res = await api.paper(aid, date);
      if (res.error) throw new Error(res.error);
      md = res.full_md || '';
      state.paperCache[aid] = md;
    }
    // First H1 line as title (strip leading '# ')
    const m = md.match(/^#\s+([^\n]+)/);
    $('#detail-title').textContent = m ? m[1].trim() : aid;
    $('#detail-body').innerHTML = marked.parse(md);
    $('#detail-body').scrollTop = 0;
  } catch (e) {
    $('#detail-body').innerHTML = `<p style="color:#ef4444">${escape(e.message)}</p>`;
  }
}

function copyAskPrompt(aid) {
  const prompt = `Use the paper-radar MCP \`get_paper\` tool to load ${aid}, then answer:\n\n[your question here]`;
  navigator.clipboard.writeText(prompt).then(() => {
    const btn = $('#detail-ask');
    const orig = btn.textContent;
    btn.textContent = '✅ 已复制 — 粘到 Claude Code 主对话';
    setTimeout(() => btn.textContent = orig, 2400);
  });
}

// ---- Theme switch ---------------------------------------------------
function toggleTheme() {
  const root = document.documentElement;
  const cur = root.classList.contains('dark') ? 'dark'
            : root.classList.contains('light') ? 'light'
            : 'system';
  const next = cur === 'system' ? 'light' : cur === 'light' ? 'dark' : 'system';
  root.classList.remove('light', 'dark', 'system');
  if (next !== 'system') root.classList.add(next);
  localStorage.setItem('theme', next);
}
function applyStoredTheme() {
  const saved = localStorage.getItem('theme') || 'system';
  document.documentElement.classList.remove('light', 'dark', 'system');
  if (saved !== 'system') document.documentElement.classList.add(saved);
}

// ---- Search ---------------------------------------------------------
let searchTimer = null;
function onSearch(e) {
  const q = e.target.value.trim();
  clearTimeout(searchTimer);
  if (!q) {
    state.searchMode = false;
    state.currentPapers = state.lastDatePapers || [];
    renderPapers(state.currentPapers, false);
    renderMeta();
    return;
  }
  // Debounce 250ms; do live search across ALL digests
  searchTimer = setTimeout(async () => {
    setStatus(`搜索 "${q}"…`);
    try {
      const res = await api.search(q, 50);
      state.searchMode = true;
      state.currentPapers = res.hits || [];
      renderPapers(state.currentPapers, true);
      renderMeta();
      setStatus('');
    } catch (err) {
      setStatus(`搜索失败: ${err.message}`, true);
    }
  }, 250);
}

// ---- Date change ----------------------------------------------------
async function loadDate(date) {
  state.searchMode = false;
  $('#search').value = '';
  setStatus(`加载 ${date}…`);
  try {
    const res = await api.papers(date);
    if (res.error) throw new Error(res.error);
    state.currentDate = res.date;
    state.currentPapers = res.papers || [];
    state.lastDatePapers = state.currentPapers;
    renderPapers(state.currentPapers, false);
    renderMeta();
    setStatus('');
  } catch (e) {
    setStatus(`加载失败: ${e.message}`, true);
    state.currentPapers = [];
    renderPapers([], false);
  }
}

// ---- Refresh --------------------------------------------------------
async function refresh() {
  state.paperCache = {};
  await init();
}

// ---- Bootstrap ------------------------------------------------------
async function init() {
  applyStoredTheme();
  setStatus('正在拉取 digest 列表…');
  try {
    const dRes = await api.dates();
    state.dates = dRes.dates || [];
    state.annual = dRes.annual_digests || [];
    state.digestsDir = dRes.digests_dir || '';
    if (state.dates.length === 0 && state.annual.length === 0) {
      setStatus(`没找到任何 digest（${state.digestsDir}）`, true);
      return;
    }
    state.currentDate = state.dates[0] || state.annual[0];
    renderDateSelector();
    await loadDate(state.currentDate);
  } catch (e) {
    setStatus(`无法连接服务器: ${e.message}（确认 paper-radar 服务在 7878 端口）`, true);
  }
}

// ---- Wire events ----------------------------------------------------
window.addEventListener('DOMContentLoaded', () => {
  $('#date-select').addEventListener('change', e => loadDate(e.target.value));
  $('#search').addEventListener('input', onSearch);
  $('#refresh').addEventListener('click', refresh);
  $('#theme-toggle').addEventListener('click', toggleTheme);
  $('#detail-close').addEventListener('click', () => $('#detail').close());
  $('#detail-ask').addEventListener('click', e => copyAskPrompt(e.target.dataset.aid));

  // Click backdrop to close
  $('#detail').addEventListener('click', e => {
    if (e.target === $('#detail')) $('#detail').close();
  });

  // Keyboard: '/' focuses search, Esc closes detail
  document.addEventListener('keydown', e => {
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      $('#search').focus();
    }
  });

  init();
});
