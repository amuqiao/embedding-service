(function () {
  const charts = new Map();

  const CHART_TYPES = Object.freeze({
    stat_card: { answers: "现在怎么样" },
    line: { answers: "趋势如何" },
    stacked_bar: { answers: "构成随时间怎么变" },
    horizontal_bar: { answers: "谁最多或哪段最重" },
    table: { answers: "具体是哪几个" },
  });

  const CHART_RENDERERS = Object.freeze({
    stat_card: renderStatCards,
    line: renderLinePanel,
    stacked_bar: renderStackedBarPanel,
    horizontal_bar: renderHorizontalBarPanel,
    table: renderTablePanel,
  });

  function $(selector) {
    return document.querySelector(selector);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDate(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function number(value) {
    if (value === null || value === undefined) return 0;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : 0;
  }

  function compact(value) {
    if (value === null || value === undefined || value === "") return "-";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function statusBadge(status) {
    const normalized = status || "neutral";
    return `<span class="badge ${escapeHtml(normalized)}">${escapeHtml(normalized)}</span>`;
  }

  function getPath(source, path) {
    if (!path) return source;
    return String(path)
      .split(".")
      .reduce((current, key) => (current === null || current === undefined ? undefined : current[key]), source);
  }

  function targetId(target) {
    return String(target).replace(/^#/, "");
  }

  function targetSelector(target) {
    return String(target).startsWith("#") ? String(target) : `#${target}`;
  }

  function panelRows(panel, payload, adapters) {
    if (!panel.adapter) {
      return getPath(payload, panel.dataPath);
    }
    const adapter = adapters?.[panel.adapter];
    if (!adapter) {
      throw new Error(`Unknown panel adapter: ${panel.adapter}`);
    }
    return adapter(payload);
  }

  function renderPanels(registry, section, payload, adapters) {
    for (const panel of registry[section] || []) {
      renderPanel(panel, payload, adapters);
    }
  }

  function renderPanel(panel, payload, adapters) {
    const renderer = CHART_RENDERERS[panel.chartType];
    if (!renderer) {
      throw new Error(`Unknown chartType: ${panel.chartType}`);
    }
    renderer(panel, payload, adapters);
  }

  function renderStatCards(panel, payload) {
    $(targetSelector(panel.target)).innerHTML = panel.cards
      .map((card) => {
        const value = getPath(payload, card.valuePath);
        const sub = card.subPath ? `${card.subPrefix || ""} ${compact(getPath(payload, card.subPath))}`.trim() : card.sub;
        return `
        <article class="stat-card">
          <div class="label">${escapeHtml(card.label)}</div>
          <div class="value">${escapeHtml(number(value))}</div>
          <div class="sub">${escapeHtml(sub)}</div>
        </article>
      `;
      })
      .join("");
  }

  function renderTable(target, rows, columns, emptyText) {
    const el = typeof target === "string" ? $(target) : target;
    if (!rows || rows.length === 0) {
      el.innerHTML = `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
      return;
    }
    const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
    const body = rows
      .map((row) => `
        <tr>
          ${columns.map((column) => {
            const raw = column.value ? column.value(row) : row[column.key];
            const html = column.render ? column.render(raw, row) : escapeHtml(compact(raw));
            return `<td class="${column.wrap ? "wrap" : ""}">${html}</td>`;
          }).join("")}
        </tr>
      `)
      .join("");
    el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function ensureChart(target) {
    const id = targetId(target);
    const el = document.getElementById(id);
    if (!el || !window.echarts) return null;
    const previous = charts.get(id);
    if (previous) previous.dispose();
    const chart = window.echarts.init(el, null, { renderer: "canvas" });
    charts.set(id, chart);
    return chart;
  }

  function renderFallbackChart(target, rows, labelKey, valueKeys) {
    const el = document.getElementById(targetId(target));
    if (!el) return;
    const safeRows = rows || [];
    const max = Math.max(1, ...safeRows.flatMap((row) => valueKeys.map((key) => number(row[key]))));
    el.innerHTML = `
      <div class="fallback-chart">
        ${safeRows.map((row) => `
          <div class="fallback-row">
            <span>${escapeHtml(compact(row[labelKey]))}</span>
            <div>
              ${valueKeys.map((key) => `
                <i title="${escapeHtml(key)}" style="width:${Math.max(2, (number(row[key]) / max) * 100)}%"></i>
              `).join("")}
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderLinePanel(panel, payload, adapters) {
    const rows = panelRows(panel, payload, adapters) || [];
    if (!window.echarts) {
      renderFallbackChart(panel.target, rows, panel.xField, panel.series.map((series) => series.field));
      return;
    }
    const chart = ensureChart(panel.target);
    const labels = rows.map((row) => formatDate(row[panel.xField]));
    chart.setOption({
      color: panel.colors,
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      grid: { left: 36, right: 18, top: 42, bottom: 38 },
      xAxis: { type: "category", data: labels, boundaryGap: false },
      yAxis: { type: "value", minInterval: 1 },
      series: panel.series.map((series) => ({
        name: series.name,
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 3 },
        areaStyle: { opacity: 0.08 },
        data: rows.map((row) => number(row[series.field])),
      })),
    });
  }

  function renderStackedBarPanel(panel, payload, adapters) {
    const rows = panelRows(panel, payload, adapters) || [];
    if (!window.echarts) {
      renderFallbackChart(panel.target, rows, panel.xField, panel.series.map((series) => series.field));
      return;
    }
    const chart = ensureChart(panel.target);
    chart.setOption({
      color: panel.colors,
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      grid: { left: 42, right: 18, top: 42, bottom: 38 },
      xAxis: { type: "category", data: rows.map((row) => compact(row[panel.xField])) },
      yAxis: { type: "value", minInterval: 1 },
      series: panel.series.map((series) => ({
        name: series.name,
        type: "bar",
        stack: panel.stack || "total",
        data: rows.map((row) => number(row[series.field])),
      })),
    });
  }

  function renderHorizontalBarPanel(panel, payload, adapters) {
    const rows = (panelRows(panel, payload, adapters) || []).slice(0, panel.maxItems || undefined);
    if (!window.echarts) {
      renderFallbackChart(panel.target, rows, panel.labelField, [panel.valueField]);
      return;
    }
    const chart = ensureChart(panel.target);
    const suffix = panel.valueSuffix || "";
    chart.setOption({
      color: [panel.color],
      tooltip: {
        trigger: "axis",
        valueFormatter: (value) => `${Number(value || 0).toFixed(suffix ? 2 : 0)}${suffix}`,
      },
      grid: { left: panel.left || 110, right: 18, top: 18, bottom: 24 },
      xAxis: {
        type: "value",
        minInterval: suffix ? undefined : 1,
        axisLabel: { formatter: suffix ? `{value}${suffix}` : "{value}" },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: rows.map((row) => compact(row[panel.labelField])),
      },
      series: [{ type: "bar", data: rows.map((row) => number(row[panel.valueField])), barWidth: 18 }],
    });
  }

  function renderTablePanel(panel, payload, adapters) {
    const rows = panelRows(panel, payload, adapters) || [];
    renderTable(targetSelector(panel.target), rows, panel.columns, panel.emptyText);
  }

  function resizeCharts() {
    for (const chart of charts.values()) chart.resize();
  }

  window.OpsDashboardCharts = Object.freeze({
    CHART_TYPES,
    CHART_RENDERERS,
    compact,
    escapeHtml,
    formatDate,
    getPath,
    number,
    renderPanel,
    renderPanels,
    resizeCharts,
    statusBadge,
  });
})();
