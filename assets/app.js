const state = {
  followOpmlItems: [],
  followOpmlGroups: [],
  momoyuItems: [],
  momoyuParsed: null,
  generatedAt: null,
};

const followOpmlListEl = document.getElementById("followOpmlList");
const followOpmlCountEl = document.getElementById("followOpmlCount");
const momoyuListEl = document.getElementById("momoyuList");
const momoyuCountEl = document.getElementById("momoyuCount");
const updatedAtEl = document.getElementById("updatedAt");
const itemTpl = document.getElementById("itemTpl");

function fmtNumber(n) {
  return new Intl.NumberFormat("zh-CN").format(n || 0);
}

function fmtTime(iso) {
  if (!iso) return "时间未知";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

function renderItemNode(item) {
  const node = itemTpl.content.firstElementChild.cloneNode(true);
  node.querySelector(".site").textContent = item.site_name || "订阅";
  node.querySelector(".source").textContent = `来源: ${item.source || "未分区"}`;
  node.querySelector(".time").textContent = fmtTime(item.published_at || item.first_seen_at);

  const titleEl = node.querySelector(".title");
  const zh = (item.title_zh || "").trim();
  const en = (item.title_en || "").trim();
  titleEl.textContent = "";
  if (zh && en && zh !== en) {
    const primary = document.createElement("span");
    primary.textContent = zh;
    const sub = document.createElement("span");
    sub.className = "title-sub";
    sub.textContent = en;
    titleEl.appendChild(primary);
    titleEl.appendChild(sub);
  } else {
    titleEl.textContent = item.title || zh || en;
  }
  titleEl.href = item.url || "#";
  return node;
}

function renderSection(listEl, countEl, items, emptyText) {
  if (!listEl || !countEl) return;
  countEl.textContent = `${fmtNumber(items.length)} 条`;
  listEl.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = emptyText;
    listEl.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    listEl.appendChild(renderItemNode(item));
  });
}

function renderFollowOpmlGroups() {
  if (!followOpmlListEl || !followOpmlCountEl) return;
  const groups = Array.isArray(state.followOpmlGroups) ? state.followOpmlGroups : [];
  followOpmlListEl.innerHTML = "";

  if (!groups.length) {
    renderSection(followOpmlListEl, followOpmlCountEl, state.followOpmlItems || [], "暂无 follow.opml 数据。");
    return;
  }

  const shownCount = groups.reduce((acc, group) => acc + ((group.items || []).length || 0), 0);
  followOpmlCountEl.textContent = `${fmtNumber(groups.length)} 个订阅 · ${fmtNumber(shownCount)} 条`;

  groups.forEach((group) => {
    const wrap = document.createElement("section");
    wrap.className = "momoyu-section";
    const feedName = group.source || "未命名订阅";
    wrap.innerHTML = `
      <header class="momoyu-section-head">
        <h3>${feedName}</h3>
        <span>${fmtNumber(group.shown_count || (group.items || []).length || 0)} 条</span>
      </header>
      <div class="momoyu-section-list"></div>
    `;
    const list = wrap.querySelector(".momoyu-section-list");
    const entries = Array.isArray(group.items) ? group.items : [];
    entries.forEach((item) => list.appendChild(renderItemNode(item)));
    followOpmlListEl.appendChild(wrap);
  });
}

function renderMomoyuParsed() {
  if (!momoyuListEl || !momoyuCountEl) return;
  const parsed = state.momoyuParsed || {};
  const sections = Array.isArray(parsed.sections) ? parsed.sections : [];

  momoyuCountEl.textContent = `${fmtNumber(sections.length)} 个榜单`;
  momoyuListEl.innerHTML = "";
  if (!sections.length) {
    renderSection(momoyuListEl, momoyuCountEl, state.momoyuItems || [], "暂无 momoyu RSS 数据。");
    return;
  }

  const meta = document.createElement("div");
  meta.className = "momoyu-meta";
  meta.textContent = parsed.item_title
    ? `${parsed.item_title}${parsed.pubDate ? ` · ${parsed.pubDate}` : ""}`
    : "momoyu 结构化热榜";
  momoyuListEl.appendChild(meta);

  sections.forEach((sec) => {
    const wrap = document.createElement("section");
    wrap.className = "momoyu-section";
    wrap.innerHTML = `
      <header class="momoyu-section-head">
        <h3>${sec.section || "未命名榜单"}</h3>
        <span>${fmtNumber(sec.count || 0)} 条</span>
      </header>
      <div class="momoyu-section-list"></div>
    `;
    const list = wrap.querySelector(".momoyu-section-list");
    const entries = Array.isArray(sec.entries) ? sec.entries : [];
    entries.forEach((entry) => {
      const row = document.createElement("a");
      row.className = "momoyu-entry";
      row.href = entry.url || "#";
      row.target = "_blank";
      row.rel = "noopener noreferrer";
      row.innerHTML = `
        <span class="momoyu-rank">${entry.rank ?? "-"}</span>
        <span class="momoyu-title">${entry.title || "无标题"}</span>
      `;
      list.appendChild(row);
    });
    momoyuListEl.appendChild(wrap);
  });
}

async function loadNewsData() {
  const res = await fetch(`./data/latest-24h.json?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 latest-24h.json 失败: ${res.status}`);
  return res.json();
}

async function init() {
  try {
    const payload = await loadNewsData();
    state.followOpmlItems = payload.follow_opml_items || [];
    state.followOpmlGroups = payload.follow_opml_groups || [];
    state.momoyuItems = payload.momoyu_items || [];
    state.momoyuParsed = payload.momoyu_parsed || null;
    state.generatedAt = payload.generated_at;

    renderFollowOpmlGroups();
    renderMomoyuParsed();

    if (updatedAtEl) {
      updatedAtEl.textContent = `更新时间：${fmtTime(state.generatedAt)}`;
    }
  } catch (err) {
    if (updatedAtEl) updatedAtEl.textContent = "数据加载失败";
    if (followOpmlListEl) {
      followOpmlListEl.innerHTML = `<div class=\"empty\">${err.message}</div>`;
    }
    if (momoyuListEl) {
      momoyuListEl.innerHTML = `<div class=\"empty\">${err.message}</div>`;
    }
  }
}

init();
