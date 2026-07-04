(function () {
  const charts = new Map();

  const RENDERER_TYPES = Object.freeze({
    status_line: { role: "page status and context strip" },
    metric_cards: { role: "point-in-time metrics" },
    "echarts.line": { role: "time trend" },
    "echarts.stacked_bar": { role: "composition over buckets" },
    "echarts.horizontal_bar": { role: "ranked values" },
    "html.table": { role: "row details" },
    "html.signal_list": { role: "short operational signals" },
    "html.summary_table": { role: "key/value summary" },
    "html.json_block": { role: "structured diagnostic summary" },
  });

  const RENDERERS = Object.freeze({
    status_line: renderStatusLineWidget,
    metric_cards: renderMetricCardsWidget,
    "echarts.line": renderLineWidget,
    "echarts.stacked_bar": renderStackedBarWidget,
    "echarts.horizontal_bar": renderHorizontalBarWidget,
    "html.table": renderTableWidget,
    "html.signal_list": renderSignalListWidget,
    "html.summary_table": renderSummaryTableWidget,
    "html.json_block": renderJsonBlockWidget,
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

  function metricValue(value) {
    if (value === null || value === undefined || value === "") return "-";
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : compact(value);
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

  function targetElement(target) {
    return typeof target === "string" ? $(targetSelector(target)) : target;
  }

  function widgetHostId(widgetId) {
    return `widget-${String(widgetId).replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
  }

  function resolveValue(spec, payload) {
    if (!spec) return undefined;
    let value;
    if (typeof spec.value === "function") {
      value = spec.value(payload);
    } else if ("value" in spec) {
      value = spec.value;
    } else {
      value = getPath(payload, spec.valuePath);
    }
    if ((value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0)) && "empty" in spec) {
      return spec.empty;
    }
    if (spec.format === "date") return formatDate(value);
    if (spec.format === "join") return Array.isArray(value) ? value.join(spec.separator || ", ") : value;
    if (spec.format === "json") return JSON.stringify(value, null, 2);
    return value;
  }

  function widgetRows(widget, payload, adapters) {
    if (!widget.adapter) {
      return getPath(payload, widget.dataPath);
    }
    const adapter = adapters?.[widget.adapter];
    if (!adapter) {
      throw new Error(`Unknown widget adapter: ${widget.adapter}`);
    }
    return adapter(payload);
  }

  function renderWidget(widget, target, payload, adapters) {
    const renderer = RENDERERS[widget.rendererType];
    if (!renderer) {
      throw new Error(`Unknown rendererType: ${widget.rendererType}`);
    }
    renderer(widget, target, payload, adapters);
  }

  function renderWidgetLayout(layout, widgetRegistry, payload, adapters) {
    const root = targetElement(layout.target);
    if (!root) return;
    const placements = layout.placements || [];
    const inlinePlacements = placements.filter((placement) => !placement.target);
    const groupHtml = (layout.groups || [])
      .map((group) => {
        const groupPlacements = inlinePlacements.filter((placement) => placement.group === group.key);
        if (groupPlacements.length === 0) return "";
        const body = groupPlacements.map((placement) => renderPlacementShell(placement, widgetRegistry)).join("");
        if (!group.title && !group.className) return body;
        const bodyHtml = group.className ? `<div class="${escapeHtml(group.className)}">${body}</div>` : body;
        if (!group.title) return bodyHtml;
        const subtitle = group.subtitle ? `<span>${escapeHtml(group.subtitle)}</span>` : "";
        return `
          <section class="layout-group">
            <div class="layout-group-head">
              <h2>${escapeHtml(group.title)}</h2>
              ${subtitle}
            </div>
            ${bodyHtml}
          </section>
        `;
      })
      .join("");
    root.innerHTML = groupHtml;
    for (const placement of placements) {
      const widget = widgetRegistry[placement.widgetId];
      if (!widget) {
        throw new Error(`Unknown widgetId: ${placement.widgetId}`);
      }
      renderWidget(widget, placement.target || widgetHostId(placement.widgetId), payload, adapters);
    }
  }

  function renderPlacementShell(placement, widgetRegistry) {
    const widget = widgetRegistry[placement.widgetId];
    if (!widget) {
      throw new Error(`Unknown widgetId: ${placement.widgetId}`);
    }
    const host = `<div id="${escapeHtml(widgetHostId(placement.widgetId))}" class="${escapeHtml(placement.hostClass || "")}"></div>`;
    if (placement.chrome === "bare") return host;
    return `
      <section class="${escapeHtml(placement.panelClass || "panel")}">
        <div class="panel-head">
          <h3>${escapeHtml(placement.title || widget.title || widget.id || placement.widgetId)}</h3>
          <span>${escapeHtml(placement.subtitle || widget.subtitle || widget.question || "")}</span>
        </div>
        ${host}
      </section>
    `;
  }

  function renderStatusLineWidget(widget, target, payload) {
    const el = targetElement(target);
    if (!el) return;
    el.innerHTML = (widget.items || [])
      .map((item) => {
        const badge = item.badgePath ? `${statusBadge(getPath(payload, item.badgePath) || item.badgeDefault || "neutral")} ` : "";
        const value = resolveValue(item, payload);
        return `<div>${badge}<strong>${escapeHtml(item.label)}:</strong> ${escapeHtml(compact(value))}</div>`;
      })
      .join("");
  }

  function renderMetricCardsWidget(widget, target, payload) {
    const el = targetElement(target);
    if (!el) return;
    el.innerHTML = (widget.cards || [])
      .map((card) => {
        const value = resolveValue(card, payload);
        const sub = card.subPath ? `${card.subPrefix || ""} ${compact(getPath(payload, card.subPath))}`.trim() : card.sub;
        return `
        <article class="stat-card">
          <div class="label">${escapeHtml(card.label)}</div>
          <div class="value">${escapeHtml(metricValue(value))}</div>
          <div class="sub">${escapeHtml(sub)}</div>
        </article>
      `;
      })
      .join("");
  }

  function renderTable(target, rows, columns, emptyText) {
    const el = targetElement(target);
    if (!el) return;
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

  function renderLineWidget(widget, target, payload, adapters) {
    const rows = widgetRows(widget, payload, adapters) || [];
    if (!window.echarts) {
      renderFallbackChart(target, rows, widget.xField, widget.series.map((series) => series.field));
      return;
    }
    const chart = ensureChart(target);
    const labels = rows.map((row) => formatDate(row[widget.xField]));
    chart.setOption({
      color: widget.colors,
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      grid: { left: 36, right: 18, top: 42, bottom: 38 },
      xAxis: { type: "category", data: labels, boundaryGap: false },
      yAxis: { type: "value", minInterval: 1 },
      series: widget.series.map((series) => ({
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

  function renderStackedBarWidget(widget, target, payload, adapters) {
    const rows = widgetRows(widget, payload, adapters) || [];
    if (!window.echarts) {
      renderFallbackChart(target, rows, widget.xField, widget.series.map((series) => series.field));
      return;
    }
    const chart = ensureChart(target);
    chart.setOption({
      color: widget.colors,
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      grid: { left: 42, right: 18, top: 42, bottom: 38 },
      xAxis: { type: "category", data: rows.map((row) => compact(row[widget.xField])) },
      yAxis: { type: "value", minInterval: 1 },
      series: widget.series.map((series) => ({
        name: series.name,
        type: "bar",
        stack: widget.stack || "total",
        data: rows.map((row) => number(row[series.field])),
      })),
    });
  }

  function renderHorizontalBarWidget(widget, target, payload, adapters) {
    const rows = (widgetRows(widget, payload, adapters) || []).slice(0, widget.maxItems || undefined);
    if (!window.echarts) {
      renderFallbackChart(target, rows, widget.labelField, [widget.valueField]);
      return;
    }
    const chart = ensureChart(target);
    const suffix = widget.valueSuffix || "";
    chart.setOption({
      color: [widget.color],
      tooltip: {
        trigger: "axis",
        valueFormatter: (value) => `${Number(value || 0).toFixed(suffix ? 2 : 0)}${suffix}`,
      },
      grid: { left: widget.left || 110, right: 18, top: 18, bottom: 24 },
      xAxis: {
        type: "value",
        minInterval: suffix ? undefined : 1,
        axisLabel: { formatter: suffix ? `{value}${suffix}` : "{value}" },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: rows.map((row) => compact(row[widget.labelField])),
      },
      series: [{ type: "bar", data: rows.map((row) => number(row[widget.valueField])), barWidth: 18 }],
    });
  }

  function renderTableWidget(widget, target, payload, adapters) {
    const rows = widgetRows(widget, payload, adapters) || [];
    renderTable(target, rows, widget.columns, widget.emptyText);
  }

  function renderSignalListWidget(widget, target, payload, adapters) {
    const el = targetElement(target);
    if (!el) return;
    const rows = widgetRows(widget, payload, adapters) || [];
    if (rows.length === 0) {
      el.innerHTML = `<div class="empty-state">${escapeHtml(widget.emptyText || "没有信号")}</div>`;
      return;
    }
    el.innerHTML = rows.map((row) => `<div class="signal-item"><code>${escapeHtml(compact(row))}</code></div>`).join("");
  }

  function renderSummaryTableWidget(widget, target, payload) {
    const rows = (widget.rows || []).map((row) => ({ label: row.label, value: resolveValue(row, payload) }));
    renderTable(
      target,
      rows,
      [
        { key: "label", label: "字段" },
        { key: "value", label: "值", wrap: true },
      ],
      widget.emptyText || "没有摘要"
    );
  }

  function renderJsonBlockWidget(widget, target, payload) {
    const el = targetElement(target);
    if (!el) return;
    const value = resolveValue(widget, payload);
    el.innerHTML = `<pre class="json-block">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  }

  function resizeCharts() {
    for (const chart of charts.values()) chart.resize();
  }

  window.OpsDashboardRenderers = Object.freeze({
    RENDERER_TYPES,
    RENDERERS,
    compact,
    escapeHtml,
    formatDate,
    getPath,
    number,
    renderTable,
    renderWidget,
    renderWidgetLayout,
    resizeCharts,
    statusBadge,
    widgetHostId,
  });
})();
