(function () {
  const BASE = "/internal/jobs-dashboard";
  const state = {
    section: "overview",
    config: null,
    refreshTimer: null,
  };

  const titles = {
    overview: "总览",
    failures: "失败",
    job_trace: "Job 追踪",
  };

  const Charts = window.OpsDashboardCharts;
  const { compact, escapeHtml, formatDate, getPath, renderPanels, statusBadge } = Charts;

  const PANEL_REGISTRY = Object.freeze({
    overview: [
      {
        key: "current_state",
        question: "现在怎么样",
        chartType: "stat_card",
        target: "stat-grid",
        cards: [
          {
            label: "active_jobs",
            valuePath: "capacity.current.active_jobs",
            subPrefix: "headroom",
            subPath: "capacity.current.headroom",
          },
          { label: "queued", valuePath: "summary.jobs.queued", sub: "root window" },
          {
            label: "running_active",
            valuePath: "summary.jobs.running_active",
            sub: "active attempts",
          },
          { label: "failed", valuePath: "summary.jobs.failed", sub: "window" },
          { label: "stuck", valuePath: "stuck.count", sub: "older than 10m" },
          { label: "callback_due", valuePath: "summary.callbacks.due", sub: "due now" },
        ],
      },
      {
        key: "ingress_trend",
        question: "趋势如何",
        chartType: "line",
        target: "ingress-chart",
        dataPath: "ingress",
        xField: "bucket_at",
        series: [
          { name: "created", field: "created" },
          { name: "terminal", field: "terminal" },
          { name: "failed", field: "failed" },
        ],
        colors: ["#1769aa", "#12805c", "#c9342f"],
      },
      {
        key: "latency_p95",
        question: "哪段最重",
        chartType: "horizontal_bar",
        target: "latency-chart",
        adapter: "latency_p95_rows",
        labelField: "label",
        valueField: "value",
        valueSuffix: "s",
        color: "#087f8c",
        left: 84,
      },
      {
        key: "stuck_samples",
        question: "具体是哪几个",
        chartType: "table",
        target: "stuck-table",
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
    ],
    failures: [
      {
        key: "failure_groups_rank",
        question: "谁最多",
        chartType: "horizontal_bar",
        target: "failure-chart",
        dataPath: "failure_groups",
        labelField: "error_code",
        valueField: "count",
        maxItems: 8,
        color: "#c9342f",
        left: 130,
      },
      {
        key: "failure_groups_table",
        question: "谁最多",
        chartType: "table",
        target: "failure-groups",
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
      {
        key: "failed_samples",
        question: "具体是哪几个",
        chartType: "table",
        target: "failed-samples",
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
      {
        key: "callback_outbox",
        question: "具体是哪几个",
        chartType: "table",
        target: "callbacks-table",
        dataPath: "callbacks",
        emptyText: "当前窗口没有 callback outbox",
        columns: [
          { key: "status", label: "status", render: statusBadge },
          { key: "count", label: "count" },
          { key: "due", label: "due" },
          { key: "next_attempt_at", label: "next", value: (row) => formatDate(row.next_attempt_at) },
        ],
      },
    ],
    job_trace: [
      {
        key: "trace_attempts",
        question: "这个 Job 是否触发重试机制",
        chartType: "table",
        target: "trace-attempts",
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
      {
        key: "trace_ai_calls",
        question: "模型调用证据是什么",
        chartType: "table",
        target: "trace-ai-calls",
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
      {
        key: "trace_children",
        question: "子任务具体是哪几个",
        chartType: "table",
        target: "trace-children",
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
      {
        key: "trace_timeline",
        question: "这个 Job 卡在哪一步",
        chartType: "table",
        target: "trace-timeline",
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
      {
        key: "trace_callbacks",
        question: "终态是否已经通知调用方",
        chartType: "table",
        target: "trace-callbacks",
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
    ],
  });

  const PANEL_DATA_ADAPTERS = Object.freeze({
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

  function setStatus(payload) {
    const health = payload.health || {};
    const generated = formatDate(payload.generated_at);
    const reasons = (health.reasons || []).length ? health.reasons.join(", ") : "no active warning";
    $("#status-line").innerHTML = `
      <div>${statusBadge(health.status || "ok")} <strong>reasons:</strong> ${escapeHtml(reasons)}</div>
      <div><strong>generated_at:</strong> ${escapeHtml(generated)}</div>
    `;
  }

  function setError(message) {
    $("#status-line").innerHTML = `<div class="error-state">查询失败：${escapeHtml(message)}</div>`;
  }

  function renderSignals(payload) {
    const health = payload.health || {};
    const checks = health.next_checks || [];
    $("#health-signals").innerHTML = checks
      .map((check) => `<div class="signal-item"><code>${escapeHtml(check)}</code></div>`)
      .join("");
  }

  async function loadOverview() {
    const payload = await fetchJson(`${BASE}/sections/overview/data?${filterQuery()}`);
    setStatus(payload);
    renderPanels(PANEL_REGISTRY, "overview", payload, PANEL_DATA_ADAPTERS);
    renderSignals(payload);
  }

  function jobLink(value) {
    const id = compact(value);
    if (id === "-") return "-";
    return `<button class="link-button" data-job-id="${escapeHtml(id)}" type="button">${escapeHtml(id)}</button>`;
  }

  async function loadFailures() {
    const payload = await fetchJson(`${BASE}/sections/failures/data?${filterQuery()}`);
    $("#status-line").innerHTML = `<div>${statusBadge("ok")} <strong>section:</strong> failures</div><div><strong>generated_at:</strong> ${escapeHtml(formatDate(payload.generated_at))}</div>`;
    renderPanels(PANEL_REGISTRY, "failures", payload, PANEL_DATA_ADAPTERS);
  }

  async function loadJobTrace(jobId) {
    if (!jobId) return;
    $("#job-trace-content").innerHTML = `<div class="empty-state">正在加载 ${escapeHtml(jobId)}...</div>`;
    const payload = await fetchJson(`${BASE}/jobs/${encodeURIComponent(jobId)}/data`);
    const job = payload.job || {};
    $("#job-trace-content").innerHTML = `
      <div class="trace-summary">
        <section class="panel">
          <div class="panel-head"><h3>Job Summary</h3>${statusBadge(job.status)}</div>
          <table>
            <tbody>
              ${summaryRow("job_id", job.job_id)}
              ${summaryRow("root_job_id", job.root_job_id || "-")}
              ${summaryRow("workflow_node_key", job.workflow_node_key || "-")}
              ${summaryRow("job_type", job.job_type)}
              ${summaryRow("caller_id", job.caller_id)}
              ${summaryRow("client_request_id", job.client_request_id || "-")}
              ${summaryRow("progress", `${job.progress_percent ?? 0}% / ${job.progress_stage || "-"}`)}
              ${summaryRow("callback", job.callback_status || "-")}
              ${summaryRow("created_at", formatDate(job.created_at))}
              ${summaryRow("finished_at", formatDate(job.finished_at))}
            </tbody>
          </table>
        </section>
        <section class="panel">
          <div class="panel-head"><h3>Payload Summary</h3><span>full payload disabled</span></div>
          <pre class="json-block">${escapeHtml(JSON.stringify({
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
          }, null, 2))}</pre>
        </section>
      </div>
      <section class="panel"><div class="panel-head"><h3>Attempts</h3><span>retry decision</span></div><div id="trace-attempts"></div></section>
      <section class="panel"><div class="panel-head"><h3>AI Calls</h3><span>provider evidence</span></div><div id="trace-ai-calls"></div></section>
      <section class="panel"><div class="panel-head"><h3>Workflow Children</h3><span>family view</span></div><div id="trace-children"></div></section>
      <section class="panel"><div class="panel-head"><h3>Timeline</h3><span>payload summary only</span></div><div id="trace-timeline"></div></section>
      <section class="panel"><div class="panel-head"><h3>Callbacks</h3><span>terminal delivery</span></div><div id="trace-callbacks"></div></section>
    `;
    renderTraceTables(payload);
    $("#status-line").innerHTML = `<div>${statusBadge(job.status)} <strong>job_id:</strong> ${escapeHtml(job.job_id)}</div><div><strong>generated_at:</strong> ${escapeHtml(formatDate(payload.generated_at))}</div>`;
  }

  function summaryRow(label, value) {
    return `<tr><th>${escapeHtml(label)}</th><td class="wrap">${escapeHtml(compact(value))}</td></tr>`;
  }

  function renderTraceTables(payload) {
    renderPanels(PANEL_REGISTRY, "job_trace", payload, PANEL_DATA_ADAPTERS);
  }

  function switchSection(section) {
    state.section = section;
    $(".section.active")?.classList.remove("active");
    $(`#${section}-section`)?.classList.add("active");
    $(".nav-item.active")?.classList.remove("active");
    document.querySelector(`[data-section="${section}"]`)?.classList.add("active");
    $("#section-title").textContent = titles[section] || section;
    loadCurrentSection();
  }

  async function loadCurrentSection() {
    clearRefresh();
    try {
      if (state.section === "overview") {
        await loadOverview();
        scheduleRefresh();
      } else if (state.section === "failures") {
        await loadFailures();
        scheduleRefresh();
      }
    } catch (error) {
      setError(error.message || String(error));
    }
  }

  function scheduleRefresh() {
    const seconds = state.config?.refresh_seconds || 15;
    state.refreshTimer = window.setTimeout(loadCurrentSection, Math.max(seconds, 5) * 1000);
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
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.addEventListener("click", () => switchSection(item.dataset.section));
    });
    $("#filters").addEventListener("submit", (event) => {
      event.preventDefault();
      loadCurrentSection();
    });
    $("#job-search").addEventListener("submit", async (event) => {
      event.preventDefault();
      const jobId = new FormData(event.currentTarget).get("job_id")?.toString().trim();
      if (!jobId) return;
      window.location.hash = `job=${encodeURIComponent(jobId)}`;
      switchSection("job_trace");
      try {
        await loadJobTrace(jobId);
      } catch (error) {
        $("#job-trace-content").innerHTML = `<div class="error-state">查询失败：${escapeHtml(error.message || String(error))}</div>`;
      }
    });
    document.body.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-job-id]");
      if (!button) return;
      const jobId = button.dataset.jobId;
      $("#job-search input[name='job_id']").value = jobId;
      window.location.hash = `job=${encodeURIComponent(jobId)}`;
      switchSection("job_trace");
      try {
        await loadJobTrace(jobId);
      } catch (error) {
        $("#job-trace-content").innerHTML = `<div class="error-state">查询失败：${escapeHtml(error.message || String(error))}</div>`;
      }
    });
    const hashJob = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("job");
    if (hashJob) {
      $("#job-search input[name='job_id']").value = hashJob;
      switchSection("job_trace");
      await loadJobTrace(hashJob);
      return;
    }
    await loadOverview();
    scheduleRefresh();
  }

  window.addEventListener("resize", () => {
    Charts.resizeCharts();
  });
  window.addEventListener("DOMContentLoaded", init);
})();
