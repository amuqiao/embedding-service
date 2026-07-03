(function () {
  const Charts = window.OpsDashboardCharts;
  const { formatDate, renderPanels, statusBadge } = Charts;

  const EXAMPLE_PANEL_REGISTRY = Object.freeze({
    examples: [
      {
        key: "sample_stats",
        question: "现在怎么样",
        chartType: "stat_card",
        target: "example-stats",
        cards: [
          { label: "current", valuePath: "summary.current", sub: "point-in-time" },
          { label: "incoming", valuePath: "summary.incoming", sub: "window" },
          { label: "completed", valuePath: "summary.completed", sub: "window" },
          { label: "errored", valuePath: "summary.errored", sub: "window" },
          { label: "waiting", valuePath: "summary.waiting", sub: "queue" },
          { label: "capacity", valuePath: "summary.capacity", sub: "headroom" },
        ],
      },
      {
        key: "sample_line",
        question: "趋势如何",
        chartType: "line",
        target: "example-line",
        dataPath: "trend",
        xField: "time",
        series: [
          { name: "incoming", field: "incoming" },
          { name: "completed", field: "completed" },
          { name: "errored", field: "errored" },
        ],
        colors: ["#1769aa", "#12805c", "#c9342f"],
      },
      {
        key: "sample_stacked",
        question: "构成随时间怎么变",
        chartType: "stacked_bar",
        target: "example-stacked",
        dataPath: "composition",
        xField: "bucket",
        series: [
          { name: "alpha", field: "alpha" },
          { name: "beta", field: "beta" },
          { name: "gamma", field: "gamma" },
        ],
        colors: ["#1769aa", "#087f8c", "#6554c0"],
      },
      {
        key: "sample_rank",
        question: "谁最多",
        chartType: "horizontal_bar",
        target: "example-rank",
        dataPath: "rank",
        labelField: "label",
        valueField: "value",
        maxItems: 6,
        color: "#087f8c",
        left: 96,
      },
      {
        key: "sample_table",
        question: "具体是哪几个",
        chartType: "table",
        target: "example-table",
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
    ],
  });

  const EXAMPLE_PAYLOAD = Object.freeze({
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
        message: "Long text wraps inside table cells without changing the chart contract.",
      },
      {
        label: "sample-c",
        status: "failed",
        value: 7,
        updated_at: "2026-07-03T09:15:00+08:00",
        message: "Status badges are shared helpers, not business-specific fields.",
      },
    ],
  });

  function init() {
    renderPanels(EXAMPLE_PANEL_REGISTRY, "examples", EXAMPLE_PAYLOAD);
  }

  window.addEventListener("resize", Charts.resizeCharts);
  window.addEventListener("DOMContentLoaded", init);
})();
