(function () {
  const Renderers = window.OpsDashboardRenderers;
  const { formatDate, renderWidgetLayout, statusBadge } = Renderers;

  const EXAMPLE_WIDGET_REGISTRY = Object.freeze({
    "examples.status": {
      rendererType: "status_line",
      items: [
        { label: "来源", badgeDefault: "neutral", badgePath: "status", value: "静态 Renderer 样例" },
        { label: "用途", value: "Renderer 合同示例" },
      ],
    },
    "examples.metrics": {
      rendererType: "metric_cards",
      title: "指标卡片",
      question: "单点指标样例",
      cards: [
        { label: "当前", valuePath: "summary.current", sub: "当前点" },
        { label: "进入", valuePath: "summary.incoming", sub: "时间窗口" },
        { label: "完成", valuePath: "summary.completed", sub: "时间窗口" },
        { label: "错误", valuePath: "summary.errored", sub: "时间窗口" },
        { label: "等待", valuePath: "summary.waiting", sub: "队列" },
        { label: "容量", valuePath: "summary.capacity", sub: "headroom" },
      ],
    },
    "examples.line": {
      rendererType: "echarts.line",
      title: "趋势折线",
      question: "趋势样例",
      dataPath: "trend",
      xField: "time",
      series: [
        { name: "进入", field: "incoming" },
        { name: "完成", field: "completed" },
        { name: "错误", field: "errored" },
      ],
      colors: ["#1769aa", "#12805c", "#c9342f"],
    },
    "examples.stacked": {
      rendererType: "echarts.stacked_bar",
      title: "堆叠柱状图",
      question: "构成样例",
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
      title: "横向排行",
      question: "Top N 样例",
      dataPath: "rank",
      labelField: "label",
      valueField: "value",
      maxItems: 6,
      color: "#087f8c",
      left: 96,
    },
    "examples.table": {
      rendererType: "html.table",
      title: "明细表格",
      question: "明细样例",
      dataPath: "details",
      emptyText: "没有样例明细",
      columns: [
        { key: "label", label: "标签" },
        { key: "status", label: "状态", render: statusBadge },
        { key: "value", label: "值" },
        { key: "updated_at", label: "更新时间", value: (row) => formatDate(row.updated_at) },
        { key: "message", label: "消息" },
      ],
    },
    "examples.signals": {
      rendererType: "html.signal_list",
      title: "信号列表",
      question: "短消息",
      dataPath: "signals",
      emptyText: "没有样例信号",
    },
    "examples.summary": {
      rendererType: "html.summary_table",
      title: "摘要表",
      question: "键值样例",
      rows: [
        { label: "sample_id", valuePath: "summary_meta.sample_id" },
        { label: "mode", valuePath: "summary_meta.mode" },
        { label: "更新时间", valuePath: "summary_meta.updated_at", format: "date" },
      ],
    },
    "examples.json": {
      rendererType: "html.json_block",
      title: "JSON 块",
      question: "结构化摘要",
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
        message: "Renderer 接收 rows、columns 和单元格渲染函数。",
      },
      {
        label: "sample-b",
        status: "warning",
        value: 18,
        updated_at: "2026-07-03T09:18:00+08:00",
        message: "长文本会在表格单元格内换行，不改变 Renderer 合同。",
      },
      {
        label: "sample-c",
        status: "failed",
        value: 7,
        updated_at: "2026-07-03T09:15:00+08:00",
        message: "状态 badge 是共享 helper，不绑定具体业务字段。",
      },
    ],
    signals: ["第一条静态信号", "第二条静态信号", "Renderer 样例保持通用"],
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
