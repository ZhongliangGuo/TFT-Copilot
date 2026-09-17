/* TFT Copilot frontend */
"use strict";

const $ = (id) => document.getElementById(id);
const API = "";
let DATA = null;          // 静态数据
let SESSION = localStorage.getItem("tft_session") || null;

// ---------------- 局面状态 ----------------
const EMPTY = () => ({
  status: { stage: "", level: "", gold: "", hp: "", streak: "" },
  units: [],                 // {unit, star, items[], pos:[r,c]|null}
  pool: { component: [], craftable: [], artifact: [], support: [], radiant: [], emblem: [] },
  augments: [], pending: [],
  opp: { units: [], note: "", hp: null },
  shop: [],
  note: "",
});
let S = loadState();
let assignTarget = null;   // 待配装的棋子索引
let placeTarget = null;    // {list:'my'|'opp', idx} 待摆位置
let activeItemCat = "component";

function loadState() {
  try {
    const s = JSON.parse(localStorage.getItem("tft_state"));
    if (!s || !s.units) return EMPTY();
    // 迁移旧存档: 纹章原来单独存在 s.emblems, 现在并进 s.pool.emblem (跟其他装备一视同仁)
    s.pool = s.pool || {};
    s.pool.emblem = s.pool.emblem || [];
    if (Array.isArray(s.emblems) && s.emblems.length) {
      s.pool.emblem = s.pool.emblem.concat(s.emblems);
      delete s.emblems;
    }
    return s;
  } catch (e) {}
  return EMPTY();
}
function saveState() { localStorage.setItem("tft_state", JSON.stringify(S)); }

// ---------------- 初始化 ----------------
async function init() {
  DATA = await (await fetch(API + "/api/data")).json();
  $("data-version").textContent = `${T("数据", "Data")} ${DATA.version.built_at}`;
  ["stage", "level", "gold", "hp", "streak"].forEach((k) => {
    const el = $("st-" + k);
    el.value = S.status[k] || "";
    el.oninput = () => { S.status[k] = el.value; saveState(); };
  });
  $("free-note").value = S.note || "";
  $("free-note").oninput = () => { S.note = $("free-note").value; saveState(); };
  $("opp-note").value = S.opp.note || "";
  $("opp-note").oninput = () => { S.opp.note = $("opp-note").value; saveState(); };
  $("opp-toggle").onchange = () => { $("opp-area").style.display = $("opp-toggle").checked ? "" : "none"; };

  setupUnitSearch(); setupItemSearch(); setupAugSearch(); setupOppSearch();
  setupTabs(); setupChat(); setupApiKey();
  $("btn-new-game").onclick = newGame;
  $("btn-lang").onclick = () => setLang(LANG === "zh" ? "en" : "zh");
  renderAll();
  if (!SESSION) await newSessionOnly();
  $("sess-label").textContent = SESSION || "";
}

async function newSessionOnly() {
  const r = await (await fetch(API + "/api/session", { method: "POST" })).json();
  SESSION = r.session_id;
  localStorage.setItem("tft_session", SESSION);
  $("sess-label").textContent = SESSION;
}
async function newGame() {
  if (!confirm(T("新开一局? 会清空当前局面和对话。", "Start a new game? This clears the current board and chat."))) return;
  S = EMPTY(); saveState();
  $("chat-log").innerHTML = ""; $("target-board").style.display = "none";
  ["stage", "level", "gold", "hp", "streak"].forEach((k) => ($("st-" + k).value = ""));
  $("free-note").value = ""; $("opp-note").value = "";
  await newSessionOnly(); renderAll();
}

// ---------------- 搜索 ----------------
function matches(q, name) {
  q = q.toLowerCase();
  // name+" (" 让基础形态也能被 "拉克丝 (" 这类展开查询匹配到 (作为"未定形态"选项)
  if (name.toLowerCase().includes(q) || (name.toLowerCase() + " (").includes(q)) return true;
  const alts = DATA.search[name] || [];
  return alts.some((a) => a.includes(q));
}
function bindSearch(inputId, suggestId, sourceFn, onPick, renderOpt, browseFn) {
  const inp = $(inputId), box = $(suggestId);
  // 点面板内部不夺焦点, 输入框不 blur, 面板不消失 (onclick 仍会触发)
  box.addEventListener("mousedown", (e) => e.preventDefault());
  const refresh = () => {
    const q = inp.value.trim();
    box.innerHTML = "";
    if (!q) {
      if (browseFn && document.activeElement === inp) { browseFn(box, refresh, onPick); box.style.display = "block"; }
      else box.style.display = "none";
      return;
    }
    const hits = sourceFn(q).filter((e) => matches(q, e.name)).slice(0, 12);
    if (!hits.length) { box.style.display = "none"; return; }
    for (const e of hits) {
      const div = document.createElement("div");
      div.className = "opt";
      renderOpt(div, e);
      div.onclick = (ev) => {
        // onPick 返回 "keep" 表示它自己改写了输入框 (如展开多形态列表), 不清空
        if (onPick(e, ev) !== "keep") inp.value = "";
        refresh(); inp.focus();
      };
      box.appendChild(div);
    }
    box.style.display = "block";
  };
  inp.oninput = refresh;
  inp.onblur = () => setTimeout(() => { if (document.activeElement !== inp) box.style.display = "none"; }, 150);
  inp.onfocus = refresh;
  inp.addEventListener("keydown", (e) => { if (e.key === "Escape") { box.style.display = "none"; inp.blur(); } });
  return refresh;
}

// ---------- 浏览面板 (无输入时的二级菜单) ----------
function allTraits() {
  const s = new Set();
  DATA.champions.forEach((c) => c.traits.forEach((t) => s.add(t)));
  return [...s].sort();
}
function tile(name, icon, extraCls, title) {
  const d = document.createElement("div");
  d.className = "btile " + (extraCls || "");
  if (title) d.title = title;
  d.innerHTML = `<img src="${icon}" onerror="this.style.display='none'"><span>${name}</span>`;
  return d;
}
function makeChampBrowse() {
  const f = { cost: 0, trait: "" };   // 筛选状态跨开合保留
  return (box, refresh, onPick) => {
    const fr = document.createElement("div"); fr.className = "browse-filters";
    [0, 1, 2, 3, 4, 5].forEach((c) => {
      const b = document.createElement("button");
      b.textContent = c === 0 ? T("全部", "All") : T(c + "费", c + " cost");
      if (f.cost === c) b.classList.add("active");
      b.onclick = () => { f.cost = c; refresh(); };
      fr.appendChild(b);
    });
    box.appendChild(fr);
    const tr = document.createElement("div"); tr.className = "browse-filters traits";
    const all = document.createElement("button");
    all.textContent = T("全部羁绊", "All traits"); if (!f.trait) all.classList.add("active");
    all.onclick = () => { f.trait = ""; refresh(); };
    tr.appendChild(all);
    allTraits().forEach((t) => {
      const b = document.createElement("button");
      b.textContent = t; if (f.trait === t) b.classList.add("active");
      b.onclick = () => { f.trait = f.trait === t ? "" : t; refresh(); };
      tr.appendChild(b);
    });
    box.appendChild(tr);
    const grid = document.createElement("div"); grid.className = "browse-grid";
    DATA.champions
      .filter((c) => !c.form_of)
      .filter((c) => (!f.cost || c.cost === f.cost) && (!f.trait || (c.all_traits || c.traits).includes(f.trait)))
      .forEach((c) => {
        const label = c.forms ? c.name + " ◈" : c.name;
        const title = c.forms ? T(`${c.cost}费 形态可变: ${(c.all_traits || []).join("/")}`, `${c.cost} cost, has forms: ${(c.all_traits || []).join("/")}`) : T(`${c.cost}费 ${c.traits.join("/")}`, `${c.cost} cost ${c.traits.join("/")}`);
        const t = tile(label, c.icon, "cost" + c.cost, title);
        t.onclick = () => { onPick(c); refresh(); };
        grid.appendChild(t);
      });
    box.appendChild(grid);
  };
}
function itemBrowse(box, refresh, onPick) {
  const arr = DATA.items[activeItemCat] || [];
  const grid = document.createElement("div"); grid.className = "browse-grid";
  arr.forEach((it) => {
    const t = tile(it.name, it.icon, "", it.desc);
    t.onclick = () => onPick(it);
    grid.appendChild(t);
  });
  box.appendChild(grid);
}
function makeAugBrowse() {
  const f = { tier: "", traitOnly: false };
  return (box, refresh, onPick) => {
    const fr = document.createElement("div"); fr.className = "browse-filters";
    [["", T("全部", "All")], ["银", T("银", "Silver")], ["金", T("金", "Gold")], ["彩", T("彩", "Prismatic")]].forEach(([v, label]) => {
      const b = document.createElement("button");
      b.textContent = label; if (f.tier === v) b.classList.add("active");
      b.onclick = () => { f.tier = v; refresh(); };
      fr.appendChild(b);
    });
    const tb = document.createElement("button");
    tb.textContent = T("羁绊转职类", "Trait/emblem only"); if (f.traitOnly) tb.classList.add("active");
    tb.onclick = () => { f.traitOnly = !f.traitOnly; refresh(); };
    fr.appendChild(tb);
    box.appendChild(fr);
    const grid = document.createElement("div"); grid.className = "browse-grid";
    DATA.augments
      .filter((a) => (!f.tier || a.tier === f.tier) && (!f.traitOnly || a.traits.length))
      .forEach((a) => {
        const t = tile(a.name, a.icon, a.tier ? "tier" + a.tier : "", `[${a.tier || "?"}] ${a.desc}`);
        const q = document.createElement("span");
        q.className = "qmark"; q.textContent = "?"; q.title = T("加入待选三选一", "Add to pending pick-one-of-3");
        q.onclick = (ev) => { ev.stopPropagation(); if (S.pending.length < 3) { S.pending.push(a.name); saveState(); renderAugs(); } };
        t.appendChild(q);
        t.onclick = () => onPick(a);
        grid.appendChild(t);
      });
    box.appendChild(grid);
  };
}
const champOpt = (div, c) => {
  const desc = c.forms ? T(`${c.cost}费 点击展开形态选择 (未定形态可直接再点一次)`, `${c.cost} cost, click to expand form choices (click again for the base/unresolved form)`) : T(`${c.cost}费 ${c.traits.join("/")}`, `${c.cost} cost ${c.traits.join("/")}`);
  div.innerHTML = `<img src="${c.icon}" onerror="this.style.display='none'">` +
    `<span class="cost${c.cost}">${c.name}${c.forms ? " ◈" : ""}</span><span class="desc">${desc}</span>`;
};
const plainOpt = (div, e) => {
  const tier = e.tier ? `[${e.tier}] ` : "";
  div.innerHTML = (e.icon ? `<img src="${e.icon}" onerror="this.style.display='none'">` : "") +
    `<span>${tier}${e.name}</span><span class="desc">${e.desc || ""}</span>`;
};

// 多形态棋子(拉克丝): 第一次点基础名 -> 把输入框改写成 "名字 (" 展开形态列表;
// 在展开列表里点具体形态加入, 再点基础名则按"未定形态"加入
function formAwarePick(inputId, add) {
  return (c) => {
    const inp = $(inputId);
    if (c.forms && inp.value !== c.name + " (") {
      inp.value = c.name + " (";
      return "keep";
    }
    add(c);
  };
}
function champSource() {
  // 形态变体默认不出现在搜索/浏览里, 只在展开查询 (含"(") 时出现
  return (q) => DATA.champions.filter((c) => !c.form_of || (q && q.includes("(")));
}
function setupUnitSearch() {
  bindSearch("unit-search", "unit-suggest", champSource(), formAwarePick("unit-search", (c) => {
    S.units.push({ unit: c.name, star: 1, items: [], pos: null });
    saveState(); renderUnits();
  }), champOpt, makeChampBrowse());
}
function setupOppSearch() {
  bindSearch("opp-search", "opp-suggest", champSource(), formAwarePick("opp-search", (c) => {
    S.opp.units.push({ unit: c.name, pos: null });
    saveState(); renderOpp();
  }), champOpt, makeChampBrowse());
}
function setupItemSearch() {
  bindSearch("item-search", "item-suggest",
    () => Object.values(DATA.items).flat(),
    (it) => {
      if (assignTarget !== null && S.units[assignTarget]) {
        S.units[assignTarget].items.push(it.name); assignTarget = null;
      } else {
        const cat = findItemCat(it.name);
        S.pool[cat].push(it.name);
      }
      saveState(); renderUnits(); renderPool();
    }, plainOpt, itemBrowse);
}
function setupAugSearch() {
  bindSearch("aug-search", "aug-suggest", () => DATA.augments, (a, ev) => {
    if (ev && ev.shiftKey) { if (S.pending.length < 3) S.pending.push(a.name); }
    else S.augments.push(a.name);
    saveState(); renderAugs();
  }, (div, e) => {
    plainOpt(div, e);
    const b = document.createElement("span");
    b.className = "alt"; b.textContent = T("三选一?", "Pending?");
    b.onclick = (ev) => { ev.stopPropagation(); if (S.pending.length < 3) { S.pending.push(e.name); saveState(); renderAugs(); } };
    div.appendChild(b);
  }, makeAugBrowse());
}
function findItemCat(name) {
  for (const [cat, arr] of Object.entries(DATA.items)) if (arr.some((i) => i.name === name)) return cat;
  return "craftable";
}
function iconOf(name) {
  const c = DATA.champions.find((x) => x.name === name);
  if (c) return c.icon;
  for (const arr of Object.values(DATA.items)) { const i = arr.find((x) => x.name === name); if (i) return i.icon; }
  const a = DATA.augments.find((x) => x.name === name);
  return a ? a.icon : "";
}

// ---------------- 渲染 ----------------
function renderAll() { renderUnits(); renderPool(); renderAugs(); renderOpp(); }

function renderUnits() {
  const box = $("unit-list"); box.innerHTML = "";
  S.units.forEach((u, i) => {
    const c = DATA.champions.find((x) => x.name === u.unit) || { cost: 1, icon: "" };
    const row = document.createElement("div"); row.className = "unit-row";
    row.innerHTML = `<img src="${c.icon}" onerror="this.style.display='none'">` +
      `<span class="cost${c.cost}">${u.unit}</span>` +
      `<span class="star" title="${T("点击切换星级", "Click to cycle star level")}">${"★".repeat(u.star)}</span>`;
    row.querySelector(".star").onclick = () => { u.star = u.star % 3 + 1; saveState(); renderUnits(); };
    const uitems = document.createElement("span"); uitems.className = "uitems";
    u.items.forEach((it, j) => {
      const t = document.createElement("span"); t.textContent = it; t.title = T("点击移除", "Click to remove");
      t.onclick = () => { u.items.splice(j, 1); saveState(); renderUnits(); };
      uitems.appendChild(t);
    });
    row.appendChild(uitems);
    const btnItem = document.createElement("button"); btnItem.className = "mini-btn";
    btnItem.textContent = "⚔"; btnItem.title = T("给这个棋子配装: 点击后去装备搜索里选", "Equip this unit: click, then pick from the item search");
    btnItem.onclick = () => { assignTarget = i; $("item-search").focus(); $("item-search").placeholder = T(`给 ${u.unit} 配装...`, `Equipping ${u.unit}...`); };
    row.appendChild(btnItem);
    const btnPos = document.createElement("button"); btnPos.className = "mini-btn place-btn";
    btnPos.textContent = u.pos ? `(${u.pos[0]},${u.pos[1]})` : T("位", "Pos");
    if (placeTarget && placeTarget.list === "my" && placeTarget.idx === i) btnPos.classList.add("placing");
    btnPos.onclick = () => { placeTarget = { list: "my", idx: i }; renderUnits(); };
    row.appendChild(btnPos);
    const rm = document.createElement("span"); rm.className = "rm"; rm.textContent = "×";
    rm.onclick = () => { S.units.splice(i, 1); saveState(); renderUnits(); };
    row.appendChild(rm);
    box.appendChild(row);
  });
  renderGrid("my-grid", S.units, "my");
}

function renderGrid(elId, units, listName) {
  const grid = $(elId); grid.innerHTML = "";
  for (let r = 0; r < 4; r++) {
    const row = document.createElement("div"); row.className = "row" + (r % 2 ? " odd" : "");
    for (let c = 0; c < 7; c++) {
      const cell = document.createElement("div"); cell.className = "cell";
      const idx = units.findIndex((u) => u.pos && u.pos[0] === r && u.pos[1] === c);
      if (idx >= 0) {
        const u = units[idx];
        cell.innerHTML = `<img src="${iconOf(u.unit)}" onerror="this.style.display='none'"><span class="tag">${u.unit}</span>`;
      }
      cell.onclick = () => {
        if (placeTarget && placeTarget.list === listName) {
          units[placeTarget.idx].pos = [r, c]; placeTarget = null;
        } else if (idx >= 0) units[idx].pos = null;
        saveState(); listName === "my" ? renderUnits() : renderOpp();
      };
      row.appendChild(cell);
    }
    grid.appendChild(row);
  }
}

function chip(name, onX) {
  const el = document.createElement("span"); el.className = "chip";
  const ic = iconOf(name);
  el.innerHTML = (ic ? `<img src="${ic}" onerror="this.style.display='none'">` : "") + `<span>${name}</span>`;
  const x = document.createElement("span"); x.className = "x"; x.textContent = "×"; x.onclick = onX;
  el.appendChild(x);
  return el;
}
// "持有的装备" 统一面板: 库存(未装备) + 已装备(在某棋子上), 每件可改归属
function renderPool() {
  const box = $("item-pool"); box.innerHTML = "";
  const held = [];
  Object.entries(S.pool).forEach(([cat, arr]) => arr.forEach((n, i) => held.push({ name: n, kind: "pool", cat, idx: i })));
  S.units.forEach((u, ui) => (u.items || []).forEach((n, ii) => held.push({ name: n, kind: "unit", ui, ii })));
  if (!held.length) { box.innerHTML = `<span class="dim">${T("还没有装备", "No items yet")}</span>`; return; }
  const unitOpts = S.units.map((u, i) =>
    `<option value="${i}">${u.unit}${u.star || 1}★ ${u.pos ? "@" + u.pos[0] + "," + u.pos[1] : T("备战", "Bench")}</option>`).join("");
  held.forEach((it) => {
    const row = document.createElement("div"); row.className = "held";
    const ic = itemIcon(it.name);
    row.innerHTML = (ic ? `<img src="${ic}" onerror="this.style.display='none'">` : "") + `<span class="held-n">${it.name}</span>`;
    const sel = document.createElement("select"); sel.className = "held-own";
    sel.innerHTML = `<option value="pool">${T("未装备", "Unequipped")}</option>` + unitOpts;
    sel.value = it.kind === "unit" ? String(it.ui) : "pool";
    sel.onchange = () => moveHeld(it, sel.value);
    row.appendChild(sel);
    const x = document.createElement("button"); x.className = "held-x"; x.textContent = "×";
    x.onclick = () => removeHeld(it);
    row.appendChild(x);
    box.appendChild(row);
  });
}
function moveHeld(it, target) {
  const name = it.name;
  if (it.kind === "pool") S.pool[it.cat].splice(it.idx, 1); else S.units[it.ui].items.splice(it.ii, 1);
  if (target === "pool") S.pool[findItemCat(name)].push(name); else S.units[+target].items.push(name);
  saveState(); renderAll();
}
function removeHeld(it) {
  if (it.kind === "pool") S.pool[it.cat].splice(it.idx, 1); else S.units[it.ui].items.splice(it.ii, 1);
  saveState(); renderAll();
}
function itemIcon(name) {
  if (!DATA) return "";
  for (const arr of Object.values(DATA.items)) { const f = arr.find((i) => i.name === name); if (f) return f.icon; }
  return "";
}
function renderAugs() {
  const o = $("aug-owned"); o.innerHTML = "";
  S.augments.forEach((n, i) => o.appendChild(chip(n, () => { S.augments.splice(i, 1); saveState(); renderAugs(); })));
  const p = $("aug-pending"); p.innerHTML = "";
  S.pending.forEach((n, i) => p.appendChild(chip(n, () => { S.pending.splice(i, 1); saveState(); renderAugs(); })));
}
function renderOpp() {
  const box = $("opp-list"); box.innerHTML = "";
  S.opp.units.forEach((u, i) => {
    const c = DATA.champions.find((x) => x.name === u.unit) || { cost: 1, icon: "" };
    const row = document.createElement("div"); row.className = "unit-row";
    row.innerHTML = `<img src="${c.icon}" onerror="this.style.display='none'"><span class="cost${c.cost}">${u.unit}</span>`;
    const btnPos = document.createElement("button"); btnPos.className = "mini-btn place-btn";
    btnPos.textContent = u.pos ? `(${u.pos[0]},${u.pos[1]})` : T("位", "Pos");
    if (placeTarget && placeTarget.list === "opp" && placeTarget.idx === i) btnPos.classList.add("placing");
    btnPos.onclick = () => { placeTarget = { list: "opp", idx: i }; renderOpp(); };
    row.appendChild(btnPos);
    const rm = document.createElement("span"); rm.className = "rm"; rm.textContent = "×";
    rm.onclick = () => { S.opp.units.splice(i, 1); saveState(); renderOpp(); };
    row.appendChild(rm);
    box.appendChild(row);
  });
  renderGrid("opp-grid", S.opp.units, "opp");
}

function setupTabs() {
  $("item-tabs").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      $("item-tabs").querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); activeItemCat = b.dataset.cat; renderPool();
    };
  });
}

// ---------------- 收集状态给后端 ----------------
function collectState() {
  const st = S.status;
  // 纹章在 UI 里跟其他装备合并存在 S.pool.emblem, 但后端契约仍是分开的
  // emblems 字段 + items(不含 emblem 分类) —— 这里拆回去, 不改后端。
  const { emblem: embOwned, ...poolForBackend } = S.pool;
  const state = {
    stage: st.stage || null, level: st.level ? +st.level : null,
    gold: st.gold ? +st.gold : null, hp: st.hp ? +st.hp : null, streak: st.streak || null,
    augments: S.augments, pending_augments: S.pending, emblems: embOwned,
    items: poolForBackend,
    board: S.units.filter((u) => u.pos).map((u) => ({ unit: u.unit, star: u.star, items: u.items, pos: u.pos })),
    bench: S.units.filter((u) => !u.pos).map((u) => ({ unit: u.unit, star: u.star, items: u.items })),
    note: S.note || null,
  };
  if (S.shop && S.shop.length) state.shop = S.shop;
  if ($("opp-toggle").checked && (S.opp.units.length || S.opp.note || S.opp.hp != null)) {
    state.opponent = { board: S.opp.units.map((u) => ({ unit: u.unit, pos: u.pos || undefined,
      star: u.star || undefined, items: (u.items && u.items.length) ? u.items : undefined })), note: S.opp.note || null };
    if (S.opp.hp != null) state.opponent.hp = S.opp.hp;
  }
  return state;
}

// ---------------- 对话 ----------------
function mdLite(text) {
  let t = text.replace(/```json[\s\S]*?```/g, "").replace(/```[\s\S]*?```/g, (m) => m.replace(/```\w*\n?|```/g, ""));
  t = t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  t = t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/^#{1,4}\s*(.+)$/gm, "<b>$1</b>");
  return t.trim();
}
// 距底部 60px 以内才算"跟随中"; 用户翻上去看历史时不强制拉回
function nearBottom(el) { return el.scrollHeight - el.scrollTop - el.clientHeight < 60; }
function addMsg(cls, html) {
  const log = $("chat-log");
  const stick = nearBottom(log) || cls === "user";  // 自己发消息总是跳到底部
  const div = document.createElement("div"); div.className = "msg " + cls; div.innerHTML = html;
  log.appendChild(div);
  if (stick) log.scrollTop = 1e9;
  return div;
}

async function sendChat({ action = null, message = null }) {
  const btn = $("btn-send"); btn.disabled = true;
  const label = action ? ($("quick-actions").querySelector(`[data-action=${action}]`) || {}).textContent : null;
  addMsg("user", mdLite(message || label || "..."));
  const asDiv = addMsg("assistant", "…");
  let raw = "";
  try {
    const resp = await fetch(API + "/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION, state: collectState(), action, message,
        deep: $("deep-mode").checked, api_key: userApiKey(),
        provider: apiPrefs().provider || null, model: apiPrefs().model || null }),
    });
    const reader = resp.body.getReader(); const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const events = buf.split("\n\n"); buf = events.pop();
      for (const ev of events) {
        if (!ev.startsWith("data: ")) continue;
        const e = JSON.parse(ev.slice(6));
        if (e.type === "session") { SESSION = e.session_id; localStorage.setItem("tft_session", SESSION); $("sess-label").textContent = SESSION; }
        else if (e.type === "delta") {
          raw += e.text;
          const log = $("chat-log"), stick = nearBottom(log);
          asDiv.innerHTML = mdLite(raw);
          if (stick) log.scrollTop = 1e9;
        }
        else if (e.type === "tool") addMsg("tool", "🔍 " + e.text);
        else if (e.type === "error") addMsg("error", T("出错: ", "Error: ") + mdLite(e.text));
        else if (e.type === "done" && e.structured) renderTarget(e.structured);
      }
    }
  } catch (err) {
    addMsg("error", T("请求失败: ", "Request failed: ") + err.message);
  }
  btn.disabled = false;
}

let lastTarget = null;   // 常驻目标板: 新回答省略 positioning 时沿用上一次的
function renderTarget(st) {
  const tb = $("target-board");
  if (!(st.target_comp || []).length && !(st.positioning || []).length) return;
  if (lastTarget) {
    if (!(st.positioning || []).length) st.positioning = lastTarget.positioning || [];
    if (!(st.target_comp || []).length) st.target_comp = lastTarget.target_comp || [];
  }
  lastTarget = st;
  const comp = st.target_comp || [], pos = st.positioning || [];
  tb.style.display = "";
  const copyBtn = $("tb-copy");
  if (st.team_code) {
    copyBtn.style.display = "";
    copyBtn.onclick = async () => {
      try { await navigator.clipboard.writeText(st.team_code); copyBtn.textContent = T("✓ 已复制", "✓ Copied"); }
      catch (e) { prompt(T("手动复制阵容码:", "Copy the team code manually:"), st.team_code); }
      setTimeout(() => (copyBtn.textContent = T("📋 阵容码", "📋 Team code")), 1500);
    };
  } else copyBtn.style.display = "none";
  const cbox = $("tb-comp"); cbox.innerHTML = "";
  comp.forEach((u) => {
    const c = DATA.champions.find((x) => x.name === u.unit) || { cost: 1 };
    const row = document.createElement("div"); row.className = "tc-row";
    row.innerHTML = `<span class="cost${c.cost}">${u.unit}</span><span class="star">${"★".repeat(u.star || 1)}</span>` +
      (u.items && u.items.length ? `<span class="tc-items">${u.items.join(" / ")}</span>` : "");
    cbox.appendChild(row);
  });
  const tt = $("tb-traits"); tt.innerHTML = "";
  for (const line of st.trait_check || []) {
    if (line.startsWith("阵容核验")) continue;
    const d = document.createElement("div");
    d.className = "trait-line" + (/溢出|无法识别|需要调整|未激活\(差/.test(line) ? " bad" : "");
    d.textContent = line;
    tt.appendChild(d);
  }
  const units = pos.map((p) => ({ unit: p.unit, pos: [p.row, p.col] }));
  renderGrid("tb-grid", units, "tb");
  $("tb-close").onclick = () => (tb.style.display = "none");
}

// ---------------- API 配置 (供应商/模型/key) ----------------
function apiPrefs() {
  try { return JSON.parse(localStorage.getItem("tft_api_prefs")) || {}; } catch (e) { return {}; }
}
function saveApiPrefs(p) { localStorage.setItem("tft_api_prefs", JSON.stringify(p)); }
function userApiKey() {
  const prefs = apiPrefs();
  return (prefs.keys || {})[prefs.provider || DATA.config.default_provider] || null;
}
function setupApiKey() {
  const cfg = DATA.config || {};
  const btn = $("btn-api"), panel = $("api-panel"), status = $("api-status");
  const selP = $("api-provider"), selM = $("api-model");
  const providers = cfg.providers || {};
  const curProvider = () => apiPrefs().provider || cfg.default_provider;

  const fillModels = () => {
    const p = providers[selP.value] || { models: [] };
    selM.innerHTML = "";
    for (const m of p.models) {
      const o = document.createElement("option");
      o.value = m; o.textContent = m + (m === p.default_model ? T(" (默认)", " (default)") : "");
      selM.appendChild(o);
    }
    const prefs = apiPrefs();
    selM.value = (selP.value === prefs.provider && prefs.model && p.models.includes(prefs.model))
      ? prefs.model : p.default_model;
  };
  const refreshUI = () => {
    const prefs = apiPrefs();
    const p = curProvider();
    const hasKey = !!(prefs.keys || {})[p];
    btn.textContent = `API:${p}` + (hasKey ? "✓" : "");
    const srv = (providers[p] || {}).server_key_set;
    status.textContent = hasKey ? T("使用你自己的 key", "Using your own key") : srv ? T("使用服务端 key", "Using the server's key") : T("该供应商无可用 key, 请填入", "No key available for this provider, please fill one in");
  };

  if (!cfg.allow_user_key) {
    const p = cfg.default_provider;
    if (!(providers[p] || {}).server_key_set)
      addMsg("error", T("服务端未配置 API key, 且未开放用户自填 (config.yaml 的 llm.allow_user_key)。",
        "The server has no API key configured, and user-supplied keys are disabled (config.yaml: llm.allow_user_key)."));
    return;
  }
  btn.style.display = "";
  selP.innerHTML = "";
  for (const name of Object.keys(providers)) {
    const o = document.createElement("option");
    o.value = name; o.textContent = name;
    selP.appendChild(o);
  }
  selP.value = curProvider();
  fillModels();
  selP.onchange = fillModels;
  btn.onclick = () => { panel.style.display = panel.style.display === "none" ? "" : "none"; refreshUI(); };
  $("api-save").onclick = () => {
    const prefs = apiPrefs();
    prefs.provider = selP.value;
    prefs.model = selM.value;
    const v = $("api-key-input").value.trim();
    if (v) { prefs.keys = prefs.keys || {}; prefs.keys[selP.value] = v; $("api-key-input").value = ""; }
    saveApiPrefs(prefs); refreshUI();
  };
  $("api-clear").onclick = () => { localStorage.removeItem("tft_api_prefs"); localStorage.removeItem("tft_api_key"); selP.value = cfg.default_provider; fillModels(); refreshUI(); };
  refreshUI();
  if (!(providers[curProvider()] || {}).server_key_set && !userApiKey()) panel.style.display = "";
}

function setupChat() {
  $("quick-actions").querySelectorAll("button").forEach((b) => {
    b.onclick = () => sendChat({ action: b.dataset.action });
  });
  const send = () => {
    const t = $("chat-input").value.trim();
    if (!t) return;
    $("chat-input").value = "";
    sendChat({ message: t });
  };
  $("btn-send").onclick = send;
  $("chat-input").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); };
}

// ---------------- 截图识别 (调独立视觉API) ----------------
const visionAPI = () => localStorage.getItem("vision_api") || "http://localhost:8010";
const visionKey = () => localStorage.getItem("vision_key") || "";

function setupRecognize() {
  const btn = $("btn-recognize"), file = $("rec-file");
  if (!btn) return;
  btn.onclick = () => file.click();
  file.onchange = (e) => { const f = e.target.files[0]; if (f) recognizeShot(f, $("rec-mode").value); e.target.value = ""; };
  $("btn-vcfg").onclick = () => {
    const url = prompt(T("视觉服务地址 (本地默认 http://localhost:8010; 分发填你的托管地址):", "Vision service URL (default http://localhost:8010; use your own hosted address if not local):"), visionAPI());
    if (url === null) return;
    localStorage.setItem("vision_api", url.trim());
    const key = prompt(T("视觉服务 API Key (没有留空):", "Vision service API key (leave blank if none):"), visionKey());
    if (key !== null) localStorage.setItem("vision_key", key.trim());
  };
  // 直接在网页上 Ctrl+V 粘贴截图即识别(截图软件把图放剪贴板)
  window.addEventListener("paste", (e) => {
    const items = (e.clipboardData || {}).items || [];
    for (const it of items) {
      if (it.type && it.type.startsWith("image/")) {
        const blob = it.getAsFile();
        if (blob) { e.preventDefault(); recognizeShot(blob, $("rec-mode").value); }
        return;
      }
    }
  });
}

async function recognizeShot(f, mode) {
  const btn = $("btn-recognize"), old = btn.textContent;
  btn.disabled = true; btn.textContent = T("识别中…", "Recognizing…");
  try {
    const fd = new FormData(); fd.append("image", f); fd.append("mode", mode);
    const headers = {}; if (visionKey()) headers["X-API-Key"] = visionKey();
    const r = await fetch(visionAPI() + "/api/recognize", { method: "POST", body: fd, headers });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    applyRecognized(d.state, mode);
    addMsg("assistant", T("📷 已识别并填入左侧局面 (核对后再发问):<br>", "📷 Recognized and filled into the board on the left (please check before asking):<br>") + recapHtml(d.state, mode));
  } catch (e) {
    alert(T("识别失败: ", "Recognition failed: ") + e.message + T("\n视觉服务地址: ", "\nVision service URL: ") + visionAPI() + T(" (点 ⚙ 可改)", " (click ⚙ to change)"));
  } finally { btn.disabled = false; btn.textContent = old; }
}

function recapHtml(st, mode) {
  const none = T("无", "none"), empty = T("空", "empty");
  if (mode === "enemy") {
    const opp = st.opponent || {}, b = opp.board || [];
    return T(
      `敌方棋盘 · 阶段 ${st.stage || "?"} · 对手血量 ${opp.hp ?? "?"}<br>识别到 ${b.length} 个棋子: ${b.map((u) => u.unit).join(", ") || none}`,
      `Enemy board · Stage ${st.stage || "?"} · Opponent HP ${opp.hp ?? "?"}<br>Recognized ${b.length} unit(s): ${b.map((u) => u.unit).join(", ") || none}`
    );
  }
  if (mode === "augment")
    return T(
      `阶段 ${st.stage || "?"} · 血量 ${st.hp ?? "?"} · 海克斯: ${(st.pending_augments || []).join(" / ") || none}`,
      `Stage ${st.stage || "?"} · HP ${st.hp ?? "?"} · Augments: ${(st.pending_augments || []).join(" / ") || none}`
    );
  const shop = (st.shop || []).join(", ") || empty;
  return T(
    `阶段 ${st.stage || "?"} · 等级 ${st.level ?? "?"} · 金币 ${st.gold ?? "?"} · 血量 ${st.hp ?? "?"}<br>`
    + `场上/备战 ${(st.board || []).length + (st.bench || []).length} 个 · 商店: ${shop}`,
    `Stage ${st.stage || "?"} · Level ${st.level ?? "?"} · Gold ${st.gold ?? "?"} · HP ${st.hp ?? "?"}<br>`
    + `Board/bench: ${(st.board || []).length + (st.bench || []).length} unit(s) · Shop: ${shop}`
  );
}

function applyRecognized(st, mode) {
  if (mode === "augment") {
    if (st.stage) S.status.stage = st.stage;
    if (st.hp != null) S.status.hp = st.hp;
    S.pending = (st.pending_augments || []).slice(0, 3);
  } else if (mode === "enemy") {
    if (st.stage) S.status.stage = st.stage;
    const opp = st.opponent || {};
    S.opp.units = (opp.board || []).map((u) => ({ unit: u.unit, pos: u.pos || null, star: u.star || 1, items: u.items || [] }));
    S.opp.hp = opp.hp != null ? opp.hp : null;
    $("opp-toggle").checked = true;
    $("opp-area").style.display = "";
    const el = $("st-stage"); if (el) el.value = S.status.stage;
  } else {
    S.status.stage = st.stage || "";
    S.status.level = st.level != null ? st.level : "";
    S.status.gold = st.gold != null ? st.gold : "";
    S.status.hp = st.hp != null ? st.hp : "";
    S.status.streak = st.streak || "";
    S.units = [
      ...(st.board || []).map((u) => ({ unit: u.unit, star: u.star || 1, items: u.items || [], pos: u.pos })),
      ...(st.bench || []).map((u) => ({ unit: u.unit, star: u.star || 1, items: u.items || [], pos: null })),
    ];
    const pool = { component: [], craftable: [], artifact: [], support: [], radiant: [] };
    Object.entries(st.items || {}).forEach(([k, v]) => { if (pool[k]) pool[k] = v.slice(); });
    S.pool = pool;
    S.shop = st.shop || [];
    ["stage", "level", "gold", "hp", "streak"].forEach((k) => { const el = $("st-" + k); if (el) el.value = S.status[k]; });
  }
  saveState();
  renderAll();
}

setupRecognize();
init();
