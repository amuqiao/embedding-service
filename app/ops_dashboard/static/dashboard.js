(function () {
  const BASE = "/internal/jobs-dashboard";
  const state = {
    section: "overview",
    config: null,
    refreshTimer: null,
    currentJobId: null,
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
    failures: {
      sectionKey: "failures",
      route: `${BASE}/sections/failures/data`,
      usesFilters: true,
    },
    job_trace: {
      sectionKey: "job_trace",
      route: `${BASE}/jobs/{job_id}/data`,
      usesJobId: true,
    },
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
        { label: "failed", valuePath: "summary.jobs.failed", sub: "window" },
        { label: "stuck", valuePath: "stuck.count", sub: "older than 10m" },
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
    "failures.status": {
      rendererType: "status_line",
      dataSource: "failures",
      items: [
        { label: "section", badgeDefault: "ok", badgePath: "health.status", value: "failures" },
        { label: "generated_at", valuePath: "generated_at", format: "date" },
      ],
    },
    "failures.failure_groups_rank": {
      title: "Failure Groups",
      question: "按 error_code 聚合",
      rendererType: "echarts.horizontal_bar",
      dataSource: "failures",
      dataPath: "failure_groups",
      labelField: "error_code",
      valueField: "count",
      maxItems: 8,
      color: "#c9342f",
      left: 130,
    },
    "failures.failure_groups_table": {
      title: "Failure Groups Table",
      question: "failure group details",
      rendererType: "html.table",
      dataSource: "failures",
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
    "failures.failed_samples": {
      title: "Failed Samples",
      question: "点击 job_id 查看追踪",
      rendererType: "html.table",
      dataSource: "failures",
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
    "failures.callback_outbox": {
      title: "Callbacks",
      question: "outbox 状态",
      rendererType: "html.table",
      dataSource: "failures",
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
    failures: {
      title: "失败",
      dataSource: "failures",
      target: "view-root",
      groups: [{ key: "main", className: "panel-grid" }],
      placements: [
        { widgetId: "failures.status", target: "status-line" },
        {
          widgetId: "failures.failure_groups_rank",
          group: "main",
          panelClass: "panel panel-wide",
          hostClass: "chart chart-compact",
        },
        {
          widgetId: "failures.failure_groups_table",
          group: "main",
          panelClass: "panel panel-wide",
          hostClass: "table-wrap",
        },
        { widgetId: "failures.failed_samples", group: "main", hostClass: "table-wrap" },
        { widgetId: "failures.callback_outbox", group: "main", hostClass: "table-wrap" },
      ],
    },
    job_trace: {
      title: "Job 追踪",
      dataSource: "job_trace",
      target: "job-trace-widgets",
      control: "job_search",
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

  function filterQuery() {
    const form = new FormData($("#filters"));
    const params = new URLSearchParams();
    for (const [key, value] of form.entries()) {
      const normalized = String(value).trim();
      if (normalized) params.set(key, normalized);
    }
    return params.toString();
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

  function dataSourceUrl(key, params) {
    const source = DATA_SOURCE_REGISTRY[key];
    if (!source) throw new Error(`Unknown dataSource: ${key}`);
    const configured = configuredDataSource(source.sectionKey);
    let route = configured?.route || source.route;
    if (source.usesJobId) {
      if (!params?.jobId) throw new Error("job_id is required");
      route = route.replace("{job_id}", encodeURIComponent(params.jobId));
    }
    const query = source.usesFilters ? filterQuery() : "";
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

  function renderJobSearchShell(context) {
    $("#view-root").innerHTML = `
      <form id="job-search" class="job-search">
        <label>
          job_id
          <input name="job_id" autocomplete="off" placeholder="UUID" value="${escapeHtml(context?.jobId || "")}" />
        </label>
        <button class="primary-button" type="submit">查询 Job</button>
      </form>
      <div id="job-trace-widgets" class="trace-content empty-state">${escapeHtml(context?.message || LAYOUT_REGISTRY.job_trace.emptyText)}</div>
    `;
    bindJobSearch();
  }

  function renderPage(section, payload, context) {
    const layout = LAYOUT_REGISTRY[section];
    assertLayoutDataSources(section, layout);
    setActiveSection(section);
    if (layout.control === "job_search") {
      renderJobSearchShell(context);
      if (!payload) return;
    }
    renderWidgetLayout(layout, WIDGET_REGISTRY, payload, WIDGET_DATA_ADAPTERS);
    if (layout.control === "job_search") bindJobSearch();
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
      const payload = await fetchJson(dataSourceUrl(section));
      renderPage(section, payload);
      scheduleRefresh(section);
    } catch (error) {
      setError(error.message || String(error));
    }
  }

  async function loadJobTrace(jobId) {
    if (!jobId) return;
    state.currentJobId = jobId;
    clearRefresh();
    setActiveSection("job_trace");
    renderPage("job_trace", null, { jobId, message: `正在加载 ${jobId}...` });
    const payload = await fetchJson(dataSourceUrl("job_trace", { jobId }));
    renderPage("job_trace", payload, { jobId });
  }

  function showJobTraceError(error) {
    const target = $("#job-trace-widgets");
    if (target) {
      target.innerHTML = `<div class="error-state">查询失败：${escapeHtml(error.message || String(error))}</div>`;
      return;
    }
    setError(error.message || String(error));
  }

  function bindJobSearch() {
    const form = $("#job-search");
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const jobId = new FormData(event.currentTarget).get("job_id")?.toString().trim();
      if (!jobId) return;
      window.location.hash = `job=${encodeURIComponent(jobId)}`;
      try {
        await loadJobTrace(jobId);
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
      renderPage("job_trace", null, { jobId: state.currentJobId });
      return;
    }
    loadSection(section);
  }

  function scheduleRefresh(section) {
    const configured = configuredDataSource(section);
    const seconds = configured?.refresh_seconds || state.config?.refresh_seconds || 15;
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
