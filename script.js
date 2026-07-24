"use strict";

const state = {
  data: null,
  strategy: new URLSearchParams(window.location.search).get("strategy") || "cross_perp",
  window: new URLSearchParams(window.location.search).get("window") || "1d",
  search: "",
  minSpread: null,
  longLeg: "all",
  shortLeg: "all",
  pair: "all",
  fullOnly: false,
  sort: "spread_desc",
  page: 1,
  pageSize: 100,
};

const WINDOW_DAYS = {
  "1d": 1,
  "7d": 7,
  "30d": 30,
};

const elements = {
  dataTime: document.querySelector("#data-time"),
  exchangeStatus: document.querySelector("#exchange-status"),
  strategyTabs: document.querySelector("#strategy-tabs"),
  windowTabs: document.querySelector("#window-tabs"),
  metrics: document.querySelector("#metrics"),
  body: document.querySelector("#opportunity-body"),
  resultCount: document.querySelector("#result-count"),
  emptyState: document.querySelector("#empty-state"),
  errorBanner: document.querySelector("#error-banner"),
  search: document.querySelector("#search-input"),
  minSpread: document.querySelector("#min-spread"),
  longLeg: document.querySelector("#long-leg-filter"),
  shortLeg: document.querySelector("#short-leg-filter"),
  pair: document.querySelector("#pair-filter"),
  sort: document.querySelector("#sort-select"),
  fullOnly: document.querySelector("#full-window"),
  refresh: document.querySelector("#refresh-button"),
  export: document.querySelector("#export-button"),
  reset: document.querySelector("#reset-button"),
  mobileFilterToggle: document.querySelector("#mobile-filter-toggle"),
  filters: document.querySelector(".filters"),
  dialog: document.querySelector("#detail-dialog"),
  detailPair: document.querySelector("#detail-pair"),
  detailTitle: document.querySelector("#detail-title"),
  detailContent: document.querySelector("#detail-content"),
  closeDialog: document.querySelector("#close-dialog"),
  pagination: document.querySelector("#pagination"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function exchangeLabel(id) {
  return state.data?.exchanges?.[id]?.label || id;
}

function marketLabel(market) {
  return market === "spot" ? "现货" : "永续";
}

function legLabel(leg) {
  return `${exchangeLabel(leg?.exchange)} ${marketLabel(leg?.market)}`;
}

function legFilterValue(leg) {
  return leg?.exchange && leg?.market ? `${leg.exchange}:${leg.market}` : "";
}

function venueLabel(venue) {
  return `${exchangeLabel(venue.exchange)} ${marketLabel(venue.market)}`;
}

function formatPct(value, digits = 4) {
  const number = Number(value || 0);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(digits)}%`;
}

function formatTurnover(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${(Number(value) / 10_000).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}万`;
}

function formatDate(value, includeTime = false) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, includeTime ? 16 : 10);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  }).format(date);
}

function currentRows() {
  if (!state.data) return [];
  const query = state.search.trim().toUpperCase();
  const rows = state.data.opportunities
    .map((opportunity) => ({ opportunity, windowData: opportunity.windows[state.window] }))
    .filter(({ opportunity, windowData }) => {
      if (opportunity.strategy_type !== state.strategy) return false;
      if (!windowData) return false;
      const symbols = Object.values(opportunity.symbols || {}).join(" ").toUpperCase();
      if (query && !opportunity.underlying.includes(query) && !symbols.includes(query)) return false;
      if (state.minSpread !== null && annualizedSignedDiff(windowData) < state.minSpread) return false;
      if (state.fullOnly && !isFullWindow(opportunity, windowData, state.window)) return false;
      if (state.longLeg !== "all" && legFilterValue(windowData.long_leg) !== state.longLeg) return false;
      if (state.shortLeg !== "all" && legFilterValue(windowData.short_leg) !== state.shortLeg) return false;
      if (state.pair !== "all" && opportunity.id.split(":").slice(0, -1).join(":") !== state.pair) return false;
      return true;
    });

  rows.sort((a, b) => {
    if (state.sort === "symbol_asc") return a.opportunity.underlying.localeCompare(b.opportunity.underlying);
    if (state.sort === "latest_desc") {
      const aGap = latestSignedDiff(a.opportunity);
      const bGap = latestSignedDiff(b.opportunity);
      return bGap - aGap;
    }
    return annualizedSignedDiff(b.windowData) - annualizedSignedDiff(a.windowData);
  });
  return rows;
}

function annualizedSignedDiff(windowData) {
  return Number(windowData.annualized_signed_diff_pct ?? windowData.signed_diff_pct ?? 0);
}

function isFullWindow(opportunity, windowData, window) {
  if (!windowData?.is_full_window) return false;
  const requiredDays = WINDOW_DAYS[window];
  if (!requiredDays) return true;

  const commonStart = new Date(opportunity.common_start_time).getTime();
  const windowEnd = new Date(windowData.end_time).getTime();
  if (!Number.isFinite(commonStart) || !Number.isFinite(windowEnd)) return false;
  return windowEnd - commonStart >= requiredDays * 86_400_000;
}

function annualizedRates(windowData) {
  return windowData.annualized_rates_pct || windowData.rates_pct || {};
}

function latestSignedDiff(opportunity) {
  const rates = opportunity.venues.map((venue) => (
    venue.market === "spot" ? 0 : Number(opportunity.latest?.[venue.key]?.rate_pct || 0)
  ));
  return rates.length > 1 ? rates[0] - rates[1] : 0;
}

function differenceBasis(opportunity) {
  const [first, second] = opportunity.venues;
  if (!first || !second) return "--";
  return `${venueLabel(first)} − ${venueLabel(second)}`;
}

function signedValueClass(value) {
  const number = Number(value || 0);
  if (number > 0) return "positive";
  if (number < 0) return "negative";
  return "";
}

function renderTabs() {
  elements.strategyTabs.innerHTML = Object.entries(state.data.strategy_labels).map(([strategy, label]) => `
    <button class="strategy-button ${strategy === state.strategy ? "active" : ""}"
      type="button" data-strategy="${escapeHtml(strategy)}">${escapeHtml(label)}</button>
  `).join("");
  elements.windowTabs.innerHTML = state.data.windows.map((window) => `
    <button class="segment-button ${window === state.window ? "active" : ""}"
      type="button" data-window="${escapeHtml(window)}">
      ${escapeHtml(state.data.window_labels[window])}
    </button>
  `).join("");
}

function renderExchangeStatus() {
  elements.exchangeStatus.innerHTML = Object.entries(state.data.exchanges).map(([id, exchange]) => `
    <span class="exchange-chip ${exchange.connected ? "" : "pending"}" style="--chip-color:${escapeHtml(exchange.accent)}"
      title="${exchange.connected ? "已接入真实数据" : "待接入"}">${escapeHtml(exchange.label)}</span>
  `).join("");
}

function renderLegOptions() {
  const longOptions = new Map();
  const shortOptions = new Map();

  state.data.opportunities
    .filter((item) => item.strategy_type === state.strategy && item.windows[state.window])
    .forEach((item) => {
      const windowData = item.windows[state.window];
      const longValue = legFilterValue(windowData.long_leg);
      const shortValue = legFilterValue(windowData.short_leg);
      if (longValue) longOptions.set(longValue, legLabel(windowData.long_leg));
      if (shortValue) shortOptions.set(shortValue, legLabel(windowData.short_leg));
    });

  const renderOptions = (options) => [...options.entries()]
    .sort((a, b) => a[1].localeCompare(b[1]))
    .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
    .join("");

  elements.longLeg.innerHTML = `<option value="all">全部交易腿</option>${renderOptions(longOptions)}`;
  elements.shortLeg.innerHTML = `<option value="all">全部交易腿</option>${renderOptions(shortOptions)}`;

  if (!longOptions.has(state.longLeg)) state.longLeg = "all";
  if (!shortOptions.has(state.shortLeg)) state.shortLeg = "all";
  elements.longLeg.value = state.longLeg;
  elements.shortLeg.value = state.shortLeg;
}

function renderPairOptions() {
  const options = new Map();
  state.data.opportunities
    .filter((item) => item.strategy_type === state.strategy)
    .forEach((item) => {
      const key = item.id.split(":").slice(0, -1).join(":");
      options.set(key, item.venues.map(venueLabel).join(" / "));
    });
  const sorted = [...options.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  elements.pair.innerHTML = `<option value="all">全部组合</option>` + sorted
    .map(([key, label]) => `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`)
    .join("");
  if (options.has(state.pair)) elements.pair.value = state.pair;
  else { state.pair = "all"; elements.pair.value = "all"; }
}

function renderMetrics(rows) {
  const top = rows[0];
  const available = state.data.opportunities.filter((item) => item.strategy_type === state.strategy && item.windows[state.window]).length;
  const fullCount = rows.filter((row) => (
    isFullWindow(row.opportunity, row.windowData, state.window)
  )).length;
  const latestTop = rows.length
    ? rows.reduce((best, row) => Math.max(best, latestSignedDiff(row.opportunity)), Number.NEGATIVE_INFINITY)
    : null;
  const annualizedTopValue = top ? annualizedSignedDiff(top.windowData) : null;
  elements.metrics.innerHTML = `
    <div class="metric">
      <div class="metric-label">共有合约</div>
      <div class="metric-value">${available}</div>
      <div class="metric-sub">当前统计周期</div>
    </div>
    <div class="metric">
      <div class="metric-label">筛选后候选</div>
      <div class="metric-value">${rows.length}</div>
      <div class="metric-sub">完整周期 ${fullCount}</div>
    </div>
    <div class="metric">
      <div class="metric-label">最高带符号年化差值</div>
      <div class="metric-value ${top ? signedValueClass(annualizedTopValue) : ""}">${top ? formatPct(annualizedTopValue) : "--"}</div>
      <div class="metric-sub">${top ? escapeHtml(differenceBasis(top.opportunity)) : "--"}</div>
    </div>
    <div class="metric">
      <div class="metric-label">最高带符号最新期差值</div>
      <div class="metric-value ${rows.length ? signedValueClass(latestTop) : ""}">${rows.length ? formatPct(latestTop) : "--"}</div>
      <div class="metric-sub">单期 Funding 按组合顺序相减</div>
    </div>
  `;
}

function pairValues(opportunity, source, formatter) {
  return opportunity.venues.map((venue) => {
    const value = source?.[venue.key];
    return `<span class="exchange-value"><small>${escapeHtml(venueLabel(venue))}</small>${formatter(value, venue)}</span>`;
  }).join("");
}

function rowHtml({ opportunity, windowData }) {
  const shortLabel = legLabel(windowData.short_leg);
  const longLabel = legLabel(windowData.long_leg);
  const symbols = opportunity.venues.map((venue) => opportunity.symbols[venue.key]).filter(Boolean).join(" · ");
  const latest = pairValues(opportunity, opportunity.latest, (item, venue) => venue.market === "spot" ? formatPct(0) : (item ? formatPct(item.rate_pct) : "--"));
  const annualized = pairValues(opportunity, annualizedRates(windowData), (rate) => formatPct(rate));
  const turnover = pairValues(opportunity, opportunity.turnover_24h_usdt, (value) => formatTurnover(value));
  const records = opportunity.venues.map((venue) => `${venueLabel(venue)} ${windowData.records[venue.key] || 0}`).join(" / ");
  return `
    <tr>
      <td>
        <button class="symbol-button" type="button" data-detail="${escapeHtml(opportunity.id)}">${escapeHtml(opportunity.underlying)}</button>
        <div class="symbol-sub">${escapeHtml(symbols)}</div>
      </td>
      <td><div class="route"><span class="receive-text">空 ${escapeHtml(shortLabel)}</span><span class="route-arrow">→</span><span class="pay-text">多 ${escapeHtml(longLabel)}</span></div></td>
      <td><div class="latest-pair">${latest}</div></td>
      <td><div class="cumulative-pair">${annualized}</div></td>
      <td><div class="spread-value ${signedValueClass(annualizedSignedDiff(windowData))}">${formatPct(annualizedSignedDiff(windowData))}<br><small>${escapeHtml(differenceBasis(opportunity))}</small><br><small>${Number(windowData.elapsed_days || 0).toFixed(2)} 天 · 未扣费用</small></div></td>
      <td><div class="cumulative-pair">${turnover}</div></td>
      <td><span class="record-count">${escapeHtml(records)}</span></td>
      <td><span class="date-value">${formatDate(opportunity.common_start_time)}</span></td>
    </tr>
  `;
}

function render() {
  if (!state.data) return;
  renderTabs();
  renderLegOptions();
  renderPairOptions();
  const rows = currentRows();
  const totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  state.page = Math.min(state.page, totalPages);
  const pageRows = rows.slice((state.page - 1) * state.pageSize, state.page * state.pageSize);
  renderMetrics(rows);
  elements.body.innerHTML = pageRows.map(rowHtml).join("");
  elements.resultCount.textContent = `${rows.length} 条 · 第 ${state.page}/${totalPages} 页`;
  elements.emptyState.hidden = rows.length > 0;
  elements.pagination.innerHTML = rows.length ? `
    <button class="page-button" type="button" data-page="prev" ${state.page === 1 ? "disabled" : ""} aria-label="上一页">‹</button>
    <span>${state.page} / ${totalPages}</span>
    <button class="page-button" type="button" data-page="next" ${state.page === totalPages ? "disabled" : ""} aria-label="下一页">›</button>
  ` : "";
}

function detailPanel(opportunity, venue) {
  const latest = opportunity.latest?.[venue.key];
  return `
    <section class="detail-panel">
      <h3>${escapeHtml(venueLabel(venue))}</h3>
      <div class="detail-line"><span>标的</span><strong>${escapeHtml(opportunity.symbols?.[venue.key] || "--")}</strong></div>
      <div class="detail-line"><span>上线时间</span><strong>${formatDate(opportunity.listings?.[venue.key], true)}</strong></div>
      <div class="detail-line"><span>最新 Funding</span><strong>${venue.market === "spot" ? "0%" : (latest ? formatPct(latest.rate_pct) : "--")}</strong></div>
      <div class="detail-line"><span>Funding 时间</span><strong>${latest ? formatDate(latest.time, true) : "--"}</strong></div>
      <div class="detail-line"><span>标记价格</span><strong>${latest?.price ? Number(latest.price).toLocaleString("zh-CN") : "--"}</strong></div>
      <div class="detail-line"><span>24h 成交额</span><strong>${formatTurnover(opportunity.turnover_24h_usdt?.[venue.key])} USDT</strong></div>
    </section>
  `;
}

function openDetail(id) {
  const opportunity = state.data.opportunities.find((item) => item.id === id);
  if (!opportunity) return;
  elements.detailPair.textContent = opportunity.venues.map(venueLabel).join(" / ");
  elements.detailTitle.textContent = opportunity.underlying;
  const windowRows = state.data.windows.map((window) => {
    const item = opportunity.windows[window];
    if (!item) return "";
    const rates = opportunity.venues.map((venue) => `<span>${escapeHtml(venueLabel(venue))} ${formatPct(annualizedRates(item)[venue.key])}</span>`).join("<br>");
    return `
      <tr>
        <td>${escapeHtml(state.data.window_labels[window])}</td>
        <td>${rates}</td>
        <td><span class="receive-text">空 ${escapeHtml(legLabel(item.short_leg))}</span><br><span class="pay-text">多 ${escapeHtml(legLabel(item.long_leg))}</span></td>
        <td class="spread-value ${signedValueClass(annualizedSignedDiff(item))}">${formatPct(annualizedSignedDiff(item))}<br><small>${escapeHtml(differenceBasis(opportunity))}</small></td>
        <td>${Number(item.elapsed_days || 0).toFixed(2)} 天 · ${isFullWindow(opportunity, item, window) ? "完整" : "不足"}</td>
      </tr>
    `;
  }).join("");
  elements.detailContent.innerHTML = `
    <div class="detail-grid">${opportunity.venues.map((venue) => detailPanel(opportunity, venue)).join("")}</div>
    <div class="detail-table-scroll">
      <table class="window-detail">
        <thead><tr><th>周期</th><th>年化 Funding</th><th>建议方向</th><th>带符号年化差值</th><th>数据</th></tr></thead>
        <tbody>${windowRows}</tbody>
      </table>
    </div>
  `;
  elements.dialog.showModal();
}

function exportCsv() {
  const rows = currentRows();
  const headers = ["underlying", "window", "short_exchange", "long_exchange", "difference_basis", "annualized_signed_diff_pct", "cumulative_signed_diff_pct", "elapsed_days", "common_start_time"];
  const exchangeHeaders = ["leg_1", "leg_1_latest_rate_pct", "leg_1_annualized_rate_pct", "leg_1_cumulative_rate_pct", "leg_1_turnover_24h_usdt", "leg_1_records", "leg_2", "leg_2_latest_rate_pct", "leg_2_annualized_rate_pct", "leg_2_cumulative_rate_pct", "leg_2_turnover_24h_usdt", "leg_2_records"];
  const allHeaders = headers.concat(exchangeHeaders);
  const lines = [allHeaders.join(",")];
  rows.forEach(({ opportunity, windowData }) => {
    const base = [opportunity.underlying, state.window, windowData.short_exchange, windowData.long_exchange, differenceBasis(opportunity), annualizedSignedDiff(windowData), windowData.signed_diff_pct, windowData.elapsed_days, opportunity.common_start_time];
    opportunity.venues.forEach((venue) => {
      base.push(venueLabel(venue), opportunity.latest?.[venue.key]?.rate_pct ?? "", annualizedRates(windowData)?.[venue.key] ?? "", windowData.rates_pct?.[venue.key] ?? "", opportunity.turnover_24h_usdt?.[venue.key] ?? "", windowData.records?.[venue.key] ?? "");
    });
    lines.push(base.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(","));
  });
  const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `tradifi_funding_${state.window}_${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function loadData() {
  elements.refresh.disabled = true;
  elements.refresh.textContent = "…";
  try {
    const dataUrl = new URL("./data/dashboard.json", window.location.href);
    dataUrl.searchParams.set("v", Date.now().toString());
    const response = await fetch(dataUrl, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    renderExchangeStatus();
    const metadata = Object.values(state.data.metadata_by_pair || {})[0];
    const turnoverTime = state.data.turnover_metadata?.generated_at_utc;
    elements.dataTime.textContent = metadata?.calculation_end_time_utc
      ? `Funding ${formatDate(metadata.calculation_end_time_utc, true)} · 成交额 ${formatDate(turnoverTime, true)}`
      : `读取于 ${formatDate(state.data.generated_at, true)}`;
    const dataErrors = state.data.errors.concat(state.data.turnover_metadata?.errors || []);
    elements.errorBanner.hidden = dataErrors.length === 0;
    elements.errorBanner.textContent = dataErrors.join("；");
    render();
  } catch (error) {
    elements.errorBanner.hidden = false;
    elements.errorBanner.textContent = `数据读取失败：${error.message}`;
  } finally {
    elements.refresh.disabled = false;
    elements.refresh.textContent = "↻";
  }
}

elements.windowTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-window]");
  if (!button) return;
  state.window = button.dataset.window;
  state.page = 1;
  render();
});
elements.strategyTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-strategy]");
  if (!button) return;
  state.strategy = button.dataset.strategy;
  state.longLeg = "all";
  state.shortLeg = "all";
  state.pair = "all";
  state.page = 1;
  const url = new URL(window.location.href);
  url.searchParams.set("strategy", state.strategy);
  window.history.replaceState({}, "", url);
  render();
});
elements.body.addEventListener("click", (event) => {
  const button = event.target.closest("[data-detail]");
  if (button) openDetail(button.dataset.detail);
});
elements.search.addEventListener("input", () => { state.search = elements.search.value; state.page = 1; render(); });
elements.minSpread.addEventListener("input", () => {
  const value = elements.minSpread.value.trim();
  state.minSpread = value === "" ? null : Number(value);
  state.page = 1;
  render();
});
elements.longLeg.addEventListener("change", () => { state.longLeg = elements.longLeg.value; state.page = 1; render(); });
elements.shortLeg.addEventListener("change", () => { state.shortLeg = elements.shortLeg.value; state.page = 1; render(); });
elements.pair.addEventListener("change", () => { state.pair = elements.pair.value; state.page = 1; render(); });
elements.sort.addEventListener("change", () => { state.sort = elements.sort.value; state.page = 1; render(); });
elements.fullOnly.addEventListener("change", () => { state.fullOnly = elements.fullOnly.checked; state.page = 1; render(); });
elements.refresh.addEventListener("click", loadData);
elements.export.addEventListener("click", exportCsv);
elements.reset.addEventListener("click", () => {
  state.search = "";
  state.minSpread = null;
  state.longLeg = "all";
  state.shortLeg = "all";
  state.pair = "all";
  state.fullOnly = false;
  state.sort = "spread_desc";
  elements.search.value = "";
  elements.minSpread.value = "";
  elements.longLeg.value = "all";
  elements.shortLeg.value = "all";
  elements.pair.value = "all";
  elements.fullOnly.checked = false;
  elements.sort.value = "spread_desc";
  render();
});
elements.pagination.addEventListener("click", (event) => {
  const button = event.target.closest("[data-page]");
  if (!button || button.disabled) return;
  state.page += button.dataset.page === "next" ? 1 : -1;
  render();
  document.querySelector(".results").scrollIntoView({ behavior: "smooth", block: "start" });
});
elements.mobileFilterToggle.addEventListener("click", () => {
  const collapsed = elements.filters.classList.toggle("mobile-collapsed");
  elements.mobileFilterToggle.setAttribute("aria-expanded", String(!collapsed));
  elements.mobileFilterToggle.title = collapsed ? "展开筛选" : "收起筛选";
});
elements.closeDialog.addEventListener("click", () => elements.dialog.close());
elements.dialog.addEventListener("click", (event) => { if (event.target === elements.dialog) elements.dialog.close(); });

if (window.matchMedia("(max-width: 620px)").matches) elements.filters.classList.add("mobile-collapsed");
loadData();
