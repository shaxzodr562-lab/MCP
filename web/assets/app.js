/* SIAT Yordamchi — frontend (vanilla JS, no build step) */
(() => {
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
let uid = 0;

/* ---------- formatting ---------- */
function fmtUz(v) {
  if (v == null || Number.isNaN(v)) return '—';
  let [i, d = ''] = String(Math.round(v * 1e6) / 1e6).split('.');
  d = d.slice(0, 4);
  const neg = i.startsWith('-'); i = i.replace('-', '').replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return (neg ? '-' : '') + i + (d ? ',' + d : '');
}
function compact(v) { // axis labels: plain grouped numbers (unit-neutral, so "mln"/"mlrd" never clash with the indicator unit)
  const a = Math.abs(v);
  if (a >= 100) return fmtUz(Math.round(v));
  return fmtUz(Math.round(v * 10) / 10);
}
const MONTHS = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr'];
function periodHuman(l) {
  const m = /^(\d{4})(?:-(Q|M)(\d{1,2}))?$/.exec(l || '');
  if (!m) return l || '—';
  if (!m[2]) return m[1] + '-yil';
  const n = +m[3];
  if (m[2] === 'Q') return n <= 4 ? `${m[1]}-yil ${n}-chorak` : `${m[1]}-yil (yillik jami)`;
  return `${m[1]}-yil ${MONTHS[n - 1]}`;
}
function periodShort(l) {
  const m = /^(\d{4})(?:-(Q|M)(\d{1,2}))?$/.exec(l || '');
  if (!m) return l || '';
  if (!m[2]) return m[1];
  return m[2] === 'Q' ? `${m[1]} Q${m[3]}` : `${m[1]}-${String(m[3]).padStart(2, '0')}`;
}
function md(text) {
  const lines = esc(text).split('\n'); let out = '', inList = false;
  for (const ln of lines) {
    const b = ln.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    if (/^- /.test(ln)) { if (!inList) { out += '<ul>'; inList = true; } out += `<li>${b.slice(2)}</li>`; }
    else { if (inList) { out += '</ul>'; inList = false; } if (ln.trim()) out += `<p>${b}</p>`; }
  }
  return out + (inList ? '</ul>' : '');
}
const plain = t => String(t).replace(/\*\*/g, '');
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2200); }
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) { let d = ''; try { d = (await r.json()).detail; } catch (_) { } throw new Error(d || `Xatolik ${r.status}`); }
  return r.json();
}
function countUp(node, value, text, ms = 900) {
  const dec = (text.split(',')[1] || '').length; const t0 = performance.now();
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) { node.textContent = text; return; }
  const step = now => {
    const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3), cur = value * e;
    node.textContent = k < 1 ? fmtUz(Number(cur.toFixed(dec))) : text;
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ---------- SVG line chart ---------- */
function lineChart(host, pts, opt = {}) {
  const W = 720, H = opt.h || 210, pl = 66, pr = 14, pt = 14, pb = 28, id = 'c' + (++uid);
  const vals = pts.map(p => p.value); let mn = Math.min(...vals), mx = Math.max(...vals);
  if (mn === mx) { mn -= 1; mx += 1; }
  const span = mx - mn, lo = (mn >= 0 && mn - span * .1 < 0) ? 0 : mn - span * .1, hi = mx + span * .1;
  const X = i => pl + (pts.length === 1 ? (W - pl - pr) / 2 : i * (W - pl - pr) / (pts.length - 1));
  const Y = v => pt + (1 - (v - lo) / (hi - lo)) * (H - pt - pb);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(p.value).toFixed(1)}`).join('');
  const area = `${line}L${X(pts.length - 1).toFixed(1)} ${H - pb}L${X(0).toFixed(1)} ${H - pb}Z`;
  const ticks = [0, 1, 2, 3].map(k => lo + (hi - lo) * k / 3);
  const nl = Math.min(6, pts.length), lab = [...new Set(Array.from({ length: nl }, (_, k) => Math.round(k * (pts.length - 1) / Math.max(1, nl - 1))))];
  const hi_i = opt.highlight ? pts.findIndex(p => p.period === opt.highlight) : -1;
  host.classList.add('chart');
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Grafik">
    <defs><linearGradient id="lg${id}" x1="0" x2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#6366f1"/></linearGradient>
    <linearGradient id="ag${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#22d3ee" stop-opacity=".32"/><stop offset="1" stop-color="#22d3ee" stop-opacity="0"/></linearGradient></defs>
    <g class="grid axis">${ticks.map(t => `<line x1="${pl}" x2="${W - pr}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${pl - 8}" y="${Y(t) + 4}" text-anchor="end">${compact(t)}</text>`).join('')}
      ${lab.map(i => `<text x="${X(i)}" y="${H - 8}" text-anchor="${i === 0 ? 'start' : i === pts.length - 1 ? 'end' : 'middle'}">${periodShort(pts[i].period)}</text>`).join('')}</g>
    <path class="area-path" d="${area}" fill="url(#ag${id})"/>
    <path class="line-path" d="${line}" style="stroke:url(#lg${id})"/>
    ${pts.length <= 40 ? pts.map((p, i) => `<circle cx="${X(i)}" cy="${Y(p.value)}" r="2.6" fill="#22d3ee" opacity=".75"/>`).join('') : ''}
    ${hi_i >= 0 ? `<circle class="hl-ring" cx="${X(hi_i)}" cy="${Y(pts[hi_i].value)}" r="9"/><circle class="hl" cx="${X(hi_i)}" cy="${Y(pts[hi_i].value)}" r="6"/>` : ''}
    <line class="cross" x1="0" x2="0" y1="${pt}" y2="${H - pb}" stroke="var(--accent)" stroke-opacity=".5" style="display:none"/>
    <circle class="dot" r="5.5" fill="var(--accent)" stroke="var(--surface)" stroke-width="3" style="display:none"/>
    <rect x="${pl}" y="0" width="${W - pl - pr}" height="${H}" fill="transparent" class="hit"/></svg><div class="tip"></div>`;
  const path = $('.line-path', host); path.style.setProperty('--len', Math.ceil(path.getTotalLength()) + 2);
  const svg = $('svg', host), tip = $('.tip', host), cross = $('.cross', host), dot = $('.dot', host);
  const move = ev => {
    const r = svg.getBoundingClientRect(), cx = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) / r.width * W;
    let i = Math.round((cx - pl) / ((W - pl - pr) / Math.max(1, pts.length - 1))); i = Math.max(0, Math.min(pts.length - 1, i));
    const p = pts[i]; cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.style.display = '';
    dot.setAttribute('cx', X(i)); dot.setAttribute('cy', Y(p.value)); dot.style.display = '';
    tip.innerHTML = `<b class="num">${fmtUz(p.value)}</b><span>${periodHuman(p.period)}${opt.unit ? ' · ' + esc(opt.unit) : ''}</span>`;
    tip.style.left = Math.min(Math.max(X(i) / W * 100, 14), 86) + '%'; tip.style.top = Y(p.value) / H * 100 + '%'; tip.style.opacity = 1;
  };
  const leave = () => { tip.style.opacity = 0; cross.style.display = dot.style.display = 'none'; };
  $('.hit', host).addEventListener('mousemove', move); $('.hit', host).addEventListener('touchmove', move, { passive: true });
  $('.hit', host).addEventListener('mouseleave', leave); $('.hit', host).addEventListener('touchend', leave);
}

/* ---------- state ---------- */
const S = { view: 'chat', chat: false, busy: false, overview: null, last: null };
const input = $('#input'), form = $('#composer'), sendBtn = $('#send'), feed = $('#feed');
const composerBox = $('#composerHome');
const dock = el('<div class="dock" hidden></div>'); $('#view-chat').appendChild(dock);

/* ---------- navigation ---------- */
function show(view) {
  S.view = view;
  for (const v of ['chat', 'catalog', 'about']) $('#view-' + v).hidden = v !== view;
  $$('.nav button').forEach(b => b.setAttribute('aria-selected', b.dataset.view === view));
  dock.hidden = !S.chat;
  if (view === 'catalog' && !catalog.ready) catalog.init();
  if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  window.scrollTo({ top: 0 });
}
$$('.nav button').forEach(b => b.addEventListener('click', () => show(b.dataset.view)));
$('#brand').addEventListener('click', () => { resetChat(); show('chat'); });
$('#brand').addEventListener('keydown', e => { if (e.key === 'Enter') { resetChat(); show('chat'); } });
$('#theme').addEventListener('click', () => {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t; try { localStorage.setItem('theme', t); } catch (_) { }
});

/* ---------- composer ---------- */
function fit() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 150) + 'px'; sendBtn.disabled = !input.value.trim() || S.busy; }
input.addEventListener('input', fit);
input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
form.addEventListener('submit', e => { e.preventDefault(); const q = input.value.trim(); if (!q || S.busy) return; input.value = ''; fit(); ask(q); });
document.addEventListener('keydown', e => {
  if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); (S.view === 'catalog' ? $('#catQ') : input).focus(); }
  if (e.key === 'Escape') closeDrawer();
});
function startChat() {
  if (S.chat) return; S.chat = true;
  $('#hero').hidden = true; dock.appendChild(composerBox); dock.hidden = false;
}
function resetChat() {
  S.chat = false; feed.innerHTML = ''; $('#hero').hidden = false; dock.hidden = true;
  $('.hero h1').insertAdjacentElement('afterend', $('.lead')); // keep order
  $('.lead').insertAdjacentElement('afterend', composerBox); renderHistory();
}

/* ---------- history ---------- */
const hist = { get() { try { return JSON.parse(localStorage.getItem('hist') || '[]'); } catch (_) { return []; } },
  add(q) { try { const h = [q, ...this.get().filter(x => x !== q)].slice(0, 6); localStorage.setItem('hist', JSON.stringify(h)); } catch (_) { } } };
function renderHistory() {
  const h = hist.get(); $('#history').hidden = !h.length;
  $('#historyChips').innerHTML = h.map(q => `<button class="chip small" data-q="${esc(q)}">↺ ${esc(q.length > 54 ? q.slice(0, 52) + '…' : q)}</button>`).join('');
}
$('#historyChips').addEventListener('click', e => { const b = e.target.closest('[data-q]'); if (b) ask(b.dataset.q); });

/* ---------- ask ---------- */
const STAGES = ['Savol tahlil qilinmoqda…', 'Mos ko\'rsatkich qidirilmoqda…', 'Bazadan o\'qilmoqda va hisoblanmoqda…', 'Raqamlar tekshirilmoqda…'];
async function ask(question, extra = {}, display) {
  if (S.busy) return; S.busy = true; fit();
  show('chat'); startChat();
  feed.appendChild(el(`<div class="msg-user">${esc(display || question)}</div>`));
  const holder = el(`<div class="msg-bot"><div class="answer"><div class="loading"><div class="status"><span class="spin"></span><span class="stage">${STAGES[0]}</span></div><div class="sk" style="width:80%"></div><div class="sk" style="width:55%"></div><div class="sk" style="height:90px"></div></div></div></div>`);
  feed.appendChild(holder); holder.scrollIntoView({ behavior: 'smooth', block: 'end' });
  let k = 0; const timer = setInterval(() => { k = Math.min(k + 1, STAGES.length - 1); const s = $('.stage', holder); if (s) s.textContent = STAGES[k]; }, 650);
  const t0 = performance.now();
  try {
    const data = await api('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, ...extra }) });
    await new Promise(r => setTimeout(r, Math.max(0, 500 - (performance.now() - t0)))); // let the stages register
    clearInterval(timer);
    const bot = renderBot(data, question, extra); holder.replaceWith(bot);
    requestAnimationFrame(() => bot.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    if (!extra.indicator_id && !extra.region) hist.add(question);
  } catch (err) {
    clearInterval(timer);
    holder.replaceWith(el(`<div class="msg-bot"><div class="answer bad"><div class="a-text"><p><strong>Xatolik:</strong> ${esc(err.message)}</p><p>Iltimos, keyinroq qayta urinib ko'ring.</p></div></div></div>`));
  } finally { S.busy = false; fit(); input.focus({ preventScroll: true }); }
}
function scrollEnd() { requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })); }

/* ---------- rendering answers ---------- */
const INTENT = { profile: 'Hudud profili', value: 'Qiymat', growth: "O'sish", series: 'Dinamika', compare: 'Taqqoslash', top: 'Reyting' };
function renderBot(data, q, extra) {
  const wrap = el('<div class="msg-bot"></div>');
  if (!data.parts.length) wrap.appendChild(el('<div class="answer warn"><div class="a-text"><p>Savol bo\'sh.</p></div></div>'));
  data.parts.forEach(p => wrap.appendChild(renderPart(p, p.question || q, data)));
  return wrap;
}
function renderPart(p, q, data) {
  const cls = p.status === 'ok' ? 'ok' : p.status === 'unverified' ? 'bad' : 'warn';
  const tags = [];
  if (p.intent && INTENT[p.intent]) tags.push(`<span class="tag">${INTENT[p.intent]}</span>`);
  if (p.status === 'ok' && p.verification) tags.push(`<span class="tag good" title="Javobdagi har bir raqam bazadagi qiymatga mos">✓ Tekshirildi · ${p.verification.matched}/${p.verification.checked} raqam bazaga mos</span>`);
  if (p.status === 'no_data') tags.push('<span class="tag warn">Ma\'lumot mavjud emas</span>');
  if (p.status === 'no_match') tags.push('<span class="tag warn">Mos ko\'rsatkich yo\'q</span>');
  if (p.status === 'ambiguous') tags.push('<span class="tag warn">Aniqlashtirish kerak</span>');
  if (p.status === 'unverified') tags.push('<span class="tag bad">Tasdiqlanmadi</span>');
  if (data && data.engine === 'gemini+rules') tags.push(`<span class="tag" title="Savolni Gemini tushundi${p.interpreted_as ? ': ' + esc(p.interpreted_as) : ''}. Raqamlar kod orqali bazadan olindi.">✨ Gemini tushundi</span>`);
  if (data && data.elapsed_ms != null) tags.push(`<span class="tag">${data.elapsed_ms} ms</span>`);
  const node = el(`<div class="answer ${cls}"><div class="a-head">${tags.join('')}</div><div class="a-text">${md(shownText(p))}</div><div class="visual"></div></div>`);
  const vis = $('.visual', node);
  (p.cards || []).forEach(c => vis.appendChild(renderCard(c)));
  if (!vis.children.length) vis.remove();
  if (p.clarify && p.clarify.length) {
    const f = el('<div class="a-foot"><span class="label">Tanlang:</span></div>');
    p.clarify.forEach(c => { const b = el(`<button class="chip accent">${esc(c.label)}</button>`); b.onclick = () => ask(q, { region: c.region, ...(p.indicator ? { indicator_id: p.indicator.id } : {}) }, c.label); f.appendChild(b); });
    node.appendChild(f);
  }
  if (p.alternatives && p.alternatives.length && ['ok', 'no_data', 'no_match'].includes(p.status)) {
    const box = el(`<div class="alt-list"><span class="label">${p.status === 'ok' ? 'Boshqa mos ko\'rsatkichlar' : 'Shunga yaqin ko\'rsatkichlar'}</span></div>`);
    p.alternatives.slice(0, 3).forEach(a => {
      const b = el(`<button class="alt">${esc(a.name || ('#' + a.indicator_id))}${a.periodicity ? ` <small>· ${esc(a.periodicity)}${a.unit ? ' · ' + esc(a.unit) : ''}</small>` : ''}</button>`);
      b.onclick = () => ask(q, { indicator_id: a.indicator_id }, (a.name || ('#' + a.indicator_id)));
      box.appendChild(b);
    });
    node.appendChild(box);
  }
  if (p.status === 'ok') node.appendChild(renderDetails(p));
  const foot = el('<div class="a-foot"></div>');
  (p.followups || []).forEach(f => { const b = el(`<button class="chip small">${esc(f.label)}</button>`); b.onclick = () => ask(f.question, { indicator_id: f.indicator_id }, f.label); foot.appendChild(b); });
  const acts = el('<div class="a-actions"></div>');
  const copy = el('<button class="ghost">Nusxa</button>');
  copy.onclick = async () => { try { await navigator.clipboard.writeText(plain(p.answer) + (p.indicator ? `\nManba: siat.stat.uz, ko'rsatkich ${p.indicator.code}` : '')); toast('Nusxa olindi'); } catch (_) { toast('Nusxa olib bo\'lmadi'); } };
  acts.appendChild(copy);
  const csv = (p.cards || []).find(c => c.type === 'series' || c.type === 'ranking');
  if (csv) { const b = el('<button class="ghost">CSV</button>'); b.onclick = () => downloadCsv(csv); acts.appendChild(b); }
  foot.appendChild(acts); if (foot.children.length) node.appendChild(foot);
  node.querySelectorAll('.rank-row .bar i').forEach(i => requestAnimationFrame(() => requestAnimationFrame(() => i.style.width = i.dataset.w)));
  return node;
}
function shownText(p) { // the ranking card repeats the bullet list, so hide it in the text
  return (p.cards || []).some(c => c.type === 'ranking' || c.type === 'profile') ? p.answer.split('\n').filter(l => !l.startsWith('- ')).join('\n') : p.answer;
}
function renderCard(c) {
  const box = el('<div class="card"></div>');
  const meta = (items) => `<div class="meta-row">${items.filter(Boolean).map(([k, v]) => `<span class="meta">${k} <b>${esc(v)}</b></span>`).join('')}</div>`;
  const spark = () => { if (c.spark && c.spark.points && c.spark.points.length > 1) { const h = el('<div></div>'); box.appendChild(h); lineChart(h, c.spark.points, { highlight: c.spark.highlight, unit: c.unit, h: 170 }); } };
  if (c.type === 'value') {
    box.innerHTML = meta([['Hudud', c.member], ['Davr', c.period_text], c.cumulative ? ['Turi', 'yil boshidan jamlanma'] : null]) +
      `<div class="big"><span class="v num">${esc(c.value_text)}</span><span class="u">${esc(c.unit || '')}</span></div>`;
    countUp($('.v', box), c.value, c.value_text); spark();
  } else if (c.type === 'growth') {
    const up = c.direction === 'increase', dn = c.direction === 'decrease';
    box.innerHTML = meta([['Hudud', c.member], ['Oraliq', `${c.from.period_text} → ${c.to.period_text}`]]) +
      `<div class="fromto"><div class="ft"><small>${esc(c.from.period_text)}</small><b class="num">${esc(c.from.text)}</b></div><div class="arrow">→</div><div class="ft"><small>${esc(c.to.period_text)}</small><b class="num">${esc(c.to.text)}</b></div></div>
       <div class="big"><span class="delta ${up ? 'up' : dn ? 'down' : 'flat'}">${up ? '▲' : dn ? '▼' : '•'} <span class="num">${c.percent == null ? '—' : fmtUz(Math.abs(c.percent)) + '%'}</span></span><span class="u">${esc(c.unit || '')} · farq ${esc(c.absolute_text)}</span></div>`;
    spark();
  } else if (c.type === 'growth_rate') {
    const g = c.growth, up = g > 0, dn = g < 0;
    box.innerHTML = meta([['Hudud', c.member], ['Davr', c.period_text]]) +
      `<div class="big"><span class="v num">${esc(c.rate_text)}%</span>${g != null ? `<span class="delta ${up ? 'up' : dn ? 'down' : 'flat'}">${up ? '▲' : dn ? '▼' : '•'} <span class="num">${fmtUz(Math.abs(g))}%</span> ${up ? "o'sish" : dn ? 'pasayish' : ''}</span>` : ''}</div>
       <div class="u" style="color:var(--muted);font-size:13px">o'tgan yilga nisbatan, rasmiy o'sish sur'ati</div>`;
    spark();
  } else if (c.type === 'series') {
    const s = c.stats;
    box.innerHTML = meta([['Hudud', c.member], ['Birlik', c.unit || '—'], c.cumulative ? ['Turi', 'yil boshidan jamlanma'] : null]);
    const h = el('<div></div>'); box.appendChild(h); lineChart(h, c.points, { unit: c.unit, h: 240 });
    box.appendChild(el(`<div class="tiles">${[['Birinchi', s.first], ['Oxirgi', s.last], ['Eng yuqori', s.max], ['Eng past', s.min]].map(([k, p]) => `<div class="tile"><small>${k}</small><b class="num">${fmtUz(p.value)}</b><em>${periodHuman(p.period)}</em></div>`).join('')}</div>`));
  } else if (c.type === 'profile') {
    box.innerHTML = `<div class="profile">${c.rows.map((r, i) => `<button class="pf" data-i="${i}" title="Dinamikasini ko'rish"><small>${esc(r.label)}</small><b class="num">${esc(r.text)}</b><span class="u">${esc(r.unit || '')}</span><em>${esc(r.period_text)}</em></button>`).join('')}</div>
      <p style="color:var(--faint);font-size:12.5px;margin:10px 2px 0">Kartochkani bossangiz, shu ko'rsatkichning dinamikasi chiqadi.</p>`;
    box.addEventListener('click', e => { const b = e.target.closest('.pf'); if (!b) return; const r = c.rows[+b.dataset.i]; ask('dinamikasi', { indicator_id: r.indicator_id, region: c.member }, `${c.member}: ${r.label} dinamikasi`); });
  } else if (c.type === 'ranking') {
    const mx = Math.max(...c.rows.map(r => Math.abs(r.value))) || 1;
    box.innerHTML = meta([['Davr', c.period_text], ['Birlik', c.unit || '—']]) +
      `<div class="rank">${c.rows.map((r, i) => `<div class="rank-row"><span class="n">${i + 1}</span><span class="name" title="${esc(r.member)}">${esc(r.member)}</span><span class="bar"><i data-w="${(Math.abs(r.value) / mx * 100).toFixed(1)}%"></i></span><span class="val num">${esc(r.text)}</span></div>`).join('')}</div>`;
  }
  return box;
}
function renderDetails(p) {
  const i = p.indicator || {}, src = p.source || {};
  const facts = (p.facts || []).slice(0, 30);
  const steps = (p.trace || []).map(t => `<div class="step"><span>${esc(t.tool)}(${esc(JSON.stringify(t.args).replace(/^\{|\}$/g, '').slice(0, 90))})</span><span class="st ${esc(t.status)}">${esc(t.status)}</span></div>`).join('');
  const d = el(`<div class="details"><button type="button" aria-expanded="false"><span>🔍 Hisob-kitob va manba</span><svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg></button>
    <div class="body">
      ${p.indicator ? `<dl class="kv"><dt>Ko'rsatkich</dt><dd>${esc(i.name)}</dd><dt>Kod / ID</dt><dd>${esc(i.code)} · #${esc(i.id)}</dd><dt>O'lchov birligi</dt><dd>${esc(i.unit || '—')}</dd>
      <dt>Davriylik</dt><dd>${esc(i.periodicity)}${i.is_cumulative === 1 ? ' · yil boshidan jamlanma' : ''}</dd><dt>Mavjud davrlar</dt><dd>${esc((i.range || []).join(' – '))}</dd>
      <dt>Manba</dt><dd><a href="https://siat.stat.uz/" target="_blank" rel="noopener">siat.stat.uz</a> · ${esc(src.license || 'CC BY 4.0')}</dd></dl>` : ''}
      <div class="steps">${steps}</div>
      ${(p.notes || []).length ? `<p style="color:var(--muted);margin:6px 0">${p.notes.map(esc).join('<br>')}</p>` : ''}
      <table class="facts"><tr><th>Tur</th><th>Nomi / davr</th><th>Qiymat</th></tr>${facts.map(f => `<tr><td>${f.kind === 'calc' ? 'hisoblangan' : 'bazadan'}</td><td>${esc(f.name || periodHuman(f.period) + (f.member ? ' · ' + f.member : ''))}</td><td class="num">${fmtUz(f.value)}</td></tr>`).join('')}</table>
      <p style="color:var(--faint);margin:10px 0 0">Javobdagi har bir raqam yuqoridagi qiymatlardan biriga teng bo'lishi shart; aks holda javob ko'rsatilmaydi.</p>
    </div></div>`);
  $('button', d).onclick = () => { d.classList.toggle('open'); $('button', d).setAttribute('aria-expanded', d.classList.contains('open')); };
  return d;
}
function downloadCsv(c) {
  let rows;
  if (c.type === 'series') rows = [['period', 'value'], ...c.points.map(p => [p.period, p.value])];
  else rows = [['member', 'value'], ...c.rows.map(r => [r.member, r.value])];
  const blob = new Blob(['﻿' + rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'siat-' + (c.type) + '.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* ---------- catalog ---------- */
const catalog = {
  ready: false, cat: null, q: '', per: '', offset: 0, total: 0, cats: [], open: new Set(),
  async init() {
    this.ready = true;
    this.cats = await api('/api/categories'); this.renderTree();
    $('#catQ').addEventListener('input', () => { clearTimeout(this._d); this._d = setTimeout(() => { this.q = $('#catQ').value.trim(); this.load(true); }, 260); });
    $('#catPer').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; $$('#catPer button').forEach(x => x.setAttribute('aria-pressed', x === b)); this.per = b.dataset.v; this.load(true); });
    $('#tree').addEventListener('click', e => {
      const b = e.target.closest('[data-id]'); if (!b) return;
      const id = b.dataset.id === 'all' ? null : +b.dataset.id; this.cat = id;
      if (id != null) { this.open.has(id) ? (this.cat === id && this.open.delete(id)) : this.open.add(id); }
      this.renderTree(); this.load(true);
    });
    $('#catList').addEventListener('click', e => { const b = e.target.closest('[data-ind]'); if (b) openDrawer(+b.dataset.ind); const m = e.target.closest('.more'); if (m) this.load(false); });
    this.load(true);
  },
  renderTree() {
    const kids = id => this.cats.filter(c => c.parent_id === id);
    const node = c => {
      const k = kids(c.id), open = this.open.has(c.id);
      return `<button class="tree-item ${this.cat === c.id ? 'active' : ''}" data-id="${c.id}"><span>${esc(c.name)}</span><span class="c">${c.count}</span></button>` +
        (k.length && open ? `<div class="tree-sub">${k.map(node).join('')}</div>` : '');
    };
    const tops = this.cats.filter(c => c.level === 0);
    $('#tree').innerHTML = `<button class="tree-item ${this.cat == null ? 'active' : ''}" data-id="all"><span>Barcha ko'rsatkichlar</span><span class="c">${tops.reduce((a, c) => a + c.count, 0)}</span></button>` + tops.map(node).join('');
  },
  async load(reset) {
    if (reset) { this.offset = 0; $('#catList').innerHTML = '<div class="sk" style="height:70px"></div><div class="sk" style="height:70px"></div><div class="sk" style="height:70px"></div>'; }
    const qs = new URLSearchParams({ limit: 30, offset: this.offset }); if (this.q) qs.set('q', this.q); if (this.cat) qs.set('category', this.cat); if (this.per) qs.set('periodicity', this.per);
    let d; try { d = await api('/api/catalog?' + qs); } catch (e) { $('#catList').innerHTML = `<p>${esc(e.message)}</p>`; return; }
    if (reset) $('#catList').innerHTML = '';
    $('.more', $('#catList'))?.remove();
    this.total = d.total; this.offset += d.items.length;
    $('#catCount').textContent = `${fmtUz(d.total)} ta ko'rsatkich${this.q ? ` · «${this.q}» bo'yicha eng mos natijalar` : ''}`;
    if (!d.items.length && reset) $('#catList').innerHTML = '<p style="color:var(--muted)">Hech narsa topilmadi.</p>';
    $('#catList').insertAdjacentHTML('beforeend', d.items.map(i => `<button class="ind" data-ind="${i.indicator_id}"><div class="t">${esc(i.name)}</div><div class="m"><span class="tag">${esc(i.periodicity)}</span><span>${esc(i.unit || '')}</span><span>${esc((i.period_range || []).join(' – '))}</span><span class="code">${esc(i.code)}</span></div></button>`).join(''));
    if (this.offset < this.total && d.mode === 'browse') $('#catList').insertAdjacentHTML('beforeend', '<button class="more">Yana ko\'rsatish</button>');
  },
};

/* ---------- drawer ---------- */
const drawer = $('#drawer'), back = $('#drawerBack');
function closeDrawer() { drawer.classList.remove('show'); back.classList.remove('show'); setTimeout(() => { drawer.hidden = back.hidden = true; }, 280); }
back.addEventListener('click', closeDrawer);
async function openDrawer(id, region = null, basis = 'reported') {
  drawer.hidden = back.hidden = false; requestAnimationFrame(() => { drawer.classList.add('show'); back.classList.add('show'); });
  drawer.innerHTML = '<div class="loading"><div class="sk" style="width:70%;height:26px"></div><div class="sk" style="height:200px"></div></div>';
  let d; try { d = await api(`/api/indicator/${id}?basis=${basis}${region ? '&region=' + encodeURIComponent(region) : ''}`); } catch (e) { drawer.innerHTML = `<p>${esc(e.message)}</p>`; return; }
  const ser = d.series, regions = d.members.length > 1;
  drawer.innerHTML = `<button class="icon-btn close" aria-label="Yopish">✕</button><span class="tag">${esc(d.periodicity)}</span><h2>${esc(d.name)}</h2>
    <div class="meta-row"><span class="meta">Birlik <b>${esc(d.unit || '—')}</b></span><span class="meta">Davr <b>${esc((d.period_range || []).join(' – '))}</b></span><span class="meta">Kod <b>${esc(d.code)}</b></span><span class="meta">Qiymatlar <b>${fmtUz(d.values)}</b></span></div>
    ${(d.caveats || []).map(c => `<p class="tag warn" style="white-space:normal">${esc(c)}</p>`).join('')}
    <div class="controls">${regions ? `<select id="dMember" aria-label="Hudud">${d.members.map(m => `<option value="${m.id}" ${ser && ser.member && ser.member.id === m.id ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}</select>` : ''}
      ${d.is_cumulative === 1 ? `<div class="seg" id="dBasis"><button aria-pressed="${basis === 'reported'}" data-v="reported">Jamlanma</button><button aria-pressed="${basis === 'period'}" data-v="period">Choraklik/oylik</button></div>` : ''}
      <button class="btn" id="dAsk">💬 Chatda so'rash</button></div>
    <div id="dChart"></div><div id="dTable"></div>
    ${(d.metadata['Calculation methodology'] || []).length ? `<h4 style="margin:22px 0 6px;color:var(--faint)">Metodologiya</h4><p style="color:var(--muted);font-size:13.5px">${esc(d.metadata['Calculation methodology'][0])}</p>` : ''}
    ${(d.metadata['Note'] || []).length ? `<p style="color:var(--muted);font-size:13px">${esc(d.metadata['Note'][0].slice(0, 400))}</p>` : ''}`;
  $('.close', drawer).onclick = closeDrawer;
  $('#dAsk').onclick = () => { closeDrawer(); ask("so'nggi qiymati", { indicator_id: id }, `«${d.name}» — so'nggi qiymat`); };
  $('#dMember')?.addEventListener('change', e => openDrawer(id, e.target.value, basis));
  $('#dBasis')?.addEventListener('click', e => { const b = e.target.closest('button'); if (b) openDrawer(id, $('#dMember')?.value || region, b.dataset.v); });
  if (ser && ser.points) {
    lineChart($('#dChart'), ser.points, { unit: d.unit, h: 250 });
    const rows = [...ser.points].reverse().slice(0, 16);
    $('#dTable').innerHTML = `<table class="data-table"><tr><th>Davr</th><th>Qiymat</th></tr>${rows.map(r => `<tr><td>${periodHuman(r.period)}</td><td class="num">${fmtUz(r.value)}</td></tr>`).join('')}</table>
      <p style="margin-top:12px"><button class="btn sec" id="dCsv">⬇ CSV yuklab olish</button></p>`;
    $('#dCsv').onclick = () => downloadCsv({ type: 'series', points: ser.points });
  } else $('#dChart').innerHTML = '<p style="color:var(--muted)">Bu hudud uchun qiymat yo\'q.</p>';
}

/* ---------- boot ---------- */
async function boot() {
  renderHistory();
  try {
    const [h, o] = await Promise.all([api('/api/health'), api('/api/overview')]);
    S.overview = o; $('#dot').classList.add('on'); $('#statusText').textContent = `Baza ulangan · ${fmtUz(h.indicators)} ko'rsatkich`;
    $('#examples').innerHTML = o.examples.map(e => `<button class="chip" data-q="${esc(e.text)}"><span>${e.icon}</span>${esc(e.text)}</button>`).join('');
    $('#examples').addEventListener('click', e => { const b = e.target.closest('[data-q]'); if (b) ask(b.dataset.q); });
    $('#stats').innerHTML = [[o.indicators, "ko'rsatkich"], [o.observations, "ta qiymat"], [o.regions, 'hudud (viloyat)'], [`${o.first_year}–${o.last_year}`, 'yillar qamrovi']]
      .map(([v, l]) => `<div class="stat"><b class="num" data-v="${typeof v === 'number' ? v : ''}">${typeof v === 'number' ? '0' : v}</b><span>${l}</span></div>`).join('');
    $$('#stats b[data-v]').forEach(b => { if (b.dataset.v) countUp(b, +b.dataset.v, fmtUz(+b.dataset.v), 1400); });
  } catch (e) { $('#statusText').textContent = 'Server bilan aloqa yo\'q'; }
  const v = location.hash.replace('#', ''); if (['catalog', 'about'].includes(v)) show(v);
  input.focus({ preventScroll: true });
}
boot();
})();
