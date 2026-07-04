(function () {
  const Renderers = window.OpsDashboardRenderers;
  const { formatDate, renderWidgetLayout, statusBadge } = Renderers;

  const EXAMPLE_WIDGET_REGISTRY = Object.freeze({
    "examples.status": {
      rendererType: "status_line",
      items: [
        { label: "source", badgeDefault: "neutral", badgePath: "status", value: "static renderer fixtures" },
        { label: "purpose", value: "renderer contract" },
      ],
    },
    "examples.metrics": {
      rendererType: "metric_cards",
      title: "Metric Cards",
      question: "point-in-time sample",
      cards: [
        { label: "current", valuePath: "summary.current", sub: "point-in-time" },
        { label: "incoming", valuePath: "summary.incoming", sub: "window" },
        { label: "completed", valuePath: "summary.completed", sub: "window" },
        { label: "errored", valuePath: "summary.errored", sub: "window" },
        { label: "waiting", valuePath: "summary.waiting", sub: "queue" },
        { label: "capacity", valuePath: "summary.capacity", sub: "headroom" },
      ],
    },
    "examples.line": {
      rendererType: "echarts.line",
      title: "Line",
      question: "trend sample",
      dataPath: "trend",
      xField: "time",
      series: [
        { name: "incoming", field: "incoming" },
        { name: "completed", field: "completed" },
        { name: "errored", field: "errored" },
      ],
      colors: ["#1769aa", "#12805c", "#c9342f"],
    },
    "examples.stacked": {
      rendererType: "echarts.stacked_bar",
      title: "Stacked Bar",
      question: "composition sample",
      dataPath: "composition",
      xField: "bucket",
      series: [
        { name: "alpha", field: "alpha" },
        { name: "beta", field: "beta" },
        { name: "gamma", field: "gamma" },
      ],
      colors: ["#1769aa", "#087f8c", "#6554c0"],
    },
    "examples.rank": {
      rendererType: "echarts.horizontal_bar",
      title: "Horizontal Bar",
      question: "top-n sample",
      dataPath: "rank",
      labelField: "label",
      valueField: "value",
      maxItems: 6,
      color: "#087f8c",
      left: 96,
    },
    "examples.table": {
      rendererType: "html.table",
      title: "Table",
      question: "detail sample",
      dataPath: "details",
      emptyText: "没有样例明细",
      columns: [
        { key: "label", label: "label" },
        { key: "status", label: "status", render: statusBadge },
        { key: "value", label: "value" },
        { key: "updated_at", label: "updated", value: (row) => formatDate(row.updated_at) },
        { key: "message", label: "message", wrap: true },
      ],
    },
    "examples.signals": {
      rendererType: "html.signal_list",
      title: "Signal List",
      question: "short messages",
      dataPath: "signals",
      emptyText: "没有样例信号",
    },
    "examples.summary": {
      rendererType: "html.summary_table",
      title: "Summary Table",
      question: "key/value sample",
      rows: [
        { label: "sample_id", valuePath: "summary_meta.sample_id" },
        { label: "mode", valuePath: "summary_meta.mode" },
        { label: "updated_at", valuePath: "summary_meta.updated_at", format: "date" },
      ],
    },
    "examples.json": {
      rendererType: "html.json_block",
      title: "JSON Block",
      question: "structured summary",
      valuePath: "diagnostic",
    },
  });

  const EXAMPLE_LAYOUT_REGISTRY = Object.freeze({
    examples: {
      target: "example-root",
      groups: [
        { key: "summary" },
        { key: "main", className: "panel-grid" },
      ],
      placements: [
        { widgetId: "examples.status", target: "example-status" },
        { widgetId: "examples.metrics", group: "summary", chrome: "bare", hostClass: "stat-grid" },
        { widgetId: "examples.line", group: "main", hostClass: "chart" },
        { widgetId: "examples.stacked", group: "main", hostClass: "chart" },
        { widgetId: "examples.rank", group: "main", hostClass: "chart chart-compact" },
        { widgetId: "examples.table", group: "main", hostClass: "table-wrap" },
        { widgetId: "examples.signals", group: "main", hostClass: "signal-list" },
        { widgetId: "examples.summary", group: "main", hostClass: "table-wrap" },
        { widgetId: "examples.json", group: "main" },
      ],
    },
  });

  const EXAMPLE_PAYLOAD = Object.freeze({
    status: "neutral",
    summary: {
      current: 18,
      incoming: 124,
      completed: 117,
      errored: 7,
      waiting: 3,
      capacity: 82,
    },
    trend: [
      { time: "2026-07-03T09:00:00+08:00", incoming: 12, completed: 9, errored: 1 },
      { time: "2026-07-03T09:05:00+08:00", incoming: 18, completed: 13, errored: 2 },
      { time: "2026-07-03T09:10:00+08:00", incoming: 16, completed: 16, errored: 0 },
      { time: "2026-07-03T09:15:00+08:00", incoming: 24, completed: 19, errored: 3 },
      { time: "2026-07-03T09:20:00+08:00", incoming: 20, completed: 21, errored: 1 },
    ],
    composition: [
      { bucket: "09:00", alpha: 7, beta: 3, gamma: 2 },
      { bucket: "09:05", alpha: 9, beta: 5, gamma: 4 },
      { bucket: "09:10", alpha: 6, beta: 7, gamma: 3 },
      { bucket: "09:15", alpha: 12, beta: 8, gamma: 4 },
      { bucket: "09:20", alpha: 10, beta: 6, gamma: 4 },
    ],
    rank: [
      { label: "category-a", value: 42 },
      { label: "category-b", value: 34 },
      { label: "category-c", value: 23 },
      { label: "category-d", value: 18 },
      { label: "category-e", value: 11 },
    ],
    details: [
      {
        label: "sample-a",
        status: "succeeded",
        value: 42,
        updated_at: "2026-07-03T09:20:00+08:00",
        message: "Renderer receives rows, columns and cell render functions.",
      },
      {
        label: "sample-b",
        status: "warning",
        value: 18,
        updated_at: "2026-07-03T09:18:00+08:00",
        message: "Long text wraps inside table cells without changing the renderer contract.",
      },
      {
        label: "sample-c",
        status: "failed",
        value: 7,
        updated_at: "2026-07-03T09:15:00+08:00",
        message: "Status badges are shared helpers, not business-specific fields.",
      },
    ],
    signals: ["first static signal", "second static signal", "renderer fixtures remain generic"],
    summary_meta: {
      sample_id: "sample-renderer-contract",
      mode: "static",
      updated_at: "2026-07-03T09:20:00+08:00",
    },
    diagnostic: {
      present: true,
      shape: "generic",
      fields: ["alpha", "beta", "gamma"],
    },
  });

  function init() {
    renderWidgetLayout(EXAMPLE_LAYOUT_REGISTRY.examples, EXAMPLE_WIDGET_REGISTRY, EXAMPLE_PAYLOAD);
  }

  window.addEventListener("resize", Renderers.resizeCharts);
  window.addEventListener("DOMContentLoaded", init);
})();
