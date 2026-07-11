const $ = (s) => document.querySelector(s);
let data = null;
let swapped = false;

function esc(s='') { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function markdown(md) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(md));
  return `<pre>${esc(md)}</pre>`;
}
function mean(scores) {
  const vals = Object.values(scores || {}).map(Number);
  return vals.length ? vals.reduce((a,b)=>a+b,0)/vals.length : 0;
}
function modelLabel(m) { return m === 'gpt-5.6-sol' ? 'GPT-5.6-Sol' : 'Claude Opus 4.8'; }
function renderSummary() {
  const s = data.summary;
  $('#summary').innerHTML = `
    <div class="metric"><b>${s.wins['gpt-5.6-sol']}–${s.wins['claude-opus-4-8']}</b><span>GPT–Opus 胜负</span></div>
    <div class="metric"><b>${s.mean_scores.factuality['gpt-5.6-sol'].toFixed(2)}</b><span>GPT 事实准确</span></div>
    <div class="metric"><b>${s.mean_scores.factuality['claude-opus-4-8'].toFixed(2)}</b><span>Opus 事实准确</span></div>
    <div class="metric"><b>${s.n}</b><span>匿名配对论文</span></div>`;
}
function issuesHTML(items) {
  if (!items || !items.length) return '';
  return `<b>评委指出的问题</b><ul>${items.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>`;
}
function renderPaper(index) {
  const p = data.papers[index];
  const leftModel = swapped ? 'gpt-5.6-sol' : 'claude-opus-4-8';
  const rightModel = swapped ? 'claude-opus-4-8' : 'gpt-5.6-sol';
  const labelForModel = Object.fromEntries(Object.entries(p.mapping).map(([label, model]) => [model, label]));
  const leftLabel = labelForModel[leftModel], rightLabel = labelForModel[rightModel];
  const winnerModel = p.mapping[p.judge.overall] || 'tie';
  $('#left-model').textContent = modelLabel(leftModel);
  $('#right-model').textContent = modelLabel(rightModel);
  $('#left-score').textContent = `均分 ${mean(p.judge.scores[leftLabel]).toFixed(2)}`;
  $('#right-score').textContent = `均分 ${mean(p.judge.scores[rightLabel]).toFixed(2)}`;
  $('#left-issues').innerHTML = issuesHTML(p.judge.issues[leftLabel]);
  $('#right-issues').innerHTML = issuesHTML(p.judge.issues[rightLabel]);
  $('#left-body').innerHTML = markdown(p.outputs[leftModel]);
  $('#right-body').innerHTML = markdown(p.outputs[rightModel]);
  $('#left-pane').classList.toggle('winner', winnerModel === leftModel);
  $('#right-pane').classList.toggle('winner', winnerModel === rightModel);
  $('#verdict').innerHTML = `<b>盲评：${winnerModel === 'tie' ? '平局' : modelLabel(winnerModel)+' 胜'}</b> · 置信度 ${esc(p.judge.confidence)}<br>${esc(p.judge.reason)}`;
  const figure = $('#paper-figure');
  const figureImg = $('#paper-figure-img');
  if (p.figure_url) {
    figureImg.src = p.figure_url;
    figureImg.alt = `${p.title} 论文主图`;
    figure.hidden = false;
  } else {
    figure.hidden = true;
    figureImg.removeAttribute('src');
    figureImg.alt = '';
  }
  history.replaceState(null, '', `#${p.aid}`);
  window.scrollTo({top: 0, behavior: 'instant'});
}

async function boot() {
  const response = await fetch('data.json?v=2');
  if (!response.ok) throw new Error(`data ${response.status}`);
  data = await response.json();
  renderSummary();
  const select = $('#paper-select');
  select.innerHTML = data.papers.map((p,i)=>`<option value="${i}">${i+1}. ${esc(p.title)} · ${p.aid}</option>`).join('');
  const wanted = location.hash.slice(1);
  const initial = Math.max(0, data.papers.findIndex(p=>p.aid===wanted));
  select.value = String(initial);
  select.addEventListener('change', () => renderPaper(Number(select.value)));
  $('#swap').addEventListener('click', () => { swapped = !swapped; renderPaper(Number(select.value)); });
  renderPaper(initial);
}
boot().catch(err => { document.body.innerHTML = `<pre>加载失败：${esc(err.message)}</pre>`; });
