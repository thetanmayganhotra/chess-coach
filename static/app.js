// Chess Coach front end. No build step, no libraries.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = async (path, body) => {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  return r.json();
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;
const wp = (cp) => 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * cp)) - 1);

// ------------------------------------------------------------------ board
const GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
const FILES = "abcdefgh";
function parseFen(fen) {
  const [pl, turn] = fen.split(" ");
  const sq = {};
  pl.split("/").forEach((row, i) => {
    let f = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) f += +ch;
      else { sq[FILES[f] + (8 - i)] = ch; f++; }
    }
  });
  return { sq, turn };
}
function applyVisual(fen, uci) { // just for showing my move instantly; server sends the real FEN
  const { sq, turn } = parseFen(fen);
  const a = uci.slice(0, 2), b = uci.slice(2, 4), promo = uci[4];
  const p = sq[a]; delete sq[a];
  if (p && p.toLowerCase() === "k" && Math.abs(FILES.indexOf(a[0]) - FILES.indexOf(b[0])) === 2) {
    const r = a[1], kside = b[0] === "g";
    const rf = (kside ? "h" : "a") + r, rt = (kside ? "f" : "d") + r;
    sq[rt] = sq[rf]; delete sq[rf];
  }
  if (p && p.toLowerCase() === "p" && a[0] !== b[0] && !sq[b]) delete sq[b[0] + a[1]];
  sq[b] = promo ? (turn === "w" ? promo.toUpperCase() : promo) : p;
  let rows = [];
  for (let r = 8; r >= 1; r--) {
    let row = "", e = 0;
    for (const f of FILES) { const x = sq[f + r]; if (x) { if (e) row += e; e = 0; row += x; } else e++; }
    if (e) row += e; rows.push(row);
  }
  return rows.join("/") + " " + (turn === "w" ? "b" : "w") + " - - 0 1";
}

class Board {
  constructor(el, opts = {}) {
    this.el = el; this.orientation = opts.orientation || "white"; this.onMove = opts.onMove; this.mini = opts.mini;
    this.fen = "8/8/8/8/8/8/8/8 w - - 0 1"; this.legal = []; this.last = null; this.arrows = []; this.sel = null; this.check = false;
    if (!this.mini) this._bind();
  }
  set(fen, o = {}) {
    this.fen = fen; this.legal = o.legal || []; this.last = o.last || null; this.arrows = o.arrows || [];
    this.check = !!o.check; this.sel = null;
    if (o.orientation) this.orientation = o.orientation;
    this.render();
  }
  flip() { this.orientation = this.orientation === "white" ? "black" : "white"; this.render(); }
  squares() {
    const out = [];
    for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++) {
      const r = this.orientation === "white" ? 8 - i : i + 1, f = this.orientation === "white" ? j : 7 - j;
      out.push(FILES[f] + r);
    }
    return out;
  }
  render() {
    const { sq, turn } = parseFen(this.fen);
    const targets = this.sel ? this.legal.filter((u) => u.startsWith(this.sel)).map((u) => u.slice(2, 4)) : [];
    const kingSq = this.check ? Object.keys(sq).find((s) => sq[s] === (turn === "w" ? "K" : "k")) : null;
    const list = this.squares();
    let h = `<div class="board ${this.mini ? "mini" : ""} ${this.legal.length ? "interactive" : ""}">`;
    list.forEach((s, idx) => {
      const light = (FILES.indexOf(s[0]) + +s[1]) % 2 === 1;
      const cls = ["sq", light ? "l" : "d"];
      if (this.last && (this.last.slice(0, 2) === s || this.last.slice(2, 4) === s)) cls.push("last");
      if (this.sel === s) cls.push("sel");
      if (targets.includes(s)) cls.push("tgt", sq[s] ? "cap" : "");
      if (kingSq === s) cls.push("chk");
      const p = sq[s];
      let inner = "";
      if (p) {
        const white = p === p.toUpperCase();
        const mine = this.legal.some((u) => u.startsWith(s));
        inner = `<span class="pc ${white ? "w" : "b"} ${mine ? "mine" : ""}">${GLYPH[p.toLowerCase()]}&#xFE0E;</span>`;
      }
      if (!this.mini) {
        if (idx >= 56) inner += `<span class="coord f">${s[0]}</span>`;
        if (idx % 8 === 0) inner += `<span class="coord r">${s[1]}</span>`;
      }
      h += `<div class="${cls.join(" ")}" data-sq="${s}">${inner}</div>`;
    });
    h += "</div>" + this._arrowSvg();
    this.el.innerHTML = h;
  }
  _xy(s) {
    let f = FILES.indexOf(s[0]), r = +s[1];
    return this.orientation === "white" ? [f + 0.5, 8 - r + 0.5] : [7 - f + 0.5, r - 1 + 0.5];
  }
  _arrowSvg() {
    if (!this.arrows.length) return "";
    let defs = "", body = "";
    this.arrows.forEach((a, i) => {
      const [x1, y1] = this._xy(a.uci.slice(0, 2)), [x2, y2] = this._xy(a.uci.slice(2, 4));
      const dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy), ex = x2 - (dx / L) * 0.32, ey = y2 - (dy / L) * 0.32;
      defs += `<marker id="ah${i}${this.el.id}" markerWidth="3" markerHeight="3" refX="1.4" refY="1.5" orient="auto"><path d="M0,0 L3,1.5 L0,3 z" fill="${a.color}"/></marker>`;
      body += `<line x1="${x1}" y1="${y1}" x2="${ex}" y2="${ey}" stroke="${a.color}" stroke-width="0.17" stroke-linecap="round" opacity="0.85" marker-end="url(#ah${i}${this.el.id})"/>`;
    });
    return `<svg class="arrows" viewBox="0 0 8 8"><defs>${defs}</defs>${body}</svg>`;
  }
  _sqAt(x, y) {
    const el = document.elementFromPoint(x, y);
    const s = el && el.closest(".sq");
    return s && this.el.contains(s) ? s.dataset.sq : null;
  }
  _try(from, to) {
    const cands = this.legal.filter((u) => u.startsWith(from + to));
    if (!cands.length) return false;
    const uci = cands.find((u) => u.endsWith("q")) || cands[0]; // auto-queen
    this.sel = null; this.legal = [];
    this.onMove && this.onMove(uci);
    return true;
  }
  _bind() {
    let ghost = null, dragFrom = null;
    this.el.addEventListener("pointerdown", (e) => {
      const s = this._sqAt(e.clientX, e.clientY);
      if (!s || !this.legal.length) return;
      if (this.sel && this.sel !== s && this._try(this.sel, s)) return;
      if (this.legal.some((u) => u.startsWith(s))) {
        this.sel = s; this.render(); dragFrom = s;
        const pc = this.el.querySelector(`[data-sq="${s}"] .pc`);
        if (pc) {
          ghost = pc.cloneNode(true); ghost.classList.add("drag-ghost");
          ghost.style.fontSize = getComputedStyle(pc).fontSize;
          document.body.appendChild(ghost); pc.style.opacity = 0.35;
          ghost.style.left = e.clientX + "px"; ghost.style.top = e.clientY + "px";
        }
        this.el.setPointerCapture(e.pointerId);
      } else { this.sel = null; this.render(); }
    });
    this.el.addEventListener("pointermove", (e) => {
      if (ghost) { ghost.style.left = e.clientX + "px"; ghost.style.top = e.clientY + "px"; }
    });
    this.el.addEventListener("pointerup", (e) => {
      if (ghost) { ghost.remove(); ghost = null; }
      const to = this._sqAt(e.clientX, e.clientY);
      if (dragFrom && to && to !== dragFrom && this._try(dragFrom, to)) { dragFrom = null; return; }
      dragFrom = null; this.render();
    });
  }
}

// ------------------------------------------------------------------ tabs
const loaded = {};
function show(tab) {
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  $$(".tab").forEach((t) => t.classList.toggle("on", t.id === tab));
  location.hash = tab;
  if (!loaded[tab]) { loaded[tab] = true; ({ dashboard, puzzles, openings, review, plan, play })[tab](); }
}
$$("#nav a").forEach((a) => a.addEventListener("click", () => show(a.dataset.tab)));

// ------------------------------------------------------------------ line chart
function lineChart(el, series, o = {}) {
  const W = 760, H = o.h || 260, P = { l: 40, r: 12, t: 12, b: 26 };
  const pts = series.flatMap((s) => s.points);
  if (!pts.length) { el.innerHTML = '<p class="muted">No data yet.</p>'; return; }
  let x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]));
  let y0 = Math.min(...pts.map((p) => p[1])), y1 = Math.max(...pts.map((p) => p[1]));
  if (o.yMin != null) y0 = Math.min(y0, o.yMin); if (o.yMax != null) y1 = Math.max(y1, o.yMax);
  const pad = (y1 - y0) * 0.08 || 10; y0 -= pad; y1 += pad; if (x1 === x0) x1 = x0 + 1;
  const X = (x) => P.l + ((x - x0) / (x1 - x0)) * (W - P.l - P.r), Y = (y) => H - P.b - ((y - y0) / (y1 - y0)) * (H - P.t - P.b);
  let g = "";
  const step = Math.pow(10, Math.floor(Math.log10(y1 - y0))) * ((y1 - y0) / Math.pow(10, Math.floor(Math.log10(y1 - y0))) > 4 ? 1 : 0.5);
  for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step)
    g += `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(y)}" y2="${Y(y)}" stroke="var(--line)"/><text x="${P.l - 6}" y="${Y(y) + 4}" text-anchor="end">${Math.round(y)}</text>`;
  const ticks = 6;
  for (let i = 0; i <= ticks; i++) {
    const t = x0 + ((x1 - x0) * i) / ticks;
    const lbl = o.xLabel ? o.xLabel(t) : new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    g += `<text x="${X(t)}" y="${H - 6}" text-anchor="middle">${lbl}</text>`;
  }
  for (const s of series) {
    if (!s.points.length) continue;
    const d = s.points.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join("");
    g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""} stroke-linejoin="round"/>`;
    if (s.dots) s.points.forEach((p) => g += `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="3.5" fill="${s.color}"/>`);
    if (s.label) { const p = s.points[s.points.length - 1]; g += `<text x="${X(p[0]) - 4}" y="${Y(p[1]) - 8}" text-anchor="end" style="fill:${s.color};font-weight:700">${s.label}</text>`; }
  }
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}">${g}</svg>`;
}

// ------------------------------------------------------------------ dashboard
let DASH;
async function dashboard() {
  const d = DASH = await api("/api/dashboard");
  const tcs = ["rapid", "blitz", "bullet", "daily"];
  $("#ratingCards").innerHTML = tcs.filter((t) => d.ratings[t]).map((t) => {
    const r = d.ratings[t], b = d.by_time_class[t] || {};
    return `<div class="card ${t === "rapid" ? "main" : ""}"><div class="k">${t}${t === "rapid" ? " · your best" : ""}</div>
      <div class="v">${r.rating}</div><div class="s">peak ${r.best ?? "—"} · ${b.games ?? 0} games · scores ${b.score_pct ?? "—"}%</div></div>`;
  }).join("");
  const COL = { rapid: "var(--accent)", blitz: "var(--warn)", bullet: "var(--bad)", daily: "var(--info)" };
  let cur = "rapid";
  const draw = () => {
    $("#chartSeg").innerHTML = ["rapid", "blitz", "bullet", "all"].map((t) => `<button class="${t === cur ? "on" : ""}" data-t="${t}">${t}</button>`).join("");
    $$("#chartSeg button").forEach((b) => b.onclick = () => { cur = b.dataset.t; draw(); });
    const keys = cur === "all" ? ["rapid", "blitz", "bullet"] : [cur];
    lineChart($("#ratingChart"), keys.map((k) => ({ points: d.history[k] || [], color: COL[k], label: cur === "all" ? k : null })));
  };
  draw();

  const p = d.progress, L = p.last_30 || {}, Pv = p.prev_30 || {};
  const rows = [["Games", "games", 0], ["Score %", "score", 1], ["Accuracy", "accuracy", 1], ["Blunders / game", "blunders_per_game", -1],
                ["Abandon %", "abandon_pct", -1], ["Lost on time %", "timeout_pct", -1]];
  $("#progressTable").innerHTML = `<tr><th></th><th>Prev 30d</th><th>Last 30d</th><th></th></tr>` + rows.map(([n, k, dir]) => {
    const a = Pv[k], b = L[k];
    let arrow = "";
    if (dir && a != null && b != null && a !== b) { const good = (b - a) * dir > 0; arrow = `<span class="${good ? "up" : "down"}">${good ? "▲ better" : "▼ worse"}</span>`; }
    return `<tr><td>${n}</td><td>${a ?? "—"}</td><td><b>${b ?? "—"}</b></td><td>${arrow}</td></tr>`;
  }).join("");

  const max = Math.max(...d.weaknesses.map((w) => w.cost));
  $("#weakList").innerHTML = d.weaknesses.map((w, i) => `<div class="weak"><div class="n">${i + 1}</div>
    <div><div class="t">${esc(w.name)}</div><div class="d">${esc(w.what)} ${esc(cap(w.detail))}.</div><div class="fix"><b>Fix:</b> ${esc(w.fix)}</div>
    <div class="bar"><div style="width:${(100 * w.cost) / max}%"></div></div></div>
    <div class="c">−${w.cost.toLocaleString()}<div class="muted small" style="font-weight:400">rating pts</div></div></div>`).join("");

  const a = d.abandoned, st = a.state_when_left;
  const lost = d.abandoned.rating_points_lost_actual;
  const playable = st["winning (>=70%)"] + st["equal"];
  $("#abandoned").innerHTML = `
    <div class="stats">
      <div class="stat"><div class="v">${a.count}</div><div class="k">games abandoned</div></div>
      <div class="stat"><div class="v">${Math.round(a.pct_of_losses)}%</div><div class="k">of all your losses</div></div>
      <div class="stat"><div class="v">−${lost.toLocaleString()}</div><div class="k">rating pts lost in them</div></div>
      <div class="stat"><div class="v">${st["winning (>=70%)"]}</div><div class="k">left while WINNING</div></div>
      <div class="stat"><div class="v">${st["equal"]}</div><div class="k">left while equal</div></div>
      <div class="stat"><div class="v">${st["losing (<=30%)"]}</div><div class="k">left while losing</div></div>
      <div class="stat"><div class="v">${a.left_right_after_a_blunder}</div><div class="k">within 3 moves of a blunder</div></div>
      <div class="stat"><div class="v">${a.clock_left_when_left[">75%"]}</div><div class="k">left with >75% of clock</div></div>
    </div>
    <div class="grid2" style="margin-top:14px">
      <div><h3>By time of day</h3><div id="abHours"></div></div>
      <div><h3>By speed</h3><div class="hbars">${Object.entries(a.abandon_rate_by_time_class).map(([k, v]) =>
        `<div class="hb"><span>${cap(k)}</span><div class="track"><div style="width:${v * 3}%"></div></div><span>${v}%</span></div>`).join("")}</div>
        <h3 style="margin-top:12px">Losses in a row before leaving</h3><div class="hbars">${Object.entries(a.losses_in_a_row_before).map(([k, v]) =>
        `<div class="hb"><span>${k === "4" ? "4+" : k}</span><div class="track"><div style="width:${(v / a.count) * 100}%"></div></div><span>${v}</span></div>`).join("")}</div></div>
    </div>
    <div class="verdict"><b>What this says:</b> it's not tilt. ${a.losses_in_a_row_before["0"]} of ${a.count} came with no loss before them, and the time of day hardly matters.
      It's that <b>you leave as soon as a game turns against you</b>, usually early (${a.moves_played["6-20"]} between moves 6 and 20) with most of your clock left.
      But ${playable} of those games were level or winning when you left, and when you play on from a losing position you still come back about a third of the time.
      At your level, the other player blunders back constantly. ${L.abandon_pct ? `In the last 30 days you abandoned <b>${L.abandon_pct}%</b> of games, so it's getting worse, not better.` : ""}
      Rule from now on: <b>never leave. Resign if you must.</b> This is the cheapest rating you'll ever gain.</div>`;
  const hrs = Array.from({ length: 24 }, (_, h) => a.by_hour[h] || 0), hm = Math.max(...hrs);
  $("#abHours").innerHTML = `<svg viewBox="0 0 480 110" style="width:100%">${hrs.map((v, h) =>
    `<rect x="${h * 20 + 2}" y="${90 - (v / hm) * 80}" width="16" height="${(v / hm) * 80}" rx="2" fill="var(--warn)"/>` +
    (h % 3 === 0 ? `<text x="${h * 20 + 10}" y="106" text-anchor="middle" style="fill:var(--muted);font-size:10px">${h}h</text>` : "")).join("")}</svg>`;

  const lp = d.loss_phase, lt = Object.values(lp).reduce((x, y) => x + y, 0);
  $("#phases").innerHTML = `<p class="muted small">The phase where the deciding mistake happened in games you lost (from a position that wasn't already lost).</p><div class="hbars">` +
    ["opening", "middlegame", "endgame"].map((k) => `<div class="hb"><span>${cap(k)}</span><div class="track"><div style="width:${(100 * lp[k]) / lt}%"></div></div><span>${Math.round((100 * lp[k]) / lt)}%</span></div>`).join("") +
    `</div><p class="small">Lost on time: <b>${d.timeouts.lost_on_time}</b> games, <b>${d.timeouts.while_winning}</b> of them while winning.</p>`;
  const t = d.tilt;
  const tr = (n, s) => `<div class="hb"><span>${n}</span><div class="track"><div style="width:${s.score_pct}%;background:${s.score_pct < 45 ? "var(--bad)" : "var(--ok)"}"></div></div><span>${s.score_pct}%</span></div>`;
  $("#tilt").innerHTML = `<p class="muted small">Your score, depending on what happened just before.</p><div class="hbars">` +
    tr("After a win", t.after_win) + tr("After a loss", t.after_loss) + tr("After 2 losses", t.by_loss_streak["2"]) +
    tr("Games 1–3 of session", t.by_session_game["1-3"]) + tr("Games 7–10", t.by_session_game["7-10"]) + tr("Late night (23–6)", t.by_hour["late night 23-6"]) + `</div>`;
  showLastSync(d.last_sync);
}

// ------------------------------------------------------------------ sync
function showLastSync(ls) { if (ls) $("#syncStatus").textContent = `last sync ${new Date(ls.ts * 1000).toLocaleString()} · ${ls.new_games} new`; }
$("#syncBtn").onclick = async () => {
  await api("/api/sync", {});
  $("#syncBtn").disabled = true;
  const poll = async () => {
    const s = await api("/api/sync");
    $("#syncStatus").textContent = s.last || "syncing…";
    if (s.running) return setTimeout(poll, 1500);
    $("#syncBtn").disabled = false;
    if (s.failed) { $("#syncStatus").textContent = "Sync failed: " + (s.last || "see sync.log"); return; }
    Object.keys(loaded).forEach((k) => delete loaded[k]);
    show(location.hash.slice(1) || "dashboard");
  };
  poll();
};

// ------------------------------------------------------------------ puzzles
let PZ, pzBoard;
const THEME_HINT = {
  "hung moved piece": "Which squares are actually safe for your pieces?", "left piece hanging": "Something of yours is under attack. What?",
  fork: "Watch out for a piece that can hit two of yours at once.", "missed free material": "Is anything of theirs undefended?",
  "allowed mate": "Look at every check your opponent could play.", "missed mate": "Checks first. There's a forced mate.",
  "back-rank mate": "Think about your back rank.", pin: "Look for pieces lined up with a king or queen.",
  "missed opponent tactic": "What does your opponent threaten right now?", "missed own tactic": "You have a check or capture that wins something.",
  "skewer / check tactic": "Look at checks along a line.", "positional / slow move": "No tactic, just find the most useful move.",
};
async function puzzles() {
  pzBoard = new Board($("#pzBoard"), { onMove: pzMove });
  const th = await api("/api/puzzle/themes");
  $("#pzTheme").innerHTML += th.map((t) => `<option value="${esc(t.theme)}">${esc(cap(t.theme))} (${t.n})</option>`).join("");
  $("#pzTheme").onchange = pzNext;
  $("#pzNext").onclick = pzNext;
  $("#pzHint").onclick = () => { if (PZ) $("#pzFeedback").innerHTML = `<div class="fb">💡 ${esc(THEME_HINT[PZ.theme] || "Checks, captures, threats.")}</div>`; };
  $("#pzGiveUp").onclick = () => { if (PZ && !PZ.done) pzMove(null); };
  pzNext();
}
async function pzNext() {
  PZ = await api("/api/puzzle?theme=" + encodeURIComponent($("#pzTheme").value));
  if (PZ.empty) { $("#pzPrompt").textContent = "No puzzles yet. Hit Sync."; return; }
  pzBoard.set(PZ.fen, { legal: PZ.legal, orientation: PZ.turn });
  $("#pzTitle").textContent = PZ.kind === "missed win" ? "Your opponent just blundered. Punish it!" : "You blundered here. Find a better move.";
  $("#pzContext").innerHTML = `${esc(PZ.context)} · <a href="${PZ.url}" target="_blank" rel="noopener">game</a>${PZ.is_review ? ` · <span class="tag">review${PZ.lapses ? `, missed ${PZ.lapses}×` : ""}</span>` : ' · <span class="tag">new</span>'}`;
  $("#pzPrompt").textContent = `${cap(PZ.turn)} to move.`;
  $("#pzFeedback").innerHTML = "";
  pzStats(PZ.stats);
}
function pzStats(s) {
  $("#pzStats").innerHTML = [["due now", s.due], ["new", s.fresh], ["learned", s.learned], ["done today", s.done_today], ["recent hit rate", s.recent_accuracy != null ? s.recent_accuracy + "%" : "—"]]
    .map(([k, v]) => `<div class="stat"><div class="v">${v ?? 0}</div><div class="k">${k}</div></div>`).join("");
}
async function pzMove(uci) {
  if (PZ.done) return; PZ.done = true;
  const giveUp = !uci;
  if (uci) pzBoard.set(applyVisual(PZ.fen, uci), { last: uci });
  $("#pzFeedback").innerHTML = '<div class="fb muted">Checking…</div>';
  const r = await api("/api/puzzle", { game_id: PZ.game_id, ply: PZ.ply, uci });
  const correct = r.correct;
  const arrows = [{ uci: r.played_uci, color: "#c2412d" }, { uci: r.best_uci, color: "#2f8a55" }];
  pzBoard.set(PZ.fen, { arrows, orientation: PZ.turn });
  const ex = r.explain || {};
  $("#pzFeedback").innerHTML = `<div class="fb ${correct ? "ok" : "no"}">
    <div class="prompt">${correct ? (uci === r.best_uci ? "✓ Exactly right" : `✓ Good. ${esc(r.your_san)} works too (engine's top move: ${esc(r.best_san)})`) : giveUp ? `Answer: ${esc(r.best_san)}` : `✗ ${esc(r.your_san)} isn't it. Best was ${esc(r.best_san)}`}</div>
    <div>In the game you played <b>${esc(r.played_in_game)}</b>. ${esc(ex.text || "")}</div>
    ${ex.tip ? `<div class="tip"><b>Next time:</b> ${esc(ex.tip)}</div>` : ""}
    <div class="muted small" style="margin-top:6px">${correct ? `Comes back in ${r.next_in_days} day${r.next_in_days === 1 ? "" : "s"}.` : "This one comes back in 10 minutes."}</div></div>`;
  pzStats(r.stats);
}

// ------------------------------------------------------------------ openings
let drillBoard, DR;
async function openings() {
  const r = await api("/api/repertoire");
  const card = (title, x) => `<div class="panel rep"><h3>${title}</h3>
    <div class="pick">${esc(x.pick)}</div><p class="small muted">${esc(x.why)}</p>
    <div><span class="big">${x.stats.score}%</span> <span class="muted small">score in ${x.stats.games} games</span></div>
    <ul>${x.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
    <h3 style="margin-top:10px">Your most-played lines</h3>
    <div class="lines">${x.lines.map((l) => `<div><span>${esc(l.line)}</span><span class="${l.score >= 50 ? "up" : "down"}">${l.score}% · ${l.games}</span></div>`).join("")}</div></div>`;
  $("#repCards").innerHTML = card("As White", r.white) + card("As Black vs 1.e4", r.black_e4) + card("As Black vs 1.d4", r.black_d4);
  $("#trouble").innerHTML = r.trouble.map((t, i) => `<div class="tcard" data-i="${i}"><div class="board-wrap" id="tb${i}"></div>
    <div class="ln">${esc(t.line)}</div>
    <div class="mv">You: <span class="bad">${esc(t.played)}</span> ×${t.times} → better <span class="good">${esc(t.best || "?")}</span></div>
    <div class="muted small">${esc(t.theme || "")}</div></div>`).join("");
  r.trouble.forEach((t, i) => new Board($("#tb" + i), { mini: true, orientation: t.color }).set(t.fen, { arrows: t.best_uci ? [{ uci: t.best_uci, color: "#2f8a55" }] : [] }));
  drillBoard = new Board($("#drillBoard"), { onMove: drillMove });
  $$(".tcard").forEach((c) => c.onclick = async () => {
    const t = r.trouble[+c.dataset.i]; DR = t;
    $("#drillPanel").hidden = false;
    const lg = await api("/api/legal", { fen: t.fen });
    drillBoard.set(t.fen, { legal: lg.legal, orientation: t.color });
    $("#drillText").textContent = `${t.line}. You've played ${t.played} here ${t.times} times. What's better?`;
    $("#drillFb").innerHTML = "";
    $("#drillPanel").scrollIntoView({ behavior: "smooth" });
  });
  $("#drillClose").onclick = () => $("#drillPanel").hidden = true;
}
async function drillMove(uci) {
  const ok = uci === DR.best_uci;
  drillBoard.set(DR.fen, { arrows: [{ uci: DR.best_uci, color: "#2f8a55" }], orientation: DR.color });
  const ex = await api(`/api/game/${DR.game_id}`);
  const m = ex.moves.find((x) => x.ply === DR.ply);
  $("#drillFb").innerHTML = `<div class="fb ${ok ? "ok" : "no"}"><div class="prompt">${ok ? "✓ That's it: " : "Not quite. Play "}${esc(DR.best)}</div>
    ${m && m.explain ? `<div>${esc(m.explain.text)}</div>${m.explain.tip ? `<div class="tip"><b>Next time:</b> ${esc(m.explain.tip)}</div>` : ""}` : ""}</div>
    <button class="btn ghost" onclick="drillRetry()">Try again</button>`;
}
async function drillRetry() { const lg = await api("/api/legal", { fen: DR.fen }); drillBoard.set(DR.fen, { legal: lg.legal, orientation: DR.color }); $("#drillFb").innerHTML = ""; }

// ------------------------------------------------------------------ review
let RV = null, rvBoard, rvIdx = -1;
async function review() {
  rvBoard = new Board($("#rvBoard"));
  ["fResult", "fTc", "fAb"].forEach((id) => $("#" + id).onchange = loadGames);
  $$("[data-nav]").forEach((b) => b.onclick = () => nav(b.dataset.nav));
  document.addEventListener("keydown", (e) => {
    if (!$("#review").classList.contains("on") || !RV) return;
    if (e.key === "ArrowLeft") { nav("prev"); e.preventDefault(); }
    if (e.key === "ArrowRight") { nav("next"); e.preventDefault(); }
  });
  loadGames();
}
async function loadGames() {
  const q = new URLSearchParams({ result: $("#fResult").value, time_class: $("#fTc").value, abandoned: $("#fAb").checked ? 1 : "" });
  const gs = await api("/api/games?" + q);
  $("#gameList").innerHTML = gs.map((g) => `<div class="gi" data-id="${g.id}"><span class="dot ${g.result}"></span>
    <div><div class="op">${esc(g.opp_name)} (${g.opp_rating})</div><div class="meta">${g.date.slice(0, 10)} · ${g.time_class} · ${g.color} · ${esc(g.termination)}</div></div>
    <div class="meta" style="text-align:right">${g.accuracy ?? "—"}%<br>${g.blunders ? `<span class="down">${g.blunders}?? </span>` : ""}</div></div>`).join("") || '<p class="muted">No games.</p>';
  $$(".gi").forEach((el) => el.onclick = () => openGame(el.dataset.id));
}
async function openGame(id) {
  $$(".gi").forEach((el) => el.classList.toggle("on", el.dataset.id === id));
  RV = await api("/api/game/" + id);
  const g = RV.game, s = RV.stats || {};
  $("#gvHead").innerHTML = `<div class="panel-h"><h2>${g.color === "white" ? "You" : esc(g.opp_name)} vs ${g.color === "black" ? "You" : esc(g.opp_name)}</h2>
    <a href="${g.url}" target="_blank" rel="noopener" class="small">open on chess.com</a></div>
    <div class="small"><span class="tag">${g.result.toUpperCase()} · ${esc(g.termination)}</span><span class="tag">${g.time_class}</span>
    <span class="tag">${esc(g.opening || g.eco || "")}</span> You ${g.my_rating} vs ${g.opp_rating}.
    Accuracy <b>${s.accuracy ?? "—"}%</b> · <span class="down">${s.blunders ?? 0} blunders</span> · ${s.mistakes ?? 0} mistakes · ${s.inaccuracies ?? 0} inaccuracies</div>`;
  rvBoard.orientation = g.color;
  // move list
  let h = "";
  RV.moves.forEach((m, i) => {
    if (i % 2 === 0) h += `<div class="no">${i / 2 + 1}.</div>`;
    const mark = m.cls === "blunder" ? "??" : m.cls === "mistake" ? "?" : m.cls === "inaccuracy" ? "?!" : "";
    h += `<div class="m ${m.cls || ""}" data-i="${i}">${esc(m.san)}${mark}${m.missed_win ? " ⚑" : ""}</div>`;
  });
  if (RV.moves.length % 2) h += "<div></div>";
  $("#moveList").innerHTML = h;
  $$("#moveList .m").forEach((el) => el.onclick = () => goto(+el.dataset.i));
  evalGraph();
  goto(-1);
}
function evalGraph() {
  const W = 600, H = 90, n = RV.moves.length;
  const y = (cp) => H / 2 - ((wp(cp ?? 0) - 50) / 50) * (H / 2 - 4);
  const pts = RV.moves.map((m, i) => [(i + 1) * (W / n), y(m.cp_after)]);
  let d = `M0,${H / 2} ` + pts.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") + ` L${W},${H / 2} Z`;
  let dots = RV.moves.map((m, i) => m.mine && (m.cls === "blunder" || m.cls === "mistake") ?
    `<circle cx="${pts[i][0]}" cy="${pts[i][1]}" r="3.5" fill="${m.cls === "blunder" ? "var(--bad)" : "var(--warn)"}"/>` : "").join("");
  $("#evalGraph").innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><rect width="${W}" height="${H}" fill="#333" rx="4"/>
    <path d="${d}" fill="#eee"/><line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="#888" stroke-dasharray="3 3"/>${dots}
    <line id="egCur" x1="0" x2="0" y1="0" y2="${H}" stroke="var(--accent)" stroke-width="2"/></svg>`;
  $("#evalGraph svg").onclick = (e) => { const r = e.currentTarget.getBoundingClientRect(); goto(Math.round(((e.clientX - r.left) / r.width) * n) - 1); };
}
function nav(k) {
  if (!RV) return;
  const n = RV.moves.length;
  if (k === "start") goto(-1); if (k === "end") goto(n - 1);
  if (k === "prev") goto(Math.max(-1, rvIdx - 1)); if (k === "next") goto(Math.min(n - 1, rvIdx + 1));
  if (k === "nextErr") { const j = RV.moves.findIndex((m, i) => i > rvIdx && m.explain && m.cls !== "inaccuracy"); if (j >= 0) goto(j); }
}
function goto(i) {
  rvIdx = i;
  const m = RV.moves[i];
  const fen = m ? m.fen_after : (RV.moves[0]?.fen_before || "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  const arrows = m && m.explain && m.best_uci ? [{ uci: m.best_uci, color: "#2f8a55" }] : [];
  // show the position BEFORE my bad move with the better move drawn, so it's clear what I should have done
  if (arrows.length) rvBoard.set(m.fen_before, { last: m.uci, arrows: [{ uci: m.uci, color: "#c2412d" }, ...arrows] });
  else rvBoard.set(fen, { last: m?.uci });
  const cp = m ? m.cp_after : 0;
  $("#evalBar > div").style.height = wp(cp ?? 0) + "%";
  if (rvBoard.orientation === "black") $("#evalBar").style.transform = "rotate(180deg)"; else $("#evalBar").style.transform = "";
  $$("#moveList .m").forEach((el) => el.classList.toggle("cur", +el.dataset.i === i));
  const cur = $(`#moveList .m[data-i="${i}"]`); cur && cur.scrollIntoView({ block: "nearest" });
  const n = RV.moves.length; const ln = $("#egCur"); if (ln) { ln.setAttribute("x1", ((i + 1) * 600) / n); ln.setAttribute("x2", ((i + 1) * 600) / n); }
  const evalTxt = (c) => c == null ? "" : Math.abs(c) >= 9000 ? (c > 0 ? "White mates" : "Black mates") : (c > 0 ? "+" : "") + (c / 100).toFixed(1);
  if (!m) { $("#rvExplain").innerHTML = '<p class="muted">Start position. Use ▶ or "Next mistake".</p>'; return; }
  const who = m.mine ? "You" : "Opponent";
  let h = `<div class="small muted">${Math.floor(i / 2) + 1}${i % 2 ? "..." : "."} ${esc(m.san)} · ${who} · eval ${evalTxt(cp)}</div>`;
  if (m.explain) {
    const e = m.explain;
    h += `<div style="margin:6px 0">${e.missed_win ? '<span class="tag missed">missed win</span>' : ""}${e.cls ? `<span class="tag ${e.cls}">${e.cls}</span>` : ""}${e.theme ? `<span class="tag">${esc(e.theme)}</span>` : ""}</div>
      <div>${esc(e.text)}</div>${e.tip ? `<div class="fb" style="margin-top:8px"><b>What to look for:</b> ${esc(e.tip)}</div>` : ""}
      <div class="muted small">Red arrow: what you played. Green: what was better.</div>`;
  } else if (m.mine) {
    h += `<div class="up" style="margin-top:6px">${m.win_before - m.win_after < 3 ? "Good move." : "Fine."}</div>`;
  }
  $("#rvExplain").innerHTML = h;
}

// ------------------------------------------------------------------ plan
async function plan() {
  const p = await api("/api/plan");
  $("#rules").innerHTML = p.rules.map((r) => `<li><b>${esc(r.rule)}</b><span>${esc(r.why)}</span></li>`).join("");
  $("#daily").innerHTML = p.daily.map((d) => `<li>${esc(d)}</li>`).join("");
  $("#week").innerHTML = p.week.map((w) => `<tr><td>${w.day}</td><td>${esc(w.focus)}</td><td class="muted">${esc(w.play)}</td></tr>`).join("");
  const ms = p.milestones.months;
  const now = Date.now() / 1000;
  const t = (s) => new Date(s + "-15").getTime() / 1000;
  $("#msNote").textContent = `Starting from ${p.milestones.start} rapid. Steep at first (stopping the abandons and timeouts is quick, cheap rating), then about +70 a month. That's roughly ${ms.length} months to 1500. Ambitious but realistic if you stick to the rules and the daily puzzles.`;
  const hist = (DASH?.history?.rapid || (await api("/api/dashboard")).history.rapid || []).filter((x) => x[0] > now - 86400 * 120);
  lineChart($("#msChart"), [
    { points: [[now, p.milestones.start], ...ms.map((m) => [t(m.month), m.target])], color: "var(--muted)", dash: "6 5", dots: true, label: "target" },
    { points: hist, color: "var(--accent)", width: 2.5, label: "you" },
  ], { h: 240, yMax: 1500, xLabel: (s) => new Date(s * 1000).toLocaleDateString(undefined, { month: "short", year: "2-digit" }) });
  $("#msTable").innerHTML = ms.map((m) => `<div><span class="muted">${new Date(m.month + "-15").toLocaleDateString(undefined, { month: "short", year: "numeric" })}</span>
    <b>${m.target}</b>${m.actual != null ? `<span class="${m.actual >= m.target ? "up" : "down"}">now ${m.actual}</span>` : ""}</div>`).join("");
}

// ------------------------------------------------------------------ play vs yourself
let botBoard, BG = null;
function play() {
  botBoard = new Board($("#botBoard"), { onMove: botMove });
  botBoard.set("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  $("#botNew").onclick = botNew; $("#botFlip").onclick = () => botBoard.flip();
  $("#botUndo").onclick = () => {
    if (!BG || BG.hist.length < 2) return;
    const e = BG.hist.pop(); BG.sans.splice(BG.sans.length - e.k, e.k);
    const h = BG.hist[BG.hist.length - 1]; BG.over = null;
    if (!h.legal.length) return botNew(); // back to a start where the bot moves first
    botBoard.set(h.fen, { legal: h.legal, last: h.last }); botMovesList(); $("#botStatus").textContent = "Your move."; $("#botWhy").textContent = "";
  };
}
async function botNew() {
  const color = $("#botColor").value;
  BG = { color, strength: +$("#botStrength").value, hist: [], sans: [], over: null };
  const start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  botBoard.orientation = color;
  if (color === "white") {
    const lg = await api("/api/legal", { fen: start });
    BG.hist.push({ fen: start, legal: lg.legal, last: null });
    botBoard.set(start, { legal: lg.legal }); $("#botStatus").textContent = "Your move."; $("#botWhy").textContent = "";
  } else {
    BG.hist.push({ fen: start, legal: [], last: null });
    $("#botStatus").textContent = "Bot-you is thinking…";
    await botStep(start, null);
  }
  botMovesList();
}
async function botMove(uci) {
  if (!BG || BG.over) return;
  const fen = BG.hist[BG.hist.length - 1].fen;
  botBoard.set(applyVisual(fen, uci), { last: uci });
  $("#botStatus").textContent = "Bot-you is thinking…";
  await botStep(fen, uci);
}
async function botStep(fen, uci) {
  const r = await api("/api/bot", { fen, uci, strength: BG.strength });
  if (r.error) { const h = BG.hist[BG.hist.length - 1]; botBoard.set(h.fen, { legal: h.legal, last: h.last }); return; }
  if (r.your_san) { BG.sans.push(r.your_san); }
  if (r.reply) { BG.sans.push(r.reply.san); }
  BG.hist.push({ fen: r.fen, legal: r.legal, last: r.reply ? r.reply.uci : uci, k: (r.your_san ? 1 : 0) + (r.reply ? 1 : 0) });
  botBoard.set(r.fen, { legal: r.over ? [] : r.legal, last: r.reply ? r.reply.uci : uci, check: r.check });
  $("#botWhy").textContent = r.reply ? `Bot played ${r.reply.san}. ${r.reply.why}` : "";
  if (r.over) { BG.over = r.over; $("#botStatus").textContent = `Game over: ${r.over.result} (${r.over.reason.replace(/_/g, " ")})`; }
  else $("#botStatus").textContent = r.check ? "Check! Your move." : "Your move.";
  botMovesList();
}
function botMovesList() {
  let h = ""; (BG?.sans || []).forEach((s, i) => { if (i % 2 === 0) h += `<div class="no">${i / 2 + 1}.</div>`; h += `<div class="m">${esc(s)}</div>`; });
  $("#botMoves").innerHTML = h;
}

show(location.hash.slice(1) || "dashboard");
