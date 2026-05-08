// =====================================================================
// Paper Radar SPA — sidebar navigation + waterfall main feed.
// All days rendered eagerly (typically <250 papers total). Sidebar
// click = smooth-scroll to anchor. Search = separate result section.
// =====================================================================

const $ = (sel, root = document) => root.querySelector(sel);
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
    const r = await fetch(`/api/papers?date=${encodeURIComponent(date)}`);
    if (!r.ok) throw new Error(`papers(${date}) ${r.status}`);
    return r.json();
  },
  async paper(arxivId, date) {
    let url = `/api/paper?arxiv_id=${encodeURIComponent(arxivId)}`;
    if (date) url += `&date=${encodeURIComponent(date)}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`paper ${r.status}`);
    return r.json();
  },
  async search(q, limit = 50) {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
    if (!r.ok) throw new Error(`search ${r.status}`);
    return r.json();
  },
};

// ---- State ----------------------------------------------------------
const state = {
  dates: [],          // ['2026-05-08', '2026-05-07', ...]
  annual: [],         // ['annual-2026-05-07', ...]
  digestsDir: '',
  paperCache: {},     // arxiv_id → full_md
};

// ---- Render helpers -------------------------------------------------
function setStatus(msg, isError = false) {
  const el = $('#status-bar');
  el.textContent = msg || '';
  el.classList.toggle('error', isError);
}

function dayAnchor(date) { return `day-${date}`; }

function renderSidebar() {
  const nav = $('#sidebar-nav');
  nav.innerHTML = '';

  const addGroup = (label, dates) => {
    if (!dates.length) return;
    const grp = document.createElement('div');
    grp.className = 'nav-group';
    grp.innerHTML = `<div class="nav-group-title">${escape(label)}</div>`;
    dates.forEach(d => {
      const a = document.createElement('a');
      a.className = 'nav-item';
      a.href = `#${dayAnchor(d)}`;
      a.dataset.date = d;
      a.innerHTML = `<span>${escape(d.replace(/^annual-/, ''))}</span>`;
      a.addEventListener('click', e => {
        e.preventDefault();
        scrollToDay(d);
      });
      grp.appendChild(a);
    });
    nav.appendChild(grp);
  };

  addGroup('Daily', state.dates);
  addGroup('Annual', state.annual);
}

function renderDayBlock(date, papersData) {
  const block = document.createElement('section');
  block.className = 'day-block';
  block.id = dayAnchor(date);

  const isAnnual = date.startsWith('annual-');
  const headerLabel = isAnnual ? date.replace(/^annual-/, 'Annual ') : date;
  const count = papersData.papers.length;

  block.innerHTML = `
    <h2 class="day-header${isAnnual ? ' annual' : ''}">
      <span>${escape(headerLabel)}</span>
      <span class="day-count">${count} papers</span>
    </h2>
    <div class="cards-grid"></div>
  `;
  const grid = block.querySelector('.cards-grid');

  papersData.papers.forEach((p, i) => {
    grid.appendChild(makeCard(p, i + 1, date));
  });
  return block;
}

function makeCard(p, fallbackRank, date) {
  const aid = p.arxiv_id || '';
  const title = p.title || aid || '(无标题)';
  const tagline = p.tagline || '';
  const rank = p.rank || fallbackRank;

  const card = document.createElement('div');
  card.className = 'paper';
  card.dataset.aid = aid;
  card.dataset.date = date;
  card.dataset.searchBlob = `${title} ${tagline} ${aid}`.toLowerCase();

  card.innerHTML = `
    <div class="paper-head">
      <span class="rank">#${rank}</span>
      <p class="paper-title">${escape(title)}</p>
    </div>
    ${tagline ? `<p class="paper-tagline">${escape(tagline)}</p>` : ''}
    <p class="paper-meta">
      ${aid ? `<a href="https://arxiv.org/abs/${aid}" target="_blank" rel="noopener" onclick="event.stopPropagation()">arxiv:${aid}</a>` : ''}
    </p>
  `;
  card.addEventListener('click', () => openPaper(aid, date));
  return card;
}

// Search-result card uses date instead of rank
function makeSearchCard(hit) {
  const aid = hit.arxiv_id || '';
  const title = hit.title || aid || '(无标题)';
  const tagline = hit.tagline || '';

  const card = document.createElement('div');
  card.className = 'paper';
  card.dataset.aid = aid;
  card.dataset.date = hit.date;
  card.innerHTML = `
    <div class="paper-head">
      <span class="rank from-date">${escape(hit.date)}</span>
      <p class="paper-title">${escape(title)}</p>
    </div>
    ${tagline ? `<p class="paper-tagline">${escape(tagline)}</p>` : ''}
    <p class="paper-meta">
      ${aid ? `<a href="https://arxiv.org/abs/${aid}" target="_blank" rel="noopener" onclick="event.stopPropagation()">arxiv:${aid}</a>` : ''}
    </p>
  `;
  card.addEventListener('click', () => openPaper(aid, hit.date));
  return card;
}

// ---- Sidebar active highlight via IntersectionObserver --------------
let activeNavObserver;
function setupActiveNavObserver() {
  if (activeNavObserver) activeNavObserver.disconnect();
  const opts = { root: $('#main'), rootMargin: '-10% 0px -70% 0px', threshold: 0 };
  activeNavObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const id = entry.target.id; // day-2026-05-08
        const date = id.replace(/^day-/, '');
        document.querySelectorAll('.nav-item').forEach(a => {
          a.classList.toggle('active', a.dataset.date === date);
        });
      }
    });
  }, opts);
  document.querySelectorAll('.day-block').forEach(b => activeNavObserver.observe(b));
}

// ---- Scroll-to-day --------------------------------------------------
function scrollToDay(date) {
  const block = document.getElementById(dayAnchor(date));
  if (block) {
    block.scrollIntoView({ behavior: 'smooth', block: 'start' });
    document.querySelectorAll('.nav-item').forEach(a => {
      a.classList.toggle('active', a.dataset.date === date);
    });
  }
}

function scrollToTop() {
  $('#main').scrollTo({ top: 0, behavior: 'smooth' });
  if (state.dates.length) {
    document.querySelectorAll('.nav-item').forEach(a => {
      a.classList.toggle('active', a.dataset.date === state.dates[0]);
    });
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
  root.classList.remove('light', 'dark');
  if (next !== 'system') root.classList.add(next);
  localStorage.setItem('theme', next);
}
function applyStoredTheme() {
  const saved = localStorage.getItem('theme') || 'system';
  document.documentElement.classList.remove('light', 'dark');
  if (saved !== 'system') document.documentElement.classList.add(saved);
}

// ---- Search ---------------------------------------------------------
let searchTimer = null;
function onSearch(e) {
  const q = e.target.value.trim();
  clearTimeout(searchTimer);
  if (!q) {
    hideSearchResults();
    return;
  }
  searchTimer = setTimeout(() => doSearch(q), 250);
}

async function doSearch(q) {
  setStatus(`搜索 "${q}"…`);
  try {
    const res = await api.search(q, 80);
    showSearchResults(res.hits || [], q);
    setStatus('');
  } catch (err) {
    setStatus(`搜索失败: ${err.message}`, true);
  }
}

function showSearchResults(hits, q) {
  $('#day-stream').classList.add('hidden');
  const sec = $('#search-results');
  sec.classList.remove('hidden');
  $('#search-count').textContent = `"${q}" · ${hits.length} hits`;
  const grid = $('#search-grid');
  grid.innerHTML = '';
  hits.forEach(h => grid.appendChild(makeSearchCard(h)));
  $('#main').scrollTop = 0;
}
function hideSearchResults() {
  $('#search-results').classList.add('hidden');
  $('#day-stream').classList.remove('hidden');
}

function toggleSidebarSearch() {
  const wrap = $('#sidebar-search');
  wrap.classList.toggle('hidden');
  if (!wrap.classList.contains('hidden')) {
    setTimeout(() => $('#search').focus(), 50);
  }
}

// ---- Refresh --------------------------------------------------------
async function refresh() {
  state.paperCache = {};
  $('#day-stream').innerHTML = '';
  $('#sidebar-nav').innerHTML = '';
  hideSearchResults();
  await init();
}

// ---- Bootstrap ------------------------------------------------------
async function init() {
  applyStoredTheme();
  setStatus('正在加载…');
  try {
    const dRes = await api.dates();
    state.dates = dRes.dates || [];
    state.annual = dRes.annual_digests || [];
    state.digestsDir = dRes.digests_dir || '';

    if (!state.dates.length && !state.annual.length) {
      setStatus(`没找到任何 digest（${state.digestsDir}）`, true);
      $('#empty').classList.remove('hidden');
      return;
    }
    renderSidebar();

    // Eagerly fetch all daily + annual digests in parallel
    const allDates = [...state.dates, ...state.annual];
    setStatus(`加载 ${allDates.length} 个 digest…`);

    const results = await Promise.all(
      allDates.map(d => api.papers(d).catch(e => ({ error: e.message, date: d, papers: [] })))
    );

    const stream = $('#day-stream');
    stream.innerHTML = '';
    results.forEach((res, i) => {
      const d = allDates[i];
      const block = renderDayBlock(d, res);
      stream.appendChild(block);
    });

    setupActiveNavObserver();

    // Highlight latest day in sidebar
    if (state.dates.length) {
      document.querySelectorAll('.nav-item').forEach(a => {
        a.classList.toggle('active', a.dataset.date === state.dates[0]);
      });
    }

    setStatus('');
    $('#main').scrollTop = 0;
  } catch (e) {
    setStatus(`无法连接服务器: ${e.message}`, true);
  }
}

// ---- Wire events ----------------------------------------------------
window.addEventListener('DOMContentLoaded', () => {
  $('#btn-search').addEventListener('click', toggleSidebarSearch);
  $('#btn-refresh').addEventListener('click', refresh);
  $('#btn-top').addEventListener('click', scrollToTop);
  $('#search').addEventListener('input', onSearch);
  $('#search-clear').addEventListener('click', () => {
    $('#search').value = '';
    hideSearchResults();
  });
  $('#theme-toggle').addEventListener('click', toggleTheme);
  $('#detail-close').addEventListener('click', () => $('#detail').close());
  $('#detail-ask').addEventListener('click', e => copyAskPrompt(e.target.dataset.aid));

  $('#detail').addEventListener('click', e => {
    if (e.target === $('#detail')) $('#detail').close();
  });

  document.addEventListener('keydown', e => {
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      const wrap = $('#sidebar-search');
      wrap.classList.remove('hidden');
      $('#search').focus();
    }
  });

  init();
});
