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

  const charts = new Map();

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
    const source = payload.mock_data ? `<span class="badge warning">data_source: mock</span>` : `<span class="badge neutral">data_source: live</span>`;
    $("#status-line").innerHTML = `
      <div>${source} ${statusBadge(health.status || "ok")} <strong>reasons:</strong> ${escapeHtml(reasons)}</div>
      <div><strong>generated_at:</strong> ${escapeHtml(generated)}</div>
    `;
  }

  function setError(message) {
    $("#status-line").innerHTML = `<div class="error-state">查询失败：${escapeHtml(message)}</div>`;
  }

  function renderStats(payload) {
    const jobs = payload.summary?.jobs || {};
    const callbacks = payload.summary?.callbacks || {};
    const gate = payload.capacity?.current || {};
    const stuck = payload.stuck || {};
    const cards = [
      ["active_jobs", gate.active_jobs, `headroom ${gate.headroom ?? "-"}`],
      ["queued", jobs.queued, "root window"],
      ["running_active", jobs.running_active, "active attempts"],
      ["failed", jobs.failed, "window"],
      ["stuck", stuck.count, "older than 10m"],
      ["callback_due", callbacks.due, "due now"],
    ];
    $("#stat-grid").innerHTML = cards
      .map(([label, value, sub]) => `
        <article class="stat-card">
          <div class="label">${escapeHtml(label)}</div>
          <div class="value">${escapeHtml(number(value))}</div>
          <div class="sub">${escapeHtml(sub)}</div>
        </article>
      `)
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

  function ensureChart(id) {
    const el = document.getElementById(id);
    if (!el || !window.echarts) return null;
    const previous = charts.get(id);
    if (previous) previous.dispose();
    const chart = window.echarts.init(el, null, { renderer: "canvas" });
    charts.set(id, chart);
    return chart;
  }

  function renderFallbackChart(id, rows, labelKey, valueKeys) {
    const el = document.getElementById(id);
    if (!el) return;
    const max = Math.max(1, ...rows.flatMap((row) => valueKeys.map((key) => number(row[key]))));
    el.innerHTML = `
      <div class="fallback-chart">
        ${rows.map((row) => `
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

  function renderIngressChart(rows) {
    if (!window.echarts) {
      renderFallbackChart("ingress-chart", rows || [], "bucket_at", ["created", "terminal", "failed"]);
      return;
    }
    const chart = ensureChart("ingress-chart");
    const labels = (rows || []).map((row) => formatDate(row.bucket_at));
    chart.setOption({
      color: ["#1769aa", "#12805c", "#c9342f", "#6554c0"],
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      grid: { left: 36, right: 18, top: 42, bottom: 38 },
      xAxis: { type: "category", data: labels, boundaryGap: false },
      yAxis: { type: "value", minInterval: 1 },
      series: ["created", "terminal", "failed"].map((name) => ({
        name,
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 3 },
        areaStyle: { opacity: 0.08 },
        data: (rows || []).map((row) => number(row[name])),
      })),
    });
  }

  function renderLatencyChart(rows) {
    const row = (rows || [])[0] || {};
    const data = [
      ["queue", row.queue_wait_p95_seconds],
      ["run", row.run_p95_seconds],
      ["lifecycle", row.lifecycle_p95_seconds],
    ];
    if (!window.echarts) {
      renderFallbackChart(
        "latency-chart",
        data.map(([label, value]) => ({ label, value })),
        "label",
        ["value"],
      );
      return;
    }
    const chart = ensureChart("latency-chart");
    chart.setOption({
      color: ["#087f8c"],
      tooltip: { trigger: "axis", valueFormatter: (value) => `${Number(value || 0).toFixed(2)}s` },
      grid: { left: 58, right: 18, top: 20, bottom: 34 },
      xAxis: { type: "category", data: data.map(([label]) => label) },
      yAxis: { type: "value", axisLabel: { formatter: "{value}s" } },
      series: [{ type: "bar", barWidth: 34, data: data.map(([, value]) => Number(value || 0).toFixed(3)) }],
    });
  }

  function renderFailureChart(rows) {
    const top = (rows || []).slice(0, 8);
    if (!window.echarts) {
      renderFallbackChart("failure-chart", top, "error_code", ["count"]);
      return;
    }
    const chart = ensureChart("failure-chart");
    chart.setOption({
      color: ["#c9342f"],
      tooltip: { trigger: "axis" },
      grid: { left: 130, right: 18, top: 18, bottom: 24 },
      xAxis: { type: "value", minInterval: 1 },
      yAxis: { type: "category", data: top.map((row) => row.error_code || "-") },
      series: [{ type: "bar", data: top.map((row) => number(row.count)), barWidth: 18 }],
    });
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
    renderStats(payload);
    renderIngressChart(payload.ingress || []);
    renderLatencyChart(payload.latency || []);
    renderSignals(payload);
    renderTable("#stuck-table", payload.stuck?.sample || [], [
      { key: "issue", label: "issue" },
      { key: "job_id", label: "job_id", render: jobLink },
      { key: "job_status", label: "status", render: statusBadge },
      { key: "job_type", label: "job_type" },
      { key: "since_at", label: "since_at", value: (row) => formatDate(row.since_at) },
    ], "未发现 stuck 样本");
  }

  function jobLink(value) {
    const id = compact(value);
    if (id === "-") return "-";
    return `<button class="link-button" data-job-id="${escapeHtml(id)}" type="button">${escapeHtml(id)}</button>`;
  }

  async function loadFailures() {
    const payload = await fetchJson(`${BASE}/sections/failures/data?${filterQuery()}`);
    const source = payload.mock_data ? `<span class="badge warning">data_source: mock</span>` : `<span class="badge neutral">data_source: live</span>`;
    $("#status-line").innerHTML = `<div>${source}</div><div><strong>generated_at:</strong> ${escapeHtml(formatDate(payload.generated_at))}</div>`;
    renderFailureChart(payload.failure_groups || []);
    renderTable("#failure-groups", payload.failure_groups || [], [
      { key: "error_code", label: "error_code" },
      { key: "error_kind", label: "error_kind" },
      { key: "failure_phase", label: "phase" },
      { key: "count", label: "count" },
      { key: "detail_type", label: "detail_type" },
      { key: "newest_updated_at", label: "newest", value: (row) => formatDate(row.newest_updated_at) },
    ], "当前窗口没有 failure groups");
    renderTable("#failed-samples", payload.failed_samples || [], [
      { key: "job_id", label: "job_id", render: jobLink },
      { key: "job_type", label: "job_type" },
      { key: "progress_percent", label: "%" },
      { key: "progress_stage", label: "stage" },
      { key: "updated_at", label: "updated", value: (row) => formatDate(row.updated_at) },
    ], "当前窗口没有 failed Job");
    renderTable("#callbacks-table", payload.callbacks || [], [
      { key: "status", label: "status", render: statusBadge },
      { key: "count", label: "count" },
      { key: "due", label: "due" },
      { key: "next_attempt_at", label: "next", value: (row) => formatDate(row.next_attempt_at) },
    ], "当前窗口没有 callback outbox");
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
    const source = payload.mock_data ? `<span class="badge warning">data_source: mock</span>` : `<span class="badge neutral">data_source: live</span>`;
    $("#status-line").innerHTML = `<div>${source} ${statusBadge(job.status)} <strong>job_id:</strong> ${escapeHtml(job.job_id)}</div><div><strong>generated_at:</strong> ${escapeHtml(formatDate(payload.generated_at))}</div>`;
  }

  function summaryRow(label, value) {
    return `<tr><th>${escapeHtml(label)}</th><td class="wrap">${escapeHtml(compact(value))}</td></tr>`;
  }

  function renderTraceTables(payload) {
    renderTable("#trace-attempts", payload.attempts || [], [
      { key: "purpose_attempt_no", label: "no" },
      { key: "purpose", label: "purpose" },
      { key: "status", label: "status", render: statusBadge },
      { key: "failure_phase", label: "phase" },
      { key: "retry_eligible", label: "eligible" },
      { key: "retry_decision", label: "decision" },
      { key: "retry_decision_reason", label: "reason", wrap: true },
      { key: "policy_max_attempts", label: "max" },
    ], "没有 attempts");
    renderTable("#trace-ai-calls", payload.ai_calls || [], [
      { key: "status", label: "status", render: statusBadge },
      { key: "operation", label: "operation" },
      { key: "model_id", label: "model" },
      { key: "error_code", label: "error_code" },
      { key: "duration_ms", label: "ms" },
      { key: "billable_status", label: "billable" },
      { key: "error_message", label: "message", wrap: true },
    ], "没有 AI call ledger");
    renderTable("#trace-children", payload.workflow_children || [], [
      { key: "workflow_node_key", label: "node", wrap: true },
      { key: "job_id", label: "job_id", render: jobLink },
      { key: "status", label: "status", render: statusBadge },
      { key: "job_type", label: "job_type" },
      { key: "progress_percent", label: "%" },
      { key: "updated_at", label: "updated", value: (row) => formatDate(row.updated_at) },
    ], "没有 workflow children");
    renderTable("#trace-timeline", payload.timeline || [], [
      { key: "created_at", label: "created_at", value: (row) => formatDate(row.created_at) },
      { key: "event_type", label: "event" },
      { key: "from_status", label: "from" },
      { key: "to_status", label: "to" },
      { key: "reason", label: "reason" },
      { key: "payload_summary", label: "payload", value: (row) => JSON.stringify(row.payload_summary), wrap: true },
    ], "没有 timeline events");
    renderTable("#trace-callbacks", payload.callbacks || [], [
      { key: "event_type", label: "event" },
      { key: "status", label: "status", render: statusBadge },
      { key: "delivery_attempts", label: "attempts" },
      { key: "last_http_status", label: "http" },
      { key: "last_error_message", label: "last_error", wrap: true },
    ], "没有 callbacks");
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
    for (const chart of charts.values()) chart.resize();
  });
  window.addEventListener("DOMContentLoaded", init);
})();
