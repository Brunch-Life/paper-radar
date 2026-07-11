// =====================================================================
// 论文雷达 · Paper Radar — 学术编辑部前端 (production, vanilla JS, no build)
// 设计语言来自 demos/c.html；功能与竞态防护逐项等价迁移自旧版 app.js。
//
// Architecture notes:
// - Event delegation: one click listener each on #stream / #results-list /
//   #reader instead of per-entry listeners.
// - Progressive feed: skeleton day-blocks render right after /api/dates;
//   each /api/papers fills its own block as it lands.
// - Race guards: state.openSeq (reader/chat/deep-read single source of
//   truth), viewSeq (result views), chatBusy (seq-guarded release),
//   IME guards, 250ms debounce (cleared by setSeg/hideResults), poll caps,
//   localStorage try/catch.
// - Boot immediately: classic script at end of <body>; never waits for the
//   two deferred CDN libs (marked/DOMPurify missing ⇒ escaped-<pre> fallback).
// =====================================================================

// ── 1. UTIL ──────────────────────────────────────────────────────────
const $ = (sel, root = document) => root.querySelector(sel);

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const extractTitle = (md) => {
  const m = String(md || '').match(/^#\s+([^\n]+)/);
  return m ? m[1].trim() : '';
};

function setStatus(msg, isError = false) {
  const el = $('#status-bar');
  el.textContent = msg || '';
  el.classList.toggle('error', isError);
}

let toastSeq = 0;
function toast(msg) {
  const box = $('#toasts');
  if (!box) return;
  const el = document.createElement('div');
  el.className = 'toast';
  el.dataset.tid = String(++toastSeq);
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

const dayAnchor = (date) => `day-${date}`;
const pad2 = (n) => String(n).padStart(2, '0');

function fmtTs(ts) {
  if (!ts) return 'on-demand';
  const d = new Date(ts * 1000);
  if (isNaN(d)) return 'on-demand';
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

const WEEKDAYS = '日一二三四五六';
function fmtDateBtn(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || '');
  if (!m) return s;
  const dt = new Date(+m[1], +m[2] - 1, +m[3]);
  return `${+m[2]}月${+m[3]}日 周${WEEKDAYS[dt.getDay()]}`;
}
function annualLabel(s) {
  const m = /^annual-(\d{4})/.exec(s || '');
  return m ? `${m[1]} 年度精选` : String(s || '').replace(/^annual-/, 'Annual ');
}
function fmtDateLong(s) {
  const am = /^annual-(\d{4})/.exec(s || '');
  if (am) return `${am[1]} 年度精选特辑`;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || '');
  if (!m) return s;
  const dt = new Date(+m[1], +m[2] - 1, +m[3]);
  return `${+m[1]} 年 ${+m[2]} 月 ${+m[3]} 日 · 星期${WEEKDAYS[dt.getDay()]}`;
}

// ── markdown pipeline ────────────────────────────────────────────────
// Sanitized HTML only: if EITHER marked or DOMPurify is missing (CDN
// failure), degrade to an escaped <pre> — never inject raw HTML.
let purifyHooked = false;
let markedConfigured = false;
function ensureMdLibs() {
  if (!markedConfigured && window.marked && typeof window.marked.use === 'function') {
    markedConfigured = true;
    try { window.marked.use({ gfm: true, breaks: false }); } catch { /* ignore */ }
  }
  if (purifyHooked || !window.DOMPurify) return;
  purifyHooked = true;
  window.DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (node.tagName === 'IMG') {
      node.setAttribute('loading', 'lazy');
      node.setAttribute('decoding', 'async');
    }
    if (node.tagName === 'A' && node.hasAttribute('href')) {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer');
    }
  });
}

function mdToHtml(md) {
  if (!(window.marked && typeof window.marked.parse === 'function' && window.DOMPurify)) return null;
  ensureMdLibs();
  let mathId = 0;
  const slot = (tex, display) => {
    const encoded = encodeURIComponent(tex.trim());
    return `<span class="math-slot${display ? ' display' : ''}" data-tex="${encoded}" data-math-id="${mathId++}">${escapeHtml(tex.trim())}</span>`;
  };
  // Protect TeX before marked parses it: marked treats \( as an escaped "("
  // and otherwise destroys the delimiter KaTeX needs to recognize.
  let source = String(md || '')
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, tex) => slot(tex, true))
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, tex) => slot(tex, false))
    .replace(/(^|[^\\$])\$([^\n$]+?)\$/g, (_, lead, tex) => lead + slot(tex, false));
  return window.DOMPurify.sanitize(window.marked.parse(source));
}

function renderMathSlots(root, attempt = 0) {
  if (!root) return;
  const slots = root.querySelectorAll('.math-slot:not([data-rendered])');
  if (!slots.length) return;
  if (!window.katex || typeof window.katex.render !== 'function') {
    if (attempt < 20) setTimeout(() => renderMathSlots(root, attempt + 1), 250);
    return;
  }
  slots.forEach((el) => {
    let tex = '';
    try { tex = decodeURIComponent(el.dataset.tex || ''); } catch { tex = el.textContent || ''; }
    try {
      window.katex.render(tex, el, {
        displayMode: el.classList.contains('display'),
        throwOnError: false,
        strict: 'ignore',
        trust: false,
      });
      el.dataset.rendered = '1';
    } catch {
      el.textContent = tex;
      el.dataset.rendered = 'error';
    }
  });
}

function renderMarkdown(md, el, append) {
  const html = mdToHtml(md);
  const out = html !== null ? html : `<pre class="md-fallback">${escapeHtml(md || '')}</pre>`;
  if (append) el.insertAdjacentHTML('beforeend', out);
  else { el.innerHTML = out; el.scrollTop = 0; }
  renderMathSlots(el);
}

// 降级精读横幅：browse 兜底生成（未读全文），可信度有限，置于文章最顶
function degradedNoteHTML(degraded) {
  if (!degraded) return '';
  return `<div class="degraded-note" role="alert">
    <span class="dn-ic" aria-hidden="true">⚠</span>
    <div><b>降级精读</b>：生成时未读到论文全文（浏览兜底版），事实性可能偏弱。
    删除后重新生成即可拿到全文版精读。</div>
  </div>`;
}

// gpt/opus 事实核查卡（置于文章顶部）
function reviewCardHTML(rev) {
  if (!rev || typeof rev.score !== 'number') return '';
  const s = rev.score;
  const cls = s >= 9 ? 'good' : s >= 7 ? 'ok' : s >= 5 ? 'warn' : 'bad';
  const model = rev.model && rev.model.includes('gpt') ? 'GPT'
    : rev.model && rev.model.includes('opus') ? 'Opus' : '审核';
  const issues = (rev.issues || []).length
    ? `<ul class="rev-issues">${rev.issues.map((i) => `<li>${escapeHtml(i)}</li>`).join('')}</ul>` : '';
  const comp = rev.completeness
    ? `<p class="rev-comp">遗漏（不计分）：${escapeHtml(rev.completeness)}</p>` : '';
  return `<aside class="review-card ${cls}">
    <div class="rev-head">
      <span class="rev-score">${s.toFixed(1)}<span class="rev-max">/10</span></span>
      <div class="rev-meta">
        <span class="rev-label">${model} 事实核查</span>
        <p class="rev-verdict">${escapeHtml(rev.verdict || '')}</p>
      </div>
    </div>${issues}${comp}
  </aside>`;
}

// ── 2. API ───────────────────────────────────────────────────────────
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
  async paper(pid, date) {
    let url = `/api/paper?pid=${encodeURIComponent(pid)}`;
    if (date) url += `&date=${encodeURIComponent(date)}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`paper ${r.status}`);
    return r.json();
  },
  async search(q, limit = 80) {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
    if (!r.ok) throw new Error(`search ${r.status}`);
    return r.json();
  },
  async recommend(limit = 40) {
    const r = await fetch(`/api/recommend?limit=${limit}`);
    if (!r.ok) throw new Error(`recommend ${r.status}`);
    return r.json();
  },
  async ondemand() {
    const r = await fetch('/api/ondemand');
    if (!r.ok) throw new Error(`ondemand ${r.status}`);
    return r.json();
  },
  async weekly() {
    const r = await fetch('/api/weekly');
    if (!r.ok) throw new Error(`weekly ${r.status}`);
    return r.json();
  },
  async askHistory(pid) {
    const r = await fetch(`/api/ask?pid=${encodeURIComponent(pid)}`);
    return r.ok ? r.json() : { history: [] };
  },
  async ask(pid, question) {
    const r = await fetch('/api/ask', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid, question }),
    });
    const j = await r.json();               // !ok 时也解析，取后端 j.error 文案
    if (!r.ok) throw new Error(j.error || `ask ${r.status}`);
    return j;
  },
  async askClear(pid) {
    try {
      const r = await fetch(`/api/ask?pid=${encodeURIComponent(pid)}`, { method: 'DELETE' });
      return r.ok;
    } catch { return false; }
  },
  async signals() {
    const r = await fetch('/api/signals');
    return r.ok ? r.json() : { votes: {}, saved: [], hidden: [] };
  },
  async deepreadStart(url) {
    const r = await fetch('/api/deepread', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || `deepread ${r.status}`);
    return j;
  },
  async deepreadPoll(id) {
    if (!id) throw new Error('no job id');
    const r = await fetch(`/api/deepread?id=${encodeURIComponent(id)}`);
    if (!r.ok) { const e = new Error(`deepread poll ${r.status}`); e.status = r.status; throw e; }
    return r.json();
  },
  _post(path, body) {
    return fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), keepalive: true,
    });
  },
  // parse JSON: redis 掉线时后端回 200 + {ok:false}，HTTP 层 r.ok 看不见
  vote(pid, v) { return this._post('/api/vote', { pid, vote: v }).then((r) => r.json()); },
  save(pid, s) { return this._post('/api/save', { pid, saved: s }).then((r) => r.json()); },
  interest(pid, score, tags = []) { return this._post('/api/interest', { pid, score, tags }).then((r) => r.json()); },
  async interestOptions(pid) {
    const r = await fetch(`/api/interest/options?pid=${encodeURIComponent(pid)}`);
    if (!r.ok) throw new Error(`interest options ${r.status}`);
    return r.json();
  },
  del(pid) { return this._post('/api/deepread/delete', { pid }); },
  click(pid) { return this._post('/api/click', { pid }).catch(() => {}); },
  dwell(pid, ms) { return this._post('/api/dwell', { pid, ms }).catch(() => {}); },
  async annotations(pid) {
    const r = await fetch(`/api/annotations?pid=${encodeURIComponent(pid)}`);
    return r.ok ? r.json() : { annotations: [] };
  },
  annotate(pid, quote, note) { return this._post('/api/annotate', { pid, quote, note }).then((r) => r.json()); },
  annotateUpdate(pid, id, note) { return this._post('/api/annotate/update', { pid, id, note }).then((r) => r.json()); },
  annotateDelete(pid, id) { return this._post('/api/annotate/delete', { pid, id }).then((r) => r.json()); },
};

// ── 3. STATE ─────────────────────────────────────────────────────────
const state = {
  dates: [], annual: [], digestsDir: '',
  signals: { votes: {}, savedSet: new Set(), hiddenSet: new Set(), interests: {} },
  tagsByPid: {},                      // pid → [tags]  (for reader rubric row)
  openPid: null, openAt: 0, openSeq: 0,
  chatPid: null,                      // pid the chat drawer is wired to
  readerPushed: false,                // 文章页是否压了一条浏览器历史（back 可关）
  lastFocus: null,
  savedScrollY: 0,                    // 打开文章前的首页滚动位置（返回时恢复）
};

// paperCache: pid → {html, md, title, arxiv_url} — sanitized ONCE, LRU-capped.
const CACHE_MAX = 40;
const paperCache = new Map();
function cacheGet(pid) {
  if (!paperCache.has(pid)) return undefined;
  const v = paperCache.get(pid);
  paperCache.delete(pid); paperCache.set(pid, v);   // refresh LRU order
  return v;
}
function cacheSet(pid, v) {
  if (paperCache.has(pid)) paperCache.delete(pid);
  paperCache.set(pid, v);
  if (paperCache.size > CACHE_MAX) paperCache.delete(paperCache.keys().next().value);
}

function resetTransient() {
  paperCache.clear();
  state.tagsByPid = {};
  state.openPid = null;
  state.openAt = 0;
  // openSeq intentionally NOT reset — must stay monotonic across refreshes
}

const readerOpen = () => !$('#reader').hidden;

// ── 4. THEME (暖白纸 ⇄ 夜读) ─────────────────────────────────────────
// storage can throw (blocked cookies / private mode) — never let it kill boot
const safeStorage = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};
function updateThemeButton() {
  const dark = document.documentElement.classList.contains('dark');
  const btn = $('#theme-toggle');
  btn.textContent = dark ? '☀ 日读' : '☽ 夜读';
  btn.setAttribute('aria-label', dark ? '切换到日间纸面模式' : '切换到夜读墨色模式');
}
function syncThemeColorMeta(dark) {
  // iOS/安卓浏览器外框颜色要跟主题走，否则夜读页面配亮白状态栏
  const m = document.querySelector('meta[name=theme-color]');
  if (m) m.content = dark ? '#14120e' : '#faf9f6';
}
function toggleTheme() {
  const dark = !document.documentElement.classList.contains('dark');
  document.documentElement.classList.toggle('dark', dark);
  safeStorage.set('theme', dark ? 'dark' : 'light');
  syncThemeColorMeta(dark);
  updateThemeButton();
}
function applyStoredTheme() {
  // head 内联脚本已在首帧前加过 .dark；这里兜底同步 + 更新按钮文案
  const dark = safeStorage.get('theme') === 'dark';
  document.documentElement.classList.toggle('dark', dark);
  syncThemeColorMeta(dark);
  updateThemeButton();
}

// ── 5. SIGNALS + ACTIONS (👍 👎 🔖 🗑, tag buttons) ───────────────────
function actsHTML(pid) {
  const v = state.signals.votes[pid];
  const saved = state.signals.savedSet.has(pid);
  const interest = state.signals.interests[pid]?.score || 0;
  return `
    <div class="acts paper-actions" data-pid="${escapeHtml(pid ?? '')}">
      <button class="act vote-up${v === 'up' ? ' on' : ''}" data-act="up" type="button" aria-pressed="${v === 'up'}" title="赞 / 多推这类" aria-label="赞，多推这类">👍</button>
      <button class="act vote-down${v === 'down' ? ' on' : ''}" data-act="down" type="button" aria-pressed="${v === 'down'}" title="踩 / 少推这类" aria-label="踩，少推这类">👎</button>
      <button class="act save-btn${saved ? ' on' : ''}" data-act="save" type="button" aria-pressed="${saved}" title="收藏" aria-label="收藏">🔖</button>
      <button class="act interest-btn${interest ? ' on' : ''}" data-act="interest" type="button" title="兴趣分 1–5；用于学习你的研究偏好" aria-label="兴趣分 ${interest || '未评分'}">${interest ? `★${interest}` : '☆'}</button>
      <button class="act del-btn" data-act="del" type="button" title="删除（删掉精读文件和数据，重新生成即可）" aria-label="删除">🗑</button>
    </div>`;
}

function syncActionUI(pid) {
  if (!pid) {
    // empty pid (e.g. reader header during on-demand submit): clear stale
    // highlights from the previous paper instead of leaving them lying
    document.querySelectorAll('#reader-actions .act').forEach((b) => {
      b.classList.remove('on');
      if (b.hasAttribute('aria-pressed')) b.setAttribute('aria-pressed', 'false');
    });
    const emptyPanel = $('#interest-panel');
    if (emptyPanel) emptyPanel.dataset.pid = '';
    return;
  }
  const v = state.signals.votes[pid];
  const saved = state.signals.savedSet.has(pid);
  const interest = state.signals.interests[pid]?.score || 0;
  document.querySelectorAll(`.paper-actions[data-pid="${CSS.escape(pid)}"]`).forEach((wrap) => {
    const up = wrap.querySelector('.vote-up');
    const down = wrap.querySelector('.vote-down');
    if (up) { up.classList.toggle('on', v === 'up'); up.setAttribute('aria-pressed', v === 'up'); }
    if (down) { down.classList.toggle('on', v === 'down'); down.setAttribute('aria-pressed', v === 'down'); }
    const sb = wrap.querySelector('.save-btn');
    if (sb) { sb.classList.toggle('on', saved); sb.setAttribute('aria-pressed', saved); }
    const ib = wrap.querySelector('.interest-btn');
    if (ib) {
      ib.classList.toggle('on', !!interest);
      ib.textContent = interest ? `★${interest}` : '☆';
      ib.setAttribute('aria-label', `兴趣分 ${interest || '未评分'}`);
    }
  });
  const panel = $('#interest-panel');
  if (panel && panel.dataset.pid === pid) {
    panel.querySelectorAll('[data-interest-score]').forEach((button) => {
      const score = Number(button.dataset.interestScore);
      const on = score > 0 && score === interest;
      button.classList.toggle('on', on);
      button.setAttribute('aria-pressed', String(on));
    });
    $('#interest-clear').hidden = !interest;
    const terms = state.signals.interests[pid]?.tags || [];
    $('#interest-status').textContent = interest
      ? `已评分 ${interest}/5${terms.length ? ` · 关注：${terms.map(prettyInterestTerm).join('、')}` : ''}`
      : '尚未评分 · 会影响之后的搜推与日更候选';
  }
}

const interestPickerState = { pid: '', score: 0, selected: new Set(), labels: new Map(), lastFocus: null };

function prettyInterestTerm(term) {
  const raw = String(term || '').split(':').slice(1).join(':') || String(term || '');
  return interestPickerState.labels.get(term) || raw.replace(/-/g, ' ');
}

function renderInterestOptions(groups) {
  const root = $('#interest-picker-groups');
  const html = (groups || []).map((group) => {
    if (!group.options?.length) return '';
    return `<section class="interest-option-group"><h3>${escapeHtml(group.label)}</h3><div class="interest-chips">${group.options.map((option) => {
      interestPickerState.labels.set(option.value, option.label);
      const on = interestPickerState.selected.has(option.value);
      return `<button type="button" class="interest-chip${on ? ' on' : ''}" data-interest-term="${escapeHtml(option.value)}" aria-pressed="${on}">${escapeHtml(option.label)}</button>`;
    }).join('')}</div></section>`;
  }).join('');
  root.innerHTML = html || '<div class="interest-loading">没有自动选项，可在下方添加自定义偏好。</div>';
}

async function openInterestPicker(pid, score) {
  const previous = state.signals.interests[pid] || {};
  const previousTags = previous.tags || [];
  interestPickerState.pid = pid;
  interestPickerState.score = score;
  interestPickerState.selected = new Set(previousTags.filter((x) => String(x).includes(':') && !String(x).startsWith('custom:')));
  interestPickerState.labels = new Map();
  interestPickerState.lastFocus = document.activeElement;
  $('#interest-custom').value = previousTags.filter((x) => !String(x).includes(':') || String(x).startsWith('custom:'))
    .map((x) => String(x).startsWith('custom:') ? String(x).slice(7) : String(x)).join(', ');
  $('#interest-picker-groups').innerHTML = '<div class="interest-loading">正在提取可选项…</div>';
  $('#interest-picker-title').textContent = `兴趣 ${score}/5 · 你关注哪些部分？`;
  $('#interest-picker').hidden = false;
  $('#interest-picker-close').focus();
  try {
    const res = await api.interestOptions(pid);
    if (interestPickerState.pid !== pid) return;
    renderInterestOptions(res.groups || []);
  } catch {
    if (interestPickerState.pid === pid) renderInterestOptions([]);
  }
}

function closeInterestPicker() {
  $('#interest-picker').hidden = true;
  const focus = interestPickerState.lastFocus;
  interestPickerState.pid = '';
  if (focus && document.contains(focus)) focus.focus();
}

function commitInterest(pid, score, terms) {
  const previous = state.signals.interests[pid] || null;
  if (score) state.signals.interests[pid] = { score, tags: terms };
  else delete state.signals.interests[pid];
  syncActionUI(pid);
  api.interest(pid, score || null, terms).then((r) => {
    if (!r || !r.ok) throw 0;
    toast(score ? `已记录兴趣分 ${score}/5，将用于后续搜推` : '已清除兴趣分');
  }).catch(() => {
    if (previous) state.signals.interests[pid] = previous;
    else delete state.signals.interests[pid];
    syncActionUI(pid);
    toast('兴趣评分失败，已回滚');
  });
}

function saveInterestPicker() {
  const pid = interestPickerState.pid;
  if (!pid) return;
  const custom = $('#interest-custom').value.split(/[,，]/).map((x) => x.trim()).filter(Boolean).slice(0, 20)
    .map((x) => `custom:${x}`);
  const terms = [...interestPickerState.selected, ...custom];
  const score = interestPickerState.score;
  closeInterestPicker();
  commitInterest(pid, score, terms);
}

function rateInterest(pid, presetScore = null) {
  if (!pid) return;
  const previous = state.signals.interests[pid] || null;
  let score = presetScore;
  if (score === null) {
    const raw = prompt('给这篇论文打兴趣分（1=完全不感兴趣，3=中性，5=非常想多看；输入 0 清除）：', previous?.score || '');
    if (raw === null) return;
    score = Number(raw.trim());
  }
  if (!Number.isInteger(score) || score < 0 || score > 5) {
    toast('请输入 0–5 的整数'); return;
  }
  if (!score) { commitInterest(pid, 0, []); return; }
  openInterestPicker(pid, score);
}

// ── 批注引擎：荧光笔高亮 + 边注（按选中文字锚定，跨会话持久化）──────
// 存储只记 quote 文本 + note；渲染时在正文里 text-search 重新定位并包 <mark>。
// 纯文本锚定对 markdown 重渲染稳健（不依赖 DOM offset）。
state.annots = [];                          // 当前文章的批注

function loadAnnotations(pid) {
  state.annots = [];
  clearHighlights();
  if (!pid) return;
  const seq = state.openSeq;
  api.annotations(pid).then((res) => {
    if (seq !== state.openSeq) return;
    state.annots = res.annotations || [];
    applyHighlights();
  }).catch(() => {});
}

function clearHighlights() {
  document.querySelectorAll('#article mark.hl').forEach((m) => {
    const parent = m.parentNode;
    while (m.firstChild) parent.insertBefore(m.firstChild, m);
    parent.removeChild(m);
    parent.normalize();
  });
}

// 在 #article 的文本节点里找到 quote 首次出现处，用 <mark class="hl"> 包起来
function highlightQuote(quote, id, hasNote) {
  const root = $('#article');
  const needle = (quote || '').replace(/\s+/g, ' ').trim();
  if (!needle) return false;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement.closest('mark.hl') ? NodeFilter.FILTER_REJECT
      : NodeFilter.FILTER_ACCEPT),
  });
  // 收集文本节点 + 拼接串（折叠空白），在拼接串里找 needle，再映射回节点区间
  const nodes = [];
  let full = '';
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const norm = n.nodeValue.replace(/\s+/g, ' ');
    nodes.push({ node: n, start: full.length, text: norm });
    full += norm;
  }
  const at = full.indexOf(needle);
  if (at < 0) return false;
  const end = at + needle.length;
  // 找到跨越 [at,end) 的节点，逐个切分包裹（同一段文本可能横跨多个节点）
  const marks = [];
  for (const seg of nodes) {
    const segEnd = seg.start + seg.text.length;
    if (segEnd <= at || seg.start >= end) continue;
    const localStart = Math.max(0, at - seg.start);
    const localEnd = Math.min(seg.text.length, end - seg.start);
    try {
      const range = document.createRange();
      range.setStart(seg.node, mapOffset(seg.node.nodeValue, localStart));
      range.setEnd(seg.node, mapOffset(seg.node.nodeValue, localEnd));
      const mk = document.createElement('mark');
      mk.className = 'hl' + (hasNote ? ' has-note' : '');
      mk.dataset.aid = id;
      range.surroundContents(mk);
      marks.push(mk);
    } catch { /* 跨元素边界的段落，跳过该子段 */ }
  }
  return marks.length > 0;
}

// 折叠空白后的偏移 → 原始 nodeValue 偏移
function mapOffset(raw, collapsedOffset) {
  let ci = 0;
  for (let i = 0; i < raw.length; i++) {
    if (ci === collapsedOffset) return i;
    const isWs = /\s/.test(raw[i]);
    if (isWs) { while (i + 1 < raw.length && /\s/.test(raw[i + 1])) i++; }
    ci++;
  }
  return raw.length;
}

function applyHighlights() {
  clearHighlights();
  for (const a of state.annots) highlightQuote(a.quote, a.id, !!a.note);
  renderNoteMarkers();
}

// 有备注的高亮在右边距放一个 ❝ 标记，点击弹出/编辑备注
function renderNoteMarkers() {
  document.querySelectorAll('#article .note-flag').forEach((n) => n.remove());
  const withNote = state.annots.filter((a) => a.note);
  for (const a of withNote) {
    const mk = document.querySelector(`#article mark.hl[data-aid="${a.id}"]`);
    if (!mk) continue;
    const flag = document.createElement('button');
    flag.className = 'note-flag';
    flag.type = 'button';
    flag.dataset.aid = a.id;
    flag.textContent = '✎';
    flag.title = a.note;
    mk.appendChild(flag);
  }
}

// 从选中片段创建批注：mode 'hl' 只高亮，'note' 高亮 + 弹备注输入
async function createAnnotation(quote, mode) {
  const pid = state.openPid;
  if (!pid || !quote) return;
  let note = '';
  if (mode === 'note') {
    note = (prompt('添加备注：', '') || '').trim();
    if (note === '' && mode === 'note') { /* 允许空备注→退化为纯高亮 */ }
  }
  const res = await api.annotate(pid, quote, note).catch(() => null);
  if (!res || !res.ok) { toast('批注保存失败'); return; }
  state.annots.push(res.annotation);
  applyHighlights();
  toast(note ? '已加备注' : '已高亮');
}

async function editAnnotation(aid) {
  const a = state.annots.find((x) => x.id === aid);
  if (!a) return;
  const choice = prompt('编辑备注（清空并确定＝删除整条批注）：', a.note || '');
  if (choice === null) return;                       // 取消
  const note = choice.trim();
  if (note === '' && !a.note) {                      // 本就无备注、又清空 → 删高亮
    await removeAnnotation(aid); return;
  }
  if (note === '') {                                 // 有备注被清空 → 问是否删整条
    if (confirm('清空备注并删除这条高亮？')) { await removeAnnotation(aid); }
    return;
  }
  const res = await api.annotateUpdate(state.openPid, aid, note).catch(() => null);
  if (res && res.ok) { a.note = note; applyHighlights(); toast('备注已更新'); }
  else toast('更新失败');
}

async function removeAnnotation(aid) {
  const res = await api.annotateDelete(state.openPid, aid).catch(() => null);
  if (res && res.ok) {
    state.annots = state.annots.filter((x) => x.id !== aid);
    applyHighlights();
    toast('已删除批注');
  } else toast('删除失败');
}

function handleAction(btn) {
  const wrap = btn.closest('.paper-actions');
  const pid = wrap?.dataset.pid;
  if (!pid) return;
  if (btn.dataset.act === 'del') {
    if (!confirm('删除这条？会删掉精读文件和数据（重新生成即可拿到全新的）。')) return;
    state.signals.hiddenSet.add(pid);
    paperCache.delete(pid);
    document.querySelectorAll(`.entry[data-pid="${CSS.escape(pid)}"]`).forEach((c) => c.remove());
    if (state.openPid === pid) requestCloseReader();
    api.del(pid).catch(() => {});
    return;
  }
  if (btn.dataset.act === 'interest') {
    rateInterest(pid);
    return;
  }
  if (btn.dataset.act === 'save') {
    const prev = state.signals.savedSet.has(pid);
    const now = !prev;
    if (now) state.signals.savedSet.add(pid); else state.signals.savedSet.delete(pid);   // optimistic
    syncActionUI(pid);
    api.save(pid, now).then((r) => { if (!r || !r.ok) throw 0; }).catch(() => {
      if (prev) state.signals.savedSet.add(pid); else state.signals.savedSet.delete(pid); // rollback
      syncActionUI(pid);
      toast('收藏失败，已回滚');
    });
  } else {
    const prev = state.signals.votes[pid] || null;
    const next = prev === btn.dataset.act ? null : btn.dataset.act;   // 3-state: re-click clears
    if (next) state.signals.votes[pid] = next; else delete state.signals.votes[pid];      // optimistic
    syncActionUI(pid);
    api.vote(pid, next).then((r) => { if (!r || !r.ok) throw 0; }).catch(() => {
      if (prev) state.signals.votes[pid] = prev; else delete state.signals.votes[pid];    // rollback
      syncActionUI(pid);
      toast('投票失败，已回滚');
    });
  }
}

function searchTag(tag) {
  if (readerOpen()) requestCloseReader();   // a tag click inside the reader = close + search
  clearTimeout(searchTimer);            // pending debounced search must not clobber this
  const inp = $('#search');
  inp.value = `tag:${tag}`;
  updateSearchClear();
  inp.focus();
  doSearch(inp.value);
}

// ── 6. ENTRIES (pure HTML string builders — events are delegated) ────
function entryLinkHTML(p) {
  if (p.arxiv_id) {
    const href = p.arxiv_url || `https://arxiv.org/abs/${p.arxiv_id}`;
    return `<a class="aid" href="${escapeHtml(href)}" target="_blank" rel="noopener" data-stop>arXiv:${escapeHtml(p.arxiv_id)}</a>`;
  }
  if (p.arxiv_url) {
    return `<a class="aid" href="${escapeHtml(p.arxiv_url)}" target="_blank" rel="noopener" data-stop>🔗 源链接</a>`;
  }
  return '';
}

// opts: {rank:N, date} for the feed, or {rank:N, badge:'…', date} for results.
// Returns '' for hidden (deleted) papers — callers just join('').
function entryHTML(p, opts = {}) {
  const pid = p.pid;
  if (pid && state.signals.hiddenSet.has(pid)) return '';
  const title = p.title || pid || '(无标题)';
  if (pid && p.tags) state.tagsByPid[pid] = p.tags;
  const badge = opts.badge ? `<span class="hitdate">${escapeHtml(opts.badge)}</span>` : '';
  const tags = (p.tags || []).slice(0, 5).map((t) =>
    `<button class="tag" type="button" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</button>`).join('');
  return `
  <article class="entry" tabindex="0" data-pid="${escapeHtml(pid ?? '')}" data-date="${escapeHtml(opts.date || p.date || '')}">
    <div class="entry-rank">${opts.rank != null ? pad2(opts.rank) : ''}</div>
    <div class="entry-main">
      <h3 class="entry-title">${escapeHtml(title)}</h3>
      ${p.tagline ? `<p class="entry-tagline">${escapeHtml(p.tagline)}</p>` : ''}
      <div class="entry-meta">
        ${badge}
        ${entryLinkHTML(p)}
        ${tags}
        ${actsHTML(pid)}
      </div>
    </div>
  </article>`;
}

function errboxHTML(msg) {
  return `<div class="errbox"><span class="err-icon">✕</span><span>${escapeHtml(msg)}</span><br><button class="retry" type="button">重 试</button></div>`;
}

// ── 7. FEED (date nav + progressive day-block stream) ────────────────
let lastActiveDate = null;
function renderDateNav() {
  lastActiveDate = null;
  const btn = (d, extra, label) =>
    `<button class="date-btn${extra}" type="button" data-date="${escapeHtml(d)}" title="${escapeHtml(d)}">${escapeHtml(label)}</button>`;
  $('#dates').innerHTML =
    state.dates.map((d) => btn(d, '', fmtDateBtn(d))).join('') +
    state.annual.map((a) => btn(a, ' annual', annualLabel(a))).join('');
}

function setActiveNav(date) {
  if (date === lastActiveDate) return;
  lastActiveDate = date;
  document.querySelectorAll('#dates .date-btn').forEach((b) => {
    const on = b.dataset.date === date;
    b.classList.toggle('on', on);
    if (on) b.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
}

function skeletonBlockHTML(date) {
  const isAnnual = date.startsWith('annual-');
  const label = isAnnual ? annualLabel(date) : fmtDateBtn(date);
  const skel = `
    <div class="skel-entry">
      <div class="skel-line skel-rank"></div>
      <div class="skel-main">
        <div class="skel-line w85"></div>
        <div class="skel-line w60"></div>
        <div class="skel-line w40 thin"></div>
      </div>
    </div>`;
  return `
  <section class="day-block${isAnnual ? ' annual' : ''}" id="${dayAnchor(date)}">
    <div class="kicker">
      <b>${escapeHtml(label)}</b>
      <span class="plain day-count">…</span>
      <span class="kicker-line" aria-hidden="true"></span>
      <span class="kicker-date">${escapeHtml(date)}</span>
    </div>
    <div class="day-list">${skel.repeat(3)}</div>
  </section>`;
}

function fillDayBlock(date, res) {
  const block = document.getElementById(dayAnchor(date));
  if (!block) return;
  const papers = res.papers || [];
  const html = papers.map((p, i) => entryHTML(p, { rank: p.rank || i + 1, date })).join('');
  const shown = (html.match(/class="entry"/g) || []).length;   // hidden pids filtered out
  block.querySelector('.day-count').textContent = res.error ? '—' : `共 ${shown} 篇`;
  // better placeholder estimate for content-visibility
  block.style.containIntrinsicSize = `auto ${Math.max(260, 80 + shown * 165)}px`;
  const list = block.querySelector('.day-list');
  if (res.error) {
    list.innerHTML = `<div class="errbox">加载失败：${escapeHtml(res.error)}</div>`;
  } else if (!shown) {
    list.innerHTML = `<div class="empty">—— ${papers.length ? '这天的论文已全部删除' : '这天没有精读论文'} ——</div>`;
  } else {
    list.innerHTML = html;
  }
}

let activeNavObserver;
function setupActiveNavObserver() {
  if (activeNavObserver) activeNavObserver.disconnect();
  // body 是滚动容器（编辑部单栏页面），root 用 viewport
  activeNavObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) setActiveNav(entry.target.id.replace(/^day-/, ''));
    });
  }, { rootMargin: '-15% 0px -70% 0px', threshold: 0 });
  document.querySelectorAll('.day-block').forEach((b) => activeNavObserver.observe(b));
}

function scrollToDay(date) {
  const block = document.getElementById(dayAnchor(date));
  if (!block) return;
  // instant two-phase jump: content-visibility placeholders resize while
  // scrolling, so a smooth scroll would land off-target. Jump, let layout
  // settle, then correct once.
  block.scrollIntoView({ behavior: 'instant', block: 'start' });
  requestAnimationFrame(() => {
    block.scrollIntoView({ behavior: 'instant', block: 'start' });
    setActiveNav(date);
  });
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (state.dates.length) setActiveNav(state.dates[0]);
}

// ── 8. RESULT VIEWS (search / recommend / on-demand / weekly) ────────
// viewSeq: monotonic guard so a slow response can never overwrite a newer
// view or resurrect a cleared one.
let viewSeq = 0;
let searchTimer;

function updateSearchClear() {
  const has = !!$('#search').value.length;
  $('#search-clear').hidden = !has;
  $('#search-kbd').hidden = has;
}

function showResults(items, { title, makeCount, emptyText }) {
  $('#stream').classList.add('hidden');
  const sec = $('#results');
  sec.classList.remove('hidden');
  $('#results-title').textContent = title;
  let shown = 0;
  const html = items.map((it) => {
    if (it.pid && state.signals.hiddenSet.has(it.pid)) return '';
    shown++;
    const preferenceWhy = (it.why || []).filter((x) => x.w > 0)
      .map((x) => x.tag).slice(0, 2).join(' · ');
    return entryHTML(it, {
      rank: shown,
      badge: preferenceWhy ? `偏好匹配：${preferenceWhy}`
        : (it.date === 'ondemand' ? fmtTs(it.ts) : (it.date || '')),
      date: it.date,
    });
  }).join('');
  $('#results-count').textContent = makeCount(shown);
  $('#results-list').innerHTML = html || `<div class="empty">—— ${escapeHtml(emptyText || '没有结果')} ——</div>`;
  window.scrollTo(0, 0);
}

function showResultsError(title, msg, retryFn) {
  $('#stream').classList.add('hidden');
  const sec = $('#results');
  sec.classList.remove('hidden');
  $('#results-title').textContent = title;
  $('#results-count').textContent = '';
  $('#results-list').innerHTML = errboxHTML(msg);
  $('#results-list .retry').addEventListener('click', retryFn);
}

function hideResults() {
  viewSeq++;                             // invalidate in-flight view fetches
  clearTimeout(searchTimer);             // and any pending debounced search
  $('#results').classList.add('hidden');
  $('#stream').classList.remove('hidden');
  setSeg(null);
}

function setSeg(id) {
  clearTimeout(searchTimer);   // entering any view kills a pending debounced search
  document.querySelectorAll('.views .view-btn').forEach((b) => b.classList.toggle('on', b.id === id));
}

async function doSearch(q) {
  const seq = ++viewSeq;
  setSeg(null);
  setStatus(`搜索 "${q}"…`);
  try {
    const res = await api.search(q, 80);
    if (seq !== viewSeq) return;
    showResults(res.hits || [], {
      title: '检索',
      makeCount: (n) => `「${q}」 · ${n} 条`,
      emptyText: '没有匹配 — 试试 tag:VLA、tag:sim2real，或换个关键词',
    });
    setStatus('');
  } catch (err) {
    if (seq === viewSeq) {
      setStatus(`搜索失败: ${err.message}`, true);
      showResultsError('检索', `搜索失败：${err.message}`, () => doSearch(q));
    }
  }
}

function onSearch(e) {
  const q = e.target.value.trim();
  updateSearchClear();
  clearTimeout(searchTimer);
  if (!q) { hideResults(); return; }
  searchTimer = setTimeout(() => doSearch(q), 250);
}

async function showRecommend() {
  const seq = ++viewSeq;
  setSeg('btn-recommend');
  setStatus('生成「为你推荐」…');
  try {
    const res = await api.recommend(40);
    if (seq !== viewSeq) return;
    showResults(res.papers || [], {
      title: '为你推荐',
      makeCount: (n) => res.personalized
        ? `✨ 按你的 👍👎 / 收藏 / 点击 重排 · ${n} 篇`
        : `还没有信号，先按时间排 · 去点几个 👍/🔖 再回来 · ${n} 篇`,
      emptyText: '暂时没有可推荐的论文',
    });
    setStatus('');
  } catch (e) {
    if (seq === viewSeq) {
      setSeg(null);
      setStatus(`推荐失败: ${e.message}`, true);
      showResultsError('为你推荐', `推荐失败：${e.message}`, showRecommend);
    }
  }
}

async function showOndemand() {
  const seq = ++viewSeq;
  setSeg('btn-ondemand');
  setStatus('加载即时精读历史…');
  try {
    const res = await api.ondemand();
    if (seq !== viewSeq) return;
    showResults(res.papers || [], {
      title: '⚡ 即时精读历史',
      makeCount: (n) => `${n} 篇`,
      emptyText: '还没有 — 去顶部贴个 arXiv 链接试试',
    });
    setStatus('');
  } catch (e) {
    if (seq === viewSeq) {
      setSeg(null);
      setStatus(`加载失败: ${e.message}`, true);
      showResultsError('⚡ 即时精读历史', `加载失败：${e.message}`, showOndemand);
    }
  }
}

async function showWeekly() {
  const seq = ++viewSeq;
  setSeg('btn-weekly');
  setStatus('加载本周综述…');
  try {
    const res = await api.weekly();
    if (seq !== viewSeq) return;
    if (!res.count) {
      showResults([], {
        title: '📅 本周综述',
        makeCount: () => '',
        emptyText: '还没有（周日傍晚自动生成）',
      });
    } else if (res.count === 1 && res.papers && res.papers[0]) {
      setSeg(null);                      // 文章页不是持续视图 — 不留悬空高亮
      openPaper(res.papers[0].pid, 'weekly');
    } else {
      showResults(res.papers || [], { title: '📅 本周综述', makeCount: (n) => `${n} 期`, emptyText: '' });
    }
    setStatus('');
  } catch (e) {
    if (seq === viewSeq) {
      setSeg(null);
      setStatus(`加载失败: ${e.message}`, true);
      showResultsError('📅 本周综述', `加载失败：${e.message}`, showWeekly);
    }
  }
}

// ── 9. READER (article page) ─────────────────────────────────────────
function showReader() {
  const r = $('#reader');
  if (r.hidden) {
    // remember what to return focus to, then move focus INTO the reader —
    // otherwise the background .entry keeps focus and Space/Enter re-fires
    // openPaper (mimics the old <dialog>.showModal() focus transfer).
    state.lastFocus = document.activeElement;
    // 记住首页当前滚动位置：overflow:hidden 锁定后浏览器的自动恢复不可靠，
    // 返回时由 closeReader() 手动 scrollTo 回来（scrollRestoration=manual 配合）。
    state.savedScrollY = window.scrollY;
    r.hidden = false;
    document.body.classList.add('no-scroll');
    r.focus({ preventScroll: true });
  }
  r.scrollTop = 0;
}

function readerLoadingHTML(pid) {
  return `
  <div class="article-loading">
    <p class="loading-note"><span class="loading" aria-hidden="true"></span>正在加载 ${escapeHtml(pid)} 的 6-section 全文…</p>
    <div class="skel-line w70"></div>
    <div class="skel-line w92"></div>
    <div class="skel-line w85"></div>
    <div class="skel-line w60"></div>
  </div>`;
}

function renderReaderTags(pid) {
  const tags = (pid && state.tagsByPid[pid]) || [];
  $('#reader-tags').innerHTML = tags.map((t) =>
    `<button class="tag" type="button" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</button>`).join('');
}

// nav: 'push'(默认,用户点开)| 'have'(前进键恢复,历史条目已存在)| 'none'(刷新/深链,别压历史)
async function openPaper(pid, date, nav) {
  if (!pid) { setStatus('该论文没有可用 id', true); toast('该论文没有可用 id'); return; }
  pushReaderHistory(pid, nav);
  clearOndemandTimers();                 // 精读进行时点开别的条目：停掉对页面的写入
  const seq = ++state.openSeq;
  $('#reader-crumb').textContent = `加载 ${pid}…`;
  $('#reader-arxiv').hidden = true;
  $('#reader-actions').dataset.pid = pid;
  $('#interest-panel').dataset.pid = pid;
  syncActionUI(pid);
  renderReaderTags(pid);
  $('#article').innerHTML = readerLoadingHTML(pid);
  resetChat();
  showReader();

  // engagement: click fires before fetch (failed loads still count)
  state.openPid = pid;
  state.openAt = Date.now();
  api.click(pid);

  try {
    let entry = cacheGet(pid);
    if (!entry) {
      const r = await api.paper(pid, date);
      if (seq !== state.openSeq) return;       // superseded by a newer open
      if (r.error) throw new Error(r.error);
      const md = r.full_md || '';
      entry = {
        html: mdToHtml(md),                     // null if CDN libs missing
        md,
        title: extractTitle(md) || pid,
        arxiv_url: r.arxiv_url || '',
        review: r.review || null,               // gpt/opus audit sidecar
        degraded: !!r.degraded,                 // browse-fallback = full text not read
      };
      cacheSet(pid, entry);
    }
    if (seq !== state.openSeq) return;
    $('#reader-crumb').textContent = entry.title;
    const ax = $('#reader-arxiv');
    if (entry.arxiv_url) { ax.href = entry.arxiv_url; ax.hidden = false; }
    else { ax.hidden = true; }
    const body = $('#article');
    const topHtml = degradedNoteHTML(entry.degraded) + reviewCardHTML(entry.review);
    if (entry.html !== null && entry.html !== undefined) body.innerHTML = topHtml + entry.html;
    else { body.innerHTML = topHtml; renderMarkdown(entry.md, body, true); }   // append after card
    renderMathSlots(body);
    $('#reader').scrollTop = 0;
    loadAnnotations(pid);                        // highlights + margin notes
    setupChat(pid);
  } catch (e) {
    if (seq !== state.openSeq) return;
    $('#article').innerHTML = errboxHTML(`加载精读全文失败：${e.message}`);
    $('#article .retry').addEventListener('click', () => openPaper(pid, date));
  }
}

// ── reader ↔ browser history：后退键 / iOS 侧滑 = 返回目录 ──────────
// 打开文章压入一条 #p=<pid> 历史；UI 关闭统一走 requestCloseReader() →
// history.back() → popstate 做真正的 closeReader（单一关闭路径，不会双关）。
function pushReaderHistory(pid, nav) {
  const mode = nav || 'push';
  try {
    if (mode === 'push') {
      const url = '#p=' + encodeURIComponent(pid);
      if (readerOpen() && state.readerPushed) history.replaceState({ pr: pid }, '', url);
      else { history.pushState({ pr: pid }, '', url); state.readerPushed = true; }
    } else {
      state.readerPushed = (mode === 'have');   // 'none'：刷新/深链，back 会离站，UI 关闭走直接路径
    }
  } catch { state.readerPushed = false; }       // file:// 等场景 pushState 可能抛
}

// 所有 UI 关闭路径（← 目录、Esc、🗑 删除当前 pid、tag 跳搜索）走这里。
function requestCloseReader() {
  if (state.readerPushed) history.back();       // → popstate → closeReader
  else closeReader();
}

// dwell 上报与状态复位不漏不重。fromHistory=true 表示 URL 已被浏览器回退。
function closeReader(opts = {}) {
  if (state.openPid && state.openAt) {
    const ms = Date.now() - state.openAt;
    if (ms > 1500) api.dwell(state.openPid, ms);
  }
  state.openPid = null; state.openAt = 0;
  state.openSeq++;            // invalidate in-flight open / poll / chat
  chatBusy = false;           // unblock chat for the next article
  clearOndemandTimers();
  closeChat();
  $('#reader').hidden = true;
  document.body.classList.remove('no-scroll');
  // 恢复打开文章前的首页滚动位置（instant：绕开 html 的 scroll-behavior:smooth）。
  // 先同步定位（overflow 刚解锁即可滚），再 rAF 补一次防布局回流后跑位。
  const y = state.savedScrollY;
  window.scrollTo({ top: y, left: 0, behavior: 'instant' });
  requestAnimationFrame(() => window.scrollTo({ top: y, left: 0, behavior: 'instant' }));
  state.readerPushed = false;
  // 直接关闭（非历史回退）时清掉深链 hash，避免刷新又弹回文章
  if (!opts.fromHistory && location.hash.startsWith('#p=')) {
    try { history.replaceState(null, '', location.pathname + location.search); } catch { /* ignore */ }
  }
  // return focus to the entry that opened the reader (keyboard/a11y)
  try { state.lastFocus?.focus?.({ preventScroll: true }); } catch { /* gone */ }
  state.lastFocus = null;
}

function copyAskPrompt() {
  const pid = state.chatPid || state.openPid;
  if (!pid) return;                              // 精读 pid 未 wire 前静默 no-op
  if (!navigator.clipboard) { toast('剪贴板不可用（需要 HTTPS）'); return; }
  const prompt = `Use the paper-radar MCP \`get_paper\` tool to load ${pid}, then answer:\n\n[your question here]`;
  const btn = $('#chat-copy');
  navigator.clipboard.writeText(prompt).then(() => {
    const orig = '复制 MCP prompt';
    btn.textContent = '✅ 已复制';
    toast('已复制 — 粘到 Claude Code 主对话');
    setTimeout(() => { btn.textContent = orig; }, 2400);
  }).catch(() => toast('复制失败'));
}

// ── 10. CHAT: 问这篇论文 (relay-backed, per-pid history) ──────────────
function chatBubble(role, html) { return `<div class="chat-msg ${role}">${html}</div>`; }

function botHtml(content) {
  return mdToHtml(content) ?? `<pre class="md-fallback">${escapeHtml(content)}</pre>`;
}

function renderChatMsgs(history) {
  const box = $('#chat-msgs');
  if (!box) return;
  if (!history || !history.length) {
    box.innerHTML = `<div class="chat-empty"><span class="glyph" aria-hidden="true">❧</span>基于这篇论文全文提问，答案会尽量联系到你的 Franka / RLinf / VLA setting。<br>对话按论文单独保存。</div>`;
    return;
  }
  box.innerHTML = history.map((m) => (m.role === 'user'
    ? chatBubble('user', escapeHtml(m.content))
    : chatBubble('bot', botHtml(m.content)))).join('');
  renderMathSlots(box);
  box.scrollTop = box.scrollHeight;
}

function openChat() {
  $('#chat').classList.add('open');
  $('#chat-backdrop').hidden = false;
  const box = $('#chat-msgs');
  box.scrollTop = box.scrollHeight;
}
function closeChat() {
  $('#chat').classList.remove('open');
  $('#chat-backdrop').hidden = true;
}

// ── quote-and-ask：选中正文片段 → 针对该片段提问 ─────────────────────
let chatQuote = '';                       // pending selected-text quote
function setChatQuote(text) {
  chatQuote = (text || '').trim().slice(0, 1500);
  const chip = $('#chat-quote');
  if (chatQuote) {
    $('#cq-text').textContent = chatQuote.length > 120 ? chatQuote.slice(0, 120) + '…' : chatQuote;
    chip.hidden = false;
  } else {
    chip.hidden = true;
  }
}

function resetChat() {
  state.chatPid = null;
  $('#chat-q').disabled = true;
  $('#chat-send').disabled = true;
  $('#chat-q').value = '';       // clear draft so it can't leak into another paper's history
  setChatQuote('');
  const pop = $('#sel-pop'); if (pop) pop.hidden = true;
  renderChatMsgs([]);
  closeChat();
}

let chatBusy = false;
async function sendChat() {
  const pid = state.chatPid;
  const inp = $('#chat-q');
  const typed = inp.value.trim();
  if (!pid || !typed || chatBusy) return;  // chatBusy: 双击 / 连按 Enter 不并发发两条
  // selected-quote goes into the question itself → server history stays a
  // plain string and replays naturally on reload
  const q = chatQuote ? `【针对论文中选中的片段】\n> ${chatQuote}\n\n${typed}` : typed;
  chatBusy = true;
  const seq = state.openSeq;
  const box = $('#chat-msgs');
  box.querySelector('.chat-empty')?.remove();
  box.insertAdjacentHTML('beforeend', chatBubble('user', escapeHtml(q)));
  box.insertAdjacentHTML('beforeend', chatBubble('bot pending', '<span class="loading" aria-hidden="true"></span>思考中…'));
  box.scrollTop = box.scrollHeight;
  inp.value = '';
  try {
    const res = await api.ask(pid, q);
    if (seq !== state.openSeq) return;
    const history = res.history || [];
    const domCount = box.querySelectorAll('.chat-msg:not(.err)').length;   // incl. pending
    if (history.length > domCount) {
      // server has earlier turns the box never showed (history fetch lost the
      // race with this send) — server is the source of truth, render it all
      renderChatMsgs(history);
    } else {
      // append-only fast path: swap the pending bubble for the new reply
      const last = history.filter((m) => m.role !== 'user').pop();
      const pend = box.querySelector('.pending');
      const html = last ? botHtml(last.content) : '(空响应)';
      if (pend) { pend.classList.remove('pending'); pend.innerHTML = html; }
      else box.insertAdjacentHTML('beforeend', chatBubble('bot', html));
      box.scrollTop = box.scrollHeight;
    }
    setChatQuote('');                                    // quote consumed
  } catch (e) {
    if (seq !== state.openSeq) return;
    box.querySelector('.pending')?.remove();
    inp.value = typed;                                   // restore for retry (quote chip kept)
    box.insertAdjacentHTML('beforeend',
      `<div class="chat-msg bot err">出错: ${escapeHtml(e.message)}<button class="chat-retry" type="button">重试</button></div>`);
    const errEl = box.lastElementChild;
    errEl.querySelector('.chat-retry').addEventListener('click', () => { errEl.remove(); sendChat(); });
    box.scrollTop = box.scrollHeight;
  } finally {
    if (seq === state.openSeq) chatBusy = false;         // stale requests never unlock a newer article
  }
}

function setupChat(pid) {
  state.chatPid = pid || null;
  const seq = state.openSeq;
  $('#chat-q').disabled = !pid;
  $('#chat-send').disabled = !pid;
  renderChatMsgs([]);            // empty-state hint
  if (!pid) return;
  // load existing conversation — but never clobber messages the user has
  // already started typing/sending for this article
  api.askHistory(pid).then((h) => {
    if (seq !== state.openSeq || chatBusy) return;
    const box = $('#chat-msgs');
    if (!box || box.querySelector('.chat-msg')) return;
    if (h && h.history && h.history.length) renderChatMsgs(h.history);
  }).catch(() => {});
}

async function clearChat() {
  const pid = state.chatPid;
  if (!pid) return;
  if (!confirm('清空这篇论文的全部对话？')) return;
  const seq = state.openSeq;
  const ok = await api.askClear(pid);
  if (seq !== state.openSeq) return;      // article switched while DELETE in flight
  if (ok) renderChatMsgs([]);
  else toast('清空失败，稍后再试');
}

// ── 11. ON-DEMAND instant deep-read ──────────────────────────────────
let ondemandPolling = null;
let ondemandProgress = null;
const DEEPREAD_EST_MS = 140000;
const DEEPREAD_HARD_TIMEOUT_MS = 10 * 60 * 1000;

function clearOndemandTimers() {
  if (ondemandPolling) { clearInterval(ondemandPolling); ondemandPolling = null; }
  if (ondemandProgress) { clearInterval(ondemandProgress); ondemandProgress = null; }
}

async function submitOndemand() {
  if (readerOpen() && $('#reader-actions').dataset.pid === ''
      && document.getElementById('dr-fill')) return;   // 解析中重复触发（背后焦点按 Enter 等）不重提
  const url = $('#ondemand-url').value.trim();
  if (!url) return;                       // 输入框不清空，失败可直接重试
  const seq = ++state.openSeq;
  pushReaderHistory('deepread');          // back / 侧滑同样能退出精读页
  $('#reader-crumb').textContent = '即时精读';
  $('#reader-arxiv').hidden = true;
  $('#reader-actions').dataset.pid = '';
  syncActionUI('');                       // clear the previous paper's 👍🔖 highlights
  $('#reader-tags').innerHTML = '';
  $('#article').innerHTML = `
    <div class="dr-loading">
      <p class="dr-msg">正在下载并解析论文…<br><span class="dr-sub">约 1–3 分钟；可先返回目录，结果会缓存并进入「即时精读历史」</span></p>
      <div class="dr-progress"><div class="dr-progress-fill" id="dr-fill"></div></div>
      <p class="dr-elapsed" id="dr-elapsed">已用 0s</p>
    </div>`;
  resetChat();
  showReader();
  clearOndemandTimers();
  state.openPid = null;
  state.openAt = 0;                       // dwell starts only when content renders

  const t0 = Date.now();
  ondemandProgress = setInterval(() => {
    const el = Date.now() - t0;
    const pct = 95 * (1 - Math.exp(-el / (DEEPREAD_EST_MS / 2)));   // 渐近 95%，done 才推满
    const fill = document.getElementById('dr-fill');
    const elapsed = document.getElementById('dr-elapsed');
    if (fill) fill.style.width = pct.toFixed(1) + '%';
    if (elapsed) elapsed.textContent = `已用 ${Math.round(el / 1000)}s`;
  }, 400);

  const fail = (msg) => {
    if (seq !== state.openSeq) return;    // stale 回调不许清新任务的定时器
    clearOndemandTimers();
    $('#article').innerHTML = errboxHTML(msg);
    $('#article .retry').addEventListener('click', () => submitOndemand());
  };

  const finish = (md) => {
    if (seq !== state.openSeq) return;
    clearOndemandTimers();
    const fill = document.getElementById('dr-fill');
    if (fill) fill.style.width = '100%';
    setTimeout(() => {
      if (seq !== state.openSeq) return;  // 180ms 窗口内用户可能已返回目录
      $('#reader-crumb').textContent = extractTitle(md) || '即时精读';
      renderMarkdown(md, $('#article'));
      $('#reader').scrollTop = 0;
      if (state.openPid) {
        state.openAt = Date.now();        // dwell measures reading, not waiting
        setupChat(state.openPid);
      }
    }, 180);
  };

  try {
    const { id, pid } = await api.deepreadStart(url);
    if (seq !== state.openSeq) return;
    if (pid) {
      // regenerating an id un-hides it and drops any stale cached body
      state.signals.hiddenSet.delete(pid);
      paperCache.delete(pid);
      state.openPid = pid;
      api.click(pid);
      // wire actions immediately: 👍👎🔖🗑 / MCP prompt / dwell 在解析期间就可用
      $('#reader-actions').dataset.pid = pid;
      syncActionUI(pid);
      if (state.readerPushed) {   // 深链从占位换成真 pid（刷新可恢复这篇）
        try { history.replaceState({ pr: pid }, '', '#p=' + encodeURIComponent(pid)); } catch { /* ignore */ }
      }
    }
    let pollFails = 0;
    const tick = async () => {
      if (Date.now() - t0 > DEEPREAD_HARD_TIMEOUT_MS) {
        fail('解析超时（>10 分钟）— 可能论文太长或 worker 卡住，可重试。');
        return;
      }
      try {
        const res = await api.deepreadPoll(id);
        pollFails = 0;
        if (seq !== state.openSeq) return;   // stale poll must not touch newer task's timers
        if (res.status === 'error') {
          const msg = (res.markdown || '').replace(/^#+[^\n]*\n*/, '').trim().slice(0, 300);
          fail(msg || '解析失败（空响应）');
        } else if (res.status === 'done') {
          clearOndemandTimers();
          finish(res.markdown || '## 解析失败\n\n(空响应)');
        }
      } catch (e) {
        pollFails++;
        if (pollFails >= 5 || e.status === 404 || e.status === 410) {
          fail('轮询失败（任务可能丢失）— 请重试。');
        }
      }
    };
    ondemandPolling = setInterval(tick, 3000);
    tick();
  } catch (e) {
    if (seq === state.openSeq) { clearOndemandTimers(); fail(e.message); }
  }
}

// ── 12. REFRESH + FRESHNESS ──────────────────────────────────────────
async function refresh() {
  const fr = $('#btn-refresh');
  fr.classList.add('refreshing');
  resetTransient();
  hideResults();
  try { await init(); } finally { fr.classList.remove('refreshing'); }
}

function updateFreshness() {
  $('#fresh-date').textContent =
    state.dates[0] || (state.annual[0] || '').replace(/^annual-/, 'Annual ') || '—';
}

function updateMasthead() {
  const s = state.dates[0] || state.annual[0] || null;
  $('#mast-date').textContent = s ? fmtDateLong(s) : '——';
}

// ── 13. INIT ─────────────────────────────────────────────────────────
async function init() {
  setStatus('正在加载…');
  try {
    // signals + dates are independent — fetch in parallel
    const [sig, dRes] = await Promise.all([
      api.signals().catch(() => { toast('读取投票 / 收藏状态失败'); return null; }),
      api.dates(),
    ]);
    if (sig) {
      state.signals = {
        votes: sig.votes || {},
        savedSet: new Set(sig.saved || []),
        hiddenSet: new Set(sig.hidden || []),
        interests: sig.interests || {},
      };
    }
    state.dates = dRes.dates || [];
    state.annual = dRes.annual_digests || [];
    state.digestsDir = dRes.digests_dir || '';
    updateMasthead();
    updateFreshness();

    const allDates = [...state.dates, ...state.annual];
    if (!allDates.length) {
      setStatus(`没找到任何 digest（${state.digestsDir}）`, true);
      $('#empty').classList.remove('hidden');
      $('#stream').innerHTML = '';
      $('#dates').innerHTML = '';
      return;
    }
    $('#empty').classList.add('hidden');
    renderDateNav();

    // progressive: skeletons now, fill each block as its fetch lands
    $('#stream').innerHTML = allDates.map(skeletonBlockHTML).join('');
    window.scrollTo(0, 0);
    setStatus('');
    if (state.dates.length) setActiveNav(state.dates[0]);

    await Promise.allSettled(allDates.map((d) =>
      api.papers(d)
        .then((res) => fillDayBlock(d, res))
        .catch((e) => fillDayBlock(d, { papers: [], error: e.message }))));

    setupActiveNavObserver();
  } catch (e) {
    setStatus(`无法连接服务器: ${e.message}`, true);
    $('#stream').innerHTML = errboxHTML(`无法连接服务器：${e.message}`);
    $('#stream .retry').addEventListener('click', refresh);
  }
}

// ── 14. WIRING ───────────────────────────────────────────────────────
// Boot immediately: this classic script sits at the end of <body>, so the DOM
// above is fully parsed. Waiting for DOMContentLoaded would block boot on the
// two deferred CDN scripts (jsdelivr slow/blocked ⇒ whole app dead).
function boot() {
  // Cache-skew guard: if an edge-cached OLD index.html loaded this NEW app.js,
  // the DOM it expects is absent — force one clean reload instead of throwing.
  if (!document.getElementById('stream') || !document.getElementById('reader')) {
    const u = new URL(location.href);
    if (!u.searchParams.has('_r')) { u.searchParams.set('_r', '1'); location.replace(u.href); }
    return;
  }
  applyStoredTheme();
  // 接管滚动恢复：文章页是全屏 overlay，浏览器自动恢复会与 overflow 锁冲突而落到顶端；
  // 由 showReader/closeReader 手动记忆+还原首页位置。
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

  // --- event delegation: feed + result list (entries, tags, actions) ---
  ['#stream', '#results-list'].forEach((sel) => {
    const el = $(sel);
    el.addEventListener('click', (e) => {
      if (e.target.closest('a[data-stop]')) return;        // plain link navigation
      const tag = e.target.closest('.tag[data-tag]');
      if (tag) { searchTag(tag.dataset.tag); return; }
      const act = e.target.closest('.paper-actions .act');
      if (act) { handleAction(act); return; }
      if (e.target.closest('.retry')) return;              // retry buttons bind directly
      const card = e.target.closest('.entry');
      if (card) openPaper(card.dataset.pid, card.dataset.date);
    });
    el.addEventListener('keydown', (e) => {
      if (readerOpen()) return;            // reader has focus; Space/Enter是它的滚动键，别重开
      if ((e.key === 'Enter' || e.key === ' ') && e.target.classList?.contains('entry')) {
        e.preventDefault();
        openPaper(e.target.dataset.pid, e.target.dataset.date);
      }
    });
  });

  // --- date nav (delegated) ---
  $('#dates').addEventListener('click', (e) => {
    const b = e.target.closest('.date-btn');
    if (!b) return;
    if (!$('#results').classList.contains('hidden')) {   // 结果视图里点日期 = 清搜回目录
      $('#search').value = '';
      updateSearchClear();
      hideResults();
    }
    scrollToDay(b.dataset.date);
  });

  // --- 栏目 views ---
  const segActions = { 'btn-recommend': showRecommend, 'btn-ondemand': showOndemand, 'btn-weekly': showWeekly };
  Object.entries(segActions).forEach(([id, fn]) => {
    $('#' + id).addEventListener('click', (e) => {
      // second click on the active view returns to the timeline — but only if
      // that view is actually showing (a stale highlight must not eat clicks)
      const resultsVisible = !$('#results').classList.contains('hidden');
      if (e.currentTarget.classList.contains('on') && resultsVisible) hideResults();
      else fn();
    });
  });

  // --- mast-top ---
  $('#btn-refresh').addEventListener('click', refresh);
  $('#theme-toggle').addEventListener('click', toggleTheme);
  $('#btn-top').addEventListener('click', scrollToTop);

  // --- search ---
  $('#search').addEventListener('input', onSearch);
  $('#search').addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229) return;    // Esc during IME = cancel candidates only
    if (e.key === 'Escape') {
      e.target.value = '';
      updateSearchClear();
      hideResults();
      e.target.blur();
    }
  });
  $('.search').addEventListener('click', () => $('#search').focus());   // whole strip focuses
  $('#search-clear').addEventListener('click', (e) => {
    e.stopPropagation();
    $('#search').value = '';
    updateSearchClear();
    hideResults();
  });
  $('#results-clear').addEventListener('click', () => {
    $('#search').value = '';
    updateSearchClear();
    hideResults();
  });

  // --- on-demand bar ---
  $('#ondemand-go').addEventListener('click', submitOndemand);
  $('#ondemand-url').addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229) return;      // IME guard (Pinyin)
    if (e.key === 'Enter') { e.preventDefault(); submitOndemand(); }
  });

  // --- reader ---
  $('#reader-back').addEventListener('click', requestCloseReader);
  $('#btn-ask').addEventListener('click', openChat);
  $('#reader').addEventListener('click', (e) => {
    const tag = e.target.closest('.tag[data-tag]');
    if (tag) { searchTag(tag.dataset.tag); return; }
    const interestScore = e.target.closest('[data-interest-score]');
    if (interestScore) {
      rateInterest(state.openPid, Number(interestScore.dataset.interestScore)); return;
    }
    const act = e.target.closest('.paper-actions .act');
    if (act) handleAction(act);
  });

  // --- structured interest picker ---
  $('#interest-picker-groups').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-interest-term]');
    if (!chip) return;
    const term = chip.dataset.interestTerm;
    if (interestPickerState.selected.has(term)) interestPickerState.selected.delete(term);
    else interestPickerState.selected.add(term);
    const on = interestPickerState.selected.has(term);
    chip.classList.toggle('on', on); chip.setAttribute('aria-pressed', String(on));
  });
  $('#interest-picker-close').addEventListener('click', closeInterestPicker);
  $('#interest-picker-cancel').addEventListener('click', closeInterestPicker);
  $('#interest-picker-save').addEventListener('click', saveInterestPicker);
  $('#interest-picker').addEventListener('pointerdown', (e) => {
    if (e.target === $('#interest-picker')) closeInterestPicker();
  });

  // --- chat drawer ---
  $('#chat-close').addEventListener('click', closeChat);
  $('#chat-copy').addEventListener('click', copyAskPrompt);
  $('#chat-clear').addEventListener('click', clearChat);
  $('#chat-send').addEventListener('click', sendChat);
  $('#chat-q').addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229) return;   // don't send mid-IME (Pinyin)
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  // backdrop close only when press AND release were both on the backdrop —
  // a drag that starts elsewhere and releases here must NOT close
  const bd = $('#chat-backdrop');
  let downOnBackdrop = false;
  bd.addEventListener('pointerdown', (e) => { downOnBackdrop = (e.target === bd); });
  bd.addEventListener('click', (e) => {
    if (e.target === bd && downOnBackdrop) closeChat();
    downOnBackdrop = false;
  });

  // --- select article text → floating 高亮 / 备注 / 问选中 ---
  let pendingSel = '';
  const pop = $('#sel-pop');
  const showPop = () => {
    const sel = window.getSelection();
    const text = sel && !sel.isCollapsed ? sel.toString().trim() : '';
    if (!text || text.length < 4 || !state.openPid || !readerOpen()
        || !$('#article').contains(sel.anchorNode)) { pop.hidden = true; return; }
    pendingSel = text;
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    pop.hidden = false;
    const pw = pop.offsetWidth || 220;
    pop.style.left = Math.max(8, Math.min(window.innerWidth - pw - 8,
      rect.left + rect.width / 2 - pw / 2)) + 'px';
    pop.style.top = Math.max(8, rect.top - 46) + 'px';
  };
  $('#article').addEventListener('mouseup', () => setTimeout(showPop, 0));
  $('#article').addEventListener('touchend', () => setTimeout(showPop, 120));
  document.addEventListener('selectionchange', () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) pop.hidden = true;
  });
  $('#reader').addEventListener('scroll', () => { pop.hidden = true; }, { passive: true });
  pop.addEventListener('pointerdown', (e) => e.preventDefault());   // keep the selection
  pop.addEventListener('click', (e) => {
    const b = e.target.closest('.sp-btn');
    if (!b) return;
    const act = b.dataset.sp;
    const sel = pendingSel;
    pop.hidden = true;
    if (!sel) return;
    try { window.getSelection().removeAllRanges(); } catch { /* ignore */ }
    if (act === 'ask') {
      setChatQuote(sel); openChat(); $('#chat-q').focus();
    } else {
      createAnnotation(sel, act);          // 'hl' | 'note'
    }
    pendingSel = '';
  });
  // 点已有高亮/边注 → 编辑该批注
  $('#article').addEventListener('click', (e) => {
    const flag = e.target.closest('.note-flag');
    if (flag) { e.stopPropagation(); editAnnotation(flag.dataset.aid); return; }
    const mk = e.target.closest('mark.hl');
    if (mk) editAnnotation(mk.dataset.aid);
  });
  $('#cq-x').addEventListener('click', () => setChatQuote(''));

  // --- browser history: back/侧滑 关文章，forward 重新打开 ---
  window.addEventListener('popstate', (e) => {
    if (readerOpen()) { closeReader({ fromHistory: true }); return; }
    const m = /^#p=(.+)$/.exec(location.hash);
    if (e.state && e.state.pr && m && m[1] !== 'deepread') {
      openPaper(decodeURIComponent(m[1]), undefined, 'have');
    }
  });

  // --- global shortcuts ---
  document.addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Escape') {
      if ($('#chat').classList.contains('open')) { closeChat(); return; }
      if (readerOpen()) { requestCloseReader(); return; }
      return;   // 搜索框自己的 keydown 处理输入框内的 Esc
    }
    const t = document.activeElement;
    if (e.key === '/' && t && t.tagName !== 'INPUT' && t.tagName !== 'TEXTAREA'
        && !t.isContentEditable && !readerOpen()) {
      e.preventDefault();
      const s = $('#search'); s.focus(); s.select();
    }
  });

  init();

  // deep link / 刷新恢复：#p=<pid> 直接打开那篇（不压历史，back 保持浏览器默认行为）
  const dm = /^#p=(.+)$/.exec(location.hash);
  if (dm && dm[1] !== 'deepread') openPaper(decodeURIComponent(dm[1]), undefined, 'none');
}
boot();
