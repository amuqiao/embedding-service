(function () {
  const BASE = "/internal/jobs-dashboard";
  const state = {
    section: "overview",
    config: null,
    refreshTimer: null,
    pageControls: {},
  };

  const Renderers = window.OpsDashboardRenderers;
  const {
    compact,
    escapeHtml,
    formatDate,
    getPath,
    renderWidgetLayout,
    resizeCharts,
    statusBadge,
  } = Renderers;

  const DATA_SOURCE_REGISTRY = Object.freeze({
    overview: {
      sectionKey: "overview",
      route: `${BASE}/sections/overview/data`,
      usesFilters: true,
    },
    recent_jobs: {
      sectionKey: "recent_jobs",
      route: `${BASE}/sections/recent_jobs/data`,
      usesFilters: true,
    },
    flow_capacity: {
      sectionKey: "flow_capacity",
      route: `${BASE}/sections/flow_capacity/data`,
      usesFilters: true,
    },
    failures_callbacks: {
      sectionKey: "failures_callbacks",
      route: `${BASE}/sections/failures_callbacks/data`,
      usesFilters: true,
    },
    job_trace: {
      sectionKey: "job_trace",
      route: `${BASE}/jobs/{job_id}/data`,
    },
  });

  const PAGE_CONTROL_REGISTRY = Object.freeze({
    recent_jobs: [
      {
        key: "status",
        type: "select",
        binding: "query",
        param: "status",
        label: "status",
        default: "all",
        options: ["all", "queued", "running", "succeeded", "failed"],
      },
      {
        key: "client_request_id",
        type: "text",
        binding: "query",
        param: "client_request_id",
        label: "client_request_id",
      },
      {
        key: "limit",
        type: "number",
        binding: "query",
        param: "limit",
        label: "limit",
        default: 20,
        min: 1,
        max: 100,
      },
    ],
    job_trace: [
      {
        key: "job_id",
        type: "text",
        binding: "route",
        param: "job_id",
        label: "job_id",
      },
      {
        key: "limit",
        type: "number",
        binding: "query",
        param: "limit",
        label: "limit",
        default: 100,
        min: 1,
        max: 200,
      },
    ],
  });

  const WIDGET_REGISTRY = Object.freeze({
    "overview.status": {
      rendererType: "status_line",
      dataSource: "overview",
      items: [
        {
          label: "reasons",
          badgePath: "health.status",
          badgeDefault: "ok",
          valuePath: "health.reasons",
          format: "join",
          empty: "no active warning",
        },
        { label: "generated_at", valuePath: "generated_at", format: "date" },
      ],
    },
    "overview.current_state": {
      title: "Current State",
      question: "现在怎么样",
      rendererType: "metric_cards",
      dataSource: "overview",
      cards: [
        {
          label: "active_jobs",
          valuePath: "capacity.current.active_jobs",
          subPrefix: "headroom",
          subPath: "capacity.current.headroom",
        },
        { label: "queued", valuePath: "summary.jobs.queued", sub: "root window" },
        { label: "running_active", valuePath: "summary.jobs.running_active", sub: "active attempts" },
        { label: "succeeded", valuePath: "summary.jobs.succeeded", sub: "window" },
        {
          label: "success_rate %",
          value: (payload) => {
            const rate = getPath(payload, "summary.jobs.success_rate");
            return rate === null || rate === undefined ? null : Math.round(Number(rate) * 1000) / 10;
          },
          sub: "terminal window",
        },
        { label: "failed", valuePath: "summary.jobs.failed", sub: "window" },
        { label: "callback_delivered", valuePath: "summary.callbacks.delivered", sub: "window" },
        { label: "stuck", valuePath: "stuck.total", sub: "older than 10m" },
        { label: "callback_due", valuePath: "summary.callbacks.due", sub: "due now" },
      ],
    },
    "overview.ingress_trend": {
      title: "Ingress",
      question: "created / terminal / failed",
      rendererType: "echarts.line",
      dataSource: "overview",
      dataPath: "ingress",
      xField: "bucket_at",
      series: [
        { name: "created", field: "created" },
        { name: "terminal", field: "terminal" },
        { name: "failed", field: "failed" },
      ],
      colors: ["#1769aa", "#12805c", "#c9342f"],
    },
    "overview.latency_p95": {
      title: "Latency p95",
      question: "queue / run / lifecycle",
      rendererType: "echarts.horizontal_bar",
      dataSource: "overview",
      adapter: "latency_p95_rows",
      labelField: "label",
      valueField: "value",
      valueSuffix: "s",
      color: "#087f8c",
      left: 84,
    },
    "overview.health_signals": {
      title: "Health Signals",
      question: "next checks",
      rendererType: "html.signal_list",
      dataSource: "overview",
      dataPath: "health.next_checks",
      emptyText: "没有后续检查",
    },
    "overview.stuck_samples": {
      title: "Stuck 样本",
      question: "older than 10m",
      rendererType: "html.table",
      dataSource: "overview",
      dataPath: "stuck.sample",
      emptyText: "未发现 stuck 样本",
      columns: [
        { key: "issue", label: "issue" },
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "job_status", label: "status", render: statusBadge },
        { key: "job_type", label: "job_type" },
        { key: "since_at", label: "since_at", value: (row) => formatDate(row.since_at) },
      ],
    },
    "recent_jobs.status": {
      rendererType: "status_line",
      dataSource: "recent_jobs",
      items: [
        { label: "section", badgeDefault: "neutral", badgePath: "health.status", value: "recent_jobs" },
        { label: "generated_at", valuePath: "generated_at", format: "date" },
      ],
    },
    "recent_jobs.summary_cards": {
      title: "Result Cards",
      question: "current filter",
      rendererType: "metric_cards",
      dataSource: "recent_jobs",
      cards: [
        { label: "total", valuePath: "summary.total", sub: "filtered root jobs" },
        { label: "queued", valuePath: "summary.queued", sub: "window" },
        { label: "running", valuePath: "summary.running", sub: "window" },
        { label: "succeeded", valuePath: "summary.succeeded", sub: "window" },
        { label: "failed", valuePath: "summary.failed", sub: "window" },
        { label: "terminal", valuePath: "summary.terminal", sub: "finished" },
      ],
    },
    "recent_jobs.table": {
      title: "Recent Jobs",
      question: "点击 job_id 查看追踪",
      rendererType: "html.table",
      dataSource: "recent_jobs",
      dataPath: "jobs",
      emptyText: "当前筛选没有 root Job",
      columns: [
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "status", label: "status", render: statusBadge },
        { key: "job_type", label: "job_type" },
        { key: "caller_id", label: "caller" },
        { key: "client_request_id", label: "client_request_id", wrap: true },
        { key: "progress_percent", label: "%" },
        { key: "progress_stage", label: "stage" },
        { key: "callback_status", label: "callback", render: statusBadge },
        { key: "updated_at", label: "updated", value: (row) => formatDate(row.updated_at) },
        {
          key: "duration_or_age_seconds",
          label: "age/duration s",
          value: (row) => Math.round(Number(row.duration_or_age_seconds || 0)),
        },
      ],
    },
    "flow_capacity.status": {
      rendererType: "status_line",
      dataSource: "flow_capacity",
      items: [
        { label: "section", badgeDefault: "neutral", badgePath: "health.status", value: "flow_capacity" },
        { label: "drain", badgeDefault: "neutral", badgePath: "drain.status", valuePath: "drain.status" },
        { label: "generated_at", valuePath: "generated_at", format: "date" },
      ],
    },
    "flow_capacity.next_checks": {
      title: "Flow & Capacity",
      question: "CLI handoff",
      rendererType: "html.signal_list",
      dataSource: "flow_capacity",
      dataPath: "health.next_checks",
      emptyText: "没有后续检查",
    },
    "flow_capacity.capacity_cards": {
      title: "Capacity Cards",
      question: "gate / headroom",
      rendererType: "metric_cards",
      dataSource: "flow_capacity",
      cards: [
        { label: "max_active_jobs", valuePath: "capacity.current.max_active_jobs", sub: "configured" },
        { label: "active_jobs", valuePath: "capacity.current.active_jobs", sub: "global gate" },
        { label: "headroom", valuePath: "capacity.current.headroom", sub: "remaining" },
        { label: "queued", valuePath: "capacity.current.queued", sub: "global" },
        { label: "running_active", valuePath: "capacity.current.running_active", sub: "global" },
        {
          label: "accepted_rps",
          value: (payload) => {
            const value = getPath(payload, "capacity.window.accepted_submit_rps");
            return value === null || value === undefined ? null : Math.round(Number(value) * 1000) / 1000;
          },
          sub: "window",
        },
      ],
    },
    "flow_capacity.ingress_drain": {
      title: "Ingress / Drain",
      question: "created / started / terminal / failed",
      rendererType: "echarts.line",
      dataSource: "flow_capacity",
      dataPath: "ingress",
      xField: "bucket_at",
      series: [
        { name: "created", field: "created" },
        { name: "started", field: "started" },
        { name: "terminal", field: "terminal" },
        { name: "failed", field: "failed" },
      ],
      colors: ["#1769aa", "#5f6b7a", "#12805c", "#c9342f"],
    },
    "flow_capacity.drain_cards": {
      title: "Drain",
      question: "current / window",
      rendererType: "metric_cards",
      dataSource: "flow_capacity",
      cards: [
        { label: "current_active", valuePath: "drain.current.active_jobs", sub: "family current" },
        { label: "running_inactive", valuePath: "drain.current.running_inactive", sub: "family current" },
        { label: "window_active", valuePath: "drain.window.active_jobs", sub: "family window" },
        { label: "window_failed", valuePath: "drain.window.failed", sub: "family window" },
        { label: "stuck", valuePath: "drain.stuck.total", sub: "older than 10m" },
      ],
    },
    "flow_capacity.status_composition": {
      title: "Status Composition",
      question: "queued / running / terminal",
      rendererType: "echarts.stacked_bar",
      dataSource: "flow_capacity",
      dataPath: "status_composition",
      xField: "bucket_at",
      series: [
        { name: "queued", field: "queued" },
        { name: "running", field: "running" },
        { name: "succeeded", field: "succeeded" },
        { name: "failed", field: "failed" },
      ],
      colors: ["#d68c1f", "#1769aa", "#12805c", "#c9342f"],
    },
    "flow_capacity.latency_p95": {
      title: "Latency p95",
      question: "queue / run / lifecycle",
      rendererType: "echarts.horizontal_bar",
      dataSource: "flow_capacity",
      adapter: "latency_p95_rows",
      labelField: "label",
      valueField: "value",
      valueSuffix: "s",
      color: "#087f8c",
      left: 84,
    },
    "flow_capacity.job_type_hotspots": {
      title: "Job Type Hotspots",
      question: "active / failed / p95",
      rendererType: "html.table",
      dataSource: "flow_capacity",
      dataPath: "job_type_hotspots",
      emptyText: "当前窗口没有 Job 类型热点",
      columns: [
        { key: "job_type", label: "job_type" },
        { key: "total", label: "total" },
        { key: "active_jobs", label: "active" },
        { key: "queued", label: "queued" },
        { key: "running", label: "running" },
        { key: "failed", label: "failed" },
        { key: "queue_wait_p95_seconds", label: "queue_p95_s", value: (row) => secondsCell(row.queue_wait_p95_seconds) },
        { key: "run_p95_seconds", label: "run_p95_s", value: (row) => secondsCell(row.run_p95_seconds) },
        { key: "lifecycle_p95_seconds", label: "lifecycle_p95_s", value: (row) => secondsCell(row.lifecycle_p95_seconds) },
      ],
    },
    "failures_callbacks.status": {
      rendererType: "status_line",
      dataSource: "failures_callbacks",
      items: [
        { label: "section", badgeDefault: "ok", badgePath: "health.status", value: "failures_callbacks" },
        { label: "generated_at", valuePath: "generated_at", format: "date" },
      ],
    },
    "failures_callbacks.failure_groups_rank": {
      title: "Failure Groups",
      question: "按 error_code 聚合",
      rendererType: "echarts.horizontal_bar",
      dataSource: "failures_callbacks",
      dataPath: "failure_groups",
      labelField: "error_code",
      valueField: "count",
      maxItems: 8,
      color: "#c9342f",
      left: 130,
    },
    "failures_callbacks.failure_groups_table": {
      title: "Failure Groups Table",
      question: "failure group details",
      rendererType: "html.table",
      dataSource: "failures_callbacks",
      dataPath: "failure_groups",
      emptyText: "当前窗口没有 failure groups",
      columns: [
        { key: "error_code", label: "error_code" },
        { key: "error_kind", label: "error_kind" },
        { key: "failure_phase", label: "phase" },
        { key: "count", label: "count" },
        { key: "detail_type", label: "detail_type" },
        { key: "newest_updated_at", label: "newest", value: (row) => formatDate(row.newest_updated_at) },
      ],
    },
    "failures_callbacks.failed_samples": {
      title: "Failed Samples",
      question: "点击 job_id 查看追踪",
      rendererType: "html.table",
      dataSource: "failures_callbacks",
      dataPath: "failed_samples",
      emptyText: "当前窗口没有 failed Job",
      columns: [
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "job_type", label: "job_type" },
        { key: "progress_percent", label: "%" },
        { key: "progress_stage", label: "stage" },
        { key: "updated_at", label: "updated", value: (row) => formatDate(row.updated_at) },
      ],
    },
    "failures_callbacks.callback_outbox": {
      title: "Callbacks",
      question: "outbox 状态",
      rendererType: "html.table",
      dataSource: "failures_callbacks",
      dataPath: "callbacks",
      emptyText: "当前窗口没有 callback outbox",
      columns: [
        { key: "status", label: "status", render: statusBadge },
        { key: "count", label: "count" },
        { key: "due", label: "due" },
        { key: "next_attempt_at", label: "next", value: (row) => formatDate(row.next_attempt_at) },
      ],
    },
    "job_trace.status": {
      rendererType: "status_line",
      dataSource: "job_trace",
      items: [
        { label: "job_id", badgePath: "job.status", valuePath: "job.job_id" },
        { label: "generated_at", valuePath: "generated_at", format: "date" },
      ],
    },
    "job_trace.summary": {
      title: "Job Summary",
      question: "root identity and lifecycle",
      rendererType: "html.summary_table",
      dataSource: "job_trace",
      rows: [
        { label: "job_id", valuePath: "job.job_id" },
        { label: "root_job_id", value: (payload) => getPath(payload, "job.root_job_id") || "-" },
        { label: "workflow_node_key", value: (payload) => getPath(payload, "job.workflow_node_key") || "-" },
        { label: "job_type", valuePath: "job.job_type" },
        { label: "caller_id", valuePath: "job.caller_id" },
        { label: "client_request_id", value: (payload) => getPath(payload, "job.client_request_id") || "-" },
        {
          label: "progress",
          value: (payload) => `${getPath(payload, "job.progress_percent") ?? 0}% / ${getPath(payload, "job.progress_stage") || "-"}`,
        },
        { label: "callback", value: (payload) => getPath(payload, "job.callback_status") || "-" },
        { label: "created_at", valuePath: "job.created_at", format: "date" },
        { label: "finished_at", valuePath: "job.finished_at", format: "date" },
      ],
    },
    "job_trace.payload_summary": {
      title: "Payload Summary",
      question: "full payload disabled",
      rendererType: "html.json_block",
      dataSource: "job_trace",
      value: (payload) => {
        const job = payload.job || {};
        return {
          metadata: job.metadata_summary,
          job_params: job.job_params_summary,
          runtime: job.runtime_summary,
          result: job.result_summary,
          canonical_result: job.canonical_result_summary,
          error: {
            code: job.error_code,
            message: job.error_message,
            summary: job.error_summary,
          },
        };
      },
    },
    "job_trace.attempts": {
      title: "Attempts",
      question: "retry decision",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "attempts",
      emptyText: "没有 attempts",
      columns: [
        { key: "purpose_attempt_no", label: "no" },
        { key: "purpose", label: "purpose" },
        { key: "status", label: "status", render: statusBadge },
        { key: "failure_phase", label: "phase" },
        { key: "retry_eligible", label: "eligible" },
        { key: "retry_decision", label: "decision" },
        { key: "retry_decision_reason", label: "reason", wrap: true },
        { key: "policy_max_attempts", label: "max" },
      ],
    },
    "job_trace.ai_calls": {
      title: "AI Calls",
      question: "provider evidence",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "ai_calls",
      emptyText: "没有 AI call ledger",
      columns: [
        { key: "status", label: "status", render: statusBadge },
        { key: "operation", label: "operation" },
        { key: "model_id", label: "model" },
        { key: "error_code", label: "error_code" },
        { key: "duration_ms", label: "ms" },
        { key: "billable_status", label: "billable" },
        { key: "error_message", label: "message", wrap: true },
      ],
    },
    "job_trace.children": {
      title: "Workflow Children",
      question: "family view",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "workflow_children",
      emptyText: "没有 workflow children",
      columns: [
        { key: "workflow_node_key", label: "node", wrap: true },
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "status", label: "status", render: statusBadge },
        { key: "job_type", label: "job_type" },
        { key: "progress_percent", label: "%" },
        { key: "updated_at", label: "updated", value: (row) => formatDate(row.updated_at) },
      ],
    },
    "job_trace.timeline": {
      title: "Timeline",
      question: "payload summary only",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "timeline",
      emptyText: "没有 timeline events",
      columns: [
        { key: "created_at", label: "created_at", value: (row) => formatDate(row.created_at) },
        { key: "event_type", label: "event" },
        { key: "from_status", label: "from" },
        { key: "to_status", label: "to" },
        { key: "reason", label: "reason" },
        { key: "payload_summary", label: "payload", value: (row) => JSON.stringify(row.payload_summary), wrap: true },
      ],
    },
    "job_trace.callbacks": {
      title: "Callbacks",
      question: "terminal delivery",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "callbacks",
      emptyText: "没有 callbacks",
      columns: [
        { key: "event_type", label: "event" },
        { key: "status", label: "status", render: statusBadge },
        { key: "delivery_attempts", label: "attempts" },
        { key: "last_http_status", label: "http" },
        { key: "last_error_message", label: "last_error", wrap: true },
      ],
    },
  });

  const LAYOUT_REGISTRY = Object.freeze({
    overview: {
      title: "总览",
      dataSource: "overview",
      target: "view-root",
      groups: [
        { key: "summary" },
        { key: "main", className: "panel-grid" },
      ],
      placements: [
        { widgetId: "overview.status", target: "status-line" },
        { widgetId: "overview.current_state", group: "summary", chrome: "bare", hostClass: "stat-grid" },
        { widgetId: "overview.ingress_trend", group: "main", hostClass: "chart" },
        { widgetId: "overview.latency_p95", group: "main", hostClass: "chart chart-compact" },
        { widgetId: "overview.health_signals", group: "main", hostClass: "signal-list" },
        { widgetId: "overview.stuck_samples", group: "main", hostClass: "table-wrap" },
      ],
    },
    recent_jobs: {
      title: "最近任务",
      dataSource: "recent_jobs",
      target: "recent-jobs-widgets",
      emptyText: "查询最近 root Job。",
      groups: [
        { key: "summary" },
        { key: "main", className: "panel-grid" },
      ],
      placements: [
        { widgetId: "recent_jobs.status", target: "status-line" },
        { widgetId: "recent_jobs.summary_cards", group: "summary", chrome: "bare", hostClass: "stat-grid" },
        {
          widgetId: "recent_jobs.table",
          group: "main",
          panelClass: "panel panel-wide",
          hostClass: "table-wrap",
        },
      ],
    },
    flow_capacity: {
      title: "吞吐与容量",
      dataSource: "flow_capacity",
      target: "view-root",
      groups: [
        { key: "summary" },
        { key: "main", className: "panel-grid" },
      ],
      placements: [
        { widgetId: "flow_capacity.status", target: "status-line" },
        { widgetId: "flow_capacity.capacity_cards", group: "summary", chrome: "bare", hostClass: "stat-grid" },
        { widgetId: "flow_capacity.drain_cards", group: "summary", chrome: "bare", hostClass: "stat-grid" },
        { widgetId: "flow_capacity.ingress_drain", group: "main", hostClass: "chart" },
        { widgetId: "flow_capacity.status_composition", group: "main", hostClass: "chart" },
        { widgetId: "flow_capacity.latency_p95", group: "main", hostClass: "chart chart-compact" },
        {
          widgetId: "flow_capacity.job_type_hotspots",
          group: "main",
          panelClass: "panel panel-wide",
          hostClass: "table-wrap",
        },
        { widgetId: "flow_capacity.next_checks", group: "main", hostClass: "signal-list" },
      ],
    },
    failures_callbacks: {
      title: "失败与回调",
      dataSource: "failures_callbacks",
      target: "view-root",
      groups: [{ key: "main", className: "panel-grid" }],
      placements: [
        { widgetId: "failures_callbacks.status", target: "status-line" },
        {
          widgetId: "failures_callbacks.failure_groups_rank",
          group: "main",
          panelClass: "panel panel-wide",
          hostClass: "chart chart-compact",
        },
        {
          widgetId: "failures_callbacks.failure_groups_table",
          group: "main",
          panelClass: "panel panel-wide",
          hostClass: "table-wrap",
        },
        { widgetId: "failures_callbacks.failed_samples", group: "main", hostClass: "table-wrap" },
        { widgetId: "failures_callbacks.callback_outbox", group: "main", hostClass: "table-wrap" },
      ],
    },
    job_trace: {
      title: "Job 追踪",
      dataSource: "job_trace",
      target: "job-trace-widgets",
      emptyText: "输入 job_id 后加载 Job 追踪。",
      groups: [
        { key: "summary", className: "trace-summary" },
        { key: "details", className: "trace-content" },
      ],
      placements: [
        { widgetId: "job_trace.status", target: "status-line" },
        { widgetId: "job_trace.summary", group: "summary", hostClass: "table-wrap" },
        { widgetId: "job_trace.payload_summary", group: "summary" },
        { widgetId: "job_trace.attempts", group: "details", hostClass: "table-wrap" },
        { widgetId: "job_trace.ai_calls", group: "details", hostClass: "table-wrap" },
        { widgetId: "job_trace.children", group: "details", hostClass: "table-wrap" },
        { widgetId: "job_trace.timeline", group: "details", hostClass: "table-wrap" },
        { widgetId: "job_trace.callbacks", group: "details", hostClass: "table-wrap" },
      ],
    },
  });

  const WIDGET_DATA_ADAPTERS = Object.freeze({
    latency_p95_rows: (payload) => {
      const row = (getPath(payload, "latency") || [])[0] || {};
      return [
        { label: "queue", value: row.queue_wait_p95_seconds },
        { label: "run", value: row.run_p95_seconds },
        { label: "lifecycle", value: row.lifecycle_p95_seconds },
      ];
    },
  });

  function $(selector) {
    return document.querySelector(selector);
  }

  function filterParams() {
    const form = new FormData($("#filters"));
    const params = new URLSearchParams();
    for (const [key, value] of form.entries()) {
      const normalized = String(value).trim();
      if (normalized) params.set(key, normalized);
    }
    return params;
  }

  async function fetchJson(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`${response.status} ${response.statusText}: ${text.slice(0, 200)}`);
    }
    return response.json();
  }

  function configuredDataSource(key) {
    const configured = state.config?.data_sources || state.config?.sections || [];
    return configured.find((source) => source.key === key);
  }

  function pageControls(section) {
    return PAGE_CONTROL_REGISTRY[section] || [];
  }

  function initializePageControls() {
    for (const [section, controls] of Object.entries(PAGE_CONTROL_REGISTRY)) {
      state.pageControls[section] ||= {};
      for (const control of controls) {
        if (control.default !== undefined && state.pageControls[section][control.key] === undefined) {
          state.pageControls[section][control.key] = control.default;
        }
      }
    }
  }

  function controlValue(section, control, params) {
    if (params && control.key in params) return params[control.key];
    if (control.key === "job_id" && params?.jobId) return params.jobId;
    const saved = state.pageControls[section]?.[control.key];
    if (saved !== undefined) return saved;
    return control.default;
  }

  function routeControlsReady(section, params) {
    return pageControls(section)
      .filter((control) => control.binding === "route")
      .every((control) => {
        const value = controlValue(section, control, params);
        return value !== undefined && value !== null && String(value).trim() !== "";
      });
  }

  function dataSourceUrl(key, params) {
    const source = DATA_SOURCE_REGISTRY[key];
    if (!source) throw new Error(`Unknown dataSource: ${key}`);
    const configured = configuredDataSource(source.sectionKey);
    let route = configured?.route || source.route;
    const queryParams = source.usesFilters ? filterParams() : new URLSearchParams();
    for (const control of pageControls(key)) {
      const value = controlValue(key, control, params);
      const normalized = value === undefined || value === null ? "" : String(value).trim();
      if (control.binding === "route") {
        if (!normalized) throw new Error(`${control.param} is required`);
        route = route.replace(`{${control.param}}`, encodeURIComponent(normalized));
      } else if (control.binding === "query" && normalized) {
        queryParams.set(control.param, normalized);
      }
    }
    if (route.includes("{")) throw new Error(`Unbound route param in dataSource: ${key}`);
    const query = queryParams.toString();
    return query ? `${route}?${query}` : route;
  }

  function setError(message) {
    $("#status-line").innerHTML = `<div class="error-state">查询失败：${escapeHtml(message)}</div>`;
  }

  function jobLink(value) {
    const id = compact(value);
    if (id === "-") return "-";
    return `<button class="link-button" data-job-id="${escapeHtml(id)}" type="button">${escapeHtml(id)}</button>`;
  }

  function secondsCell(value) {
    if (value === null || value === undefined) return "-";
    return Math.round(Number(value) * 100) / 100;
  }

  function renderNavigation() {
    const sections = state.config?.sections || Object.keys(LAYOUT_REGISTRY).map((key) => ({ key, title: LAYOUT_REGISTRY[key].title }));
    $("#section-nav").innerHTML = sections
      .filter((section) => LAYOUT_REGISTRY[section.key])
      .map((section) => `
        <button class="nav-item ${section.key === state.section ? "active" : ""}" data-section="${escapeHtml(section.key)}" type="button">
          ${escapeHtml(section.title || LAYOUT_REGISTRY[section.key].title || section.key)}
        </button>
      `)
      .join("");
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.addEventListener("click", () => switchSection(item.dataset.section));
    });
  }

  function setActiveSection(section) {
    state.section = section;
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.section === section);
    });
    $("#section-title").textContent = LAYOUT_REGISTRY[section]?.title || section;
  }

  function renderControlInput(section, control, context) {
    const value = controlValue(section, control, context);
    if (control.type === "select") {
      const options = (control.options || [])
        .map((option) => `
          <option value="${escapeHtml(option)}" ${String(value) === String(option) ? "selected" : ""}>${escapeHtml(option)}</option>
        `)
        .join("");
      return `
        <label>
          ${escapeHtml(control.label)}
          <select name="${escapeHtml(control.key)}">${options}</select>
        </label>
      `;
    }
    const attrs = [
      `name="${escapeHtml(control.key)}"`,
      `type="${control.type === "number" ? "number" : "text"}"`,
      `value="${escapeHtml(value ?? "")}"`,
      control.min !== undefined ? `min="${escapeHtml(control.min)}"` : "",
      control.max !== undefined ? `max="${escapeHtml(control.max)}"` : "",
      control.type === "text" ? "autocomplete=\"off\"" : "",
    ].filter(Boolean).join(" ");
    return `
      <label>
        ${escapeHtml(control.label)}
        <input ${attrs} />
      </label>
    `;
  }

  function renderPageControls(section, context) {
    const controls = pageControls(section);
    if (controls.length === 0) return "";
    return `
      <form id="page-controls" class="job-search">
        ${controls.map((control) => renderControlInput(section, control, context)).join("")}
        <button class="primary-button" type="submit">查询</button>
      </form>
    `;
  }

  function renderControlledShell(section, layout, context) {
    const message = context?.message || layout.emptyText || "";
    $("#view-root").innerHTML = `
      ${renderPageControls(section, context)}
      <div id="${escapeHtml(layout.target)}" class="trace-content empty-state">${escapeHtml(message)}</div>
    `;
    bindPageControls(section);
  }

  function renderPage(section, payload, context) {
    const layout = LAYOUT_REGISTRY[section];
    assertLayoutDataSources(section, layout);
    setActiveSection(section);
    if (pageControls(section).length > 0) {
      renderControlledShell(section, layout, context);
      if (!payload) return;
    }
    renderWidgetLayout(layout, WIDGET_REGISTRY, payload, WIDGET_DATA_ADAPTERS);
    if (pageControls(section).length > 0) bindPageControls(section);
  }

  function assertLayoutDataSources(section, layout) {
    const expected = layout.dataSource;
    if (!DATA_SOURCE_REGISTRY[expected]) {
      throw new Error(`Unknown layout dataSource: ${expected}`);
    }
    for (const placement of layout.placements || []) {
      const widget = WIDGET_REGISTRY[placement.widgetId];
      if (!widget) {
        throw new Error(`Unknown widgetId: ${placement.widgetId}`);
      }
      if (widget.dataSource && widget.dataSource !== expected) {
        throw new Error(`Widget ${placement.widgetId} belongs to ${widget.dataSource}, not ${section}`);
      }
    }
  }

  async function loadSection(section) {
    clearRefresh();
    try {
      if (!routeControlsReady(section)) {
        renderPage(section, null);
        return;
      }
      const payload = await fetchJson(dataSourceUrl(section));
      renderPage(section, payload);
      scheduleRefresh(section);
    } catch (error) {
      setError(error.message || String(error));
    }
  }

  async function loadJobTrace(jobId) {
    if (!jobId) return;
    state.pageControls.job_trace ||= {};
    state.pageControls.job_trace.job_id = jobId;
    await loadSection("job_trace");
  }

  function showJobTraceError(error) {
    const target = $("#job-trace-widgets");
    if (target) {
      target.innerHTML = `<div class="error-state">查询失败：${escapeHtml(error.message || String(error))}</div>`;
      return;
    }
    setError(error.message || String(error));
  }

  function bindPageControls(section) {
    const form = $("#page-controls");
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = {};
      for (const [key, value] of new FormData(event.currentTarget).entries()) {
        values[key] = String(value).trim();
      }
      state.pageControls[section] = { ...(state.pageControls[section] || {}), ...values };
      if (section === "job_trace" && values.job_id) {
        window.location.hash = `job=${encodeURIComponent(values.job_id)}`;
      }
      try {
        await loadSection(section);
      } catch (error) {
        showJobTraceError(error);
      }
    });
  }

  function switchSection(section) {
    if (!LAYOUT_REGISTRY[section]) return;
    clearRefresh();
    if (section === "job_trace") {
      $("#status-line").innerHTML = "";
      if (routeControlsReady("job_trace")) {
        loadSection("job_trace");
        return;
      }
      renderPage("job_trace", null);
      return;
    }
    loadSection(section);
  }

  function scheduleRefresh(section) {
    const configured = configuredDataSource(section);
    const seconds = configured?.refresh_seconds ?? state.config?.refresh_seconds ?? 15;
    if (!seconds) return;
    state.refreshTimer = window.setTimeout(() => loadSection(section), Math.max(seconds, 5) * 1000);
  }

  function clearRefresh() {
    if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
    state.refreshTimer = null;
  }

  async function init() {
    try {
      state.config = await fetchJson(`${BASE}/config`);
      initializePageControls();
    } catch (error) {
      setError(error.message || String(error));
    }
    renderNavigation();
    $("#filters").addEventListener("submit", (event) => {
      event.preventDefault();
      if (state.section !== "job_trace") loadSection(state.section);
    });
    document.body.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-job-id]");
      if (!button) return;
      const jobId = button.dataset.jobId;
      window.location.hash = `job=${encodeURIComponent(jobId)}`;
      try {
        await loadJobTrace(jobId);
      } catch (error) {
        showJobTraceError(error);
      }
    });
    const hashJob = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("job");
    if (hashJob) {
      try {
        await loadJobTrace(hashJob);
      } catch (error) {
        showJobTraceError(error);
      }
      return;
    }
    await loadSection("overview");
  }

  window.addEventListener("resize", resizeCharts);
  window.addEventListener("DOMContentLoaded", init);
})();
