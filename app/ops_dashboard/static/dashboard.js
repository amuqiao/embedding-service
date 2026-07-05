(function () {
  const BASE = "/internal/jobs-dashboard";
  const state = {
    section: "overview",
    config: null,
    refreshTimer: null,
    appliedFilters: {},
    pageControls: {},
    pageControlDrafts: {},
    pageControlsDirty: {},
    pageJsonBySection: {},
    actionNoticeTimer: null,
  };

  const Renderers = window.OpsDashboardRenderers;
  const JOB_ID_UUID_PATTERN = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
  const {
    compact,
    escapeHtml,
    formatDate,
    formatJson,
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
        label: "状态",
        default: "all",
        options: ["all", "queued", "running", "succeeded", "failed"],
      },
      {
        key: "job_id",
        type: "uuid",
        binding: "query",
        param: "job_id",
        label: "job_id",
        placeholder: "UUID job_id",
        pattern: JOB_ID_UUID_PATTERN,
      },
      {
        key: "limit",
        type: "number",
        binding: "query",
        param: "limit",
        label: "数量",
        default: 20,
        min: 1,
        max: 100,
      },
    ],
    job_trace: [
      {
        key: "job_id",
        type: "uuid",
        binding: "route",
        param: "job_id",
        label: "job_id",
        placeholder: "UUID job_id",
        pattern: JOB_ID_UUID_PATTERN,
      },
      {
        key: "limit",
        type: "number",
        binding: "query",
        param: "limit",
        label: "数量",
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
          label: "原因",
          badgePath: "health.status",
          badgeDefault: "ok",
          valuePath: "health.reasons",
          format: "join",
          empty: "无活跃告警",
        },
        { label: "生成时间", valuePath: "generated_at", format: "date" },
      ],
    },
    "overview.current_state": {
      title: "当前状态",
      question: "现在怎么样",
      rendererType: "metric_cards",
      dataSource: "overview",
      cards: [
        {
          label: "活跃 Job",
          valuePath: "capacity.current.active_jobs",
          subPrefix: "剩余额度",
          subPath: "capacity.current.headroom",
        },
        { label: "排队", valuePath: "summary.jobs.queued", sub: "root 时间窗口" },
        { label: "运行中", valuePath: "summary.jobs.running_active", sub: "活跃执行" },
        { label: "成功", valuePath: "summary.jobs.succeeded", sub: "时间窗口" },
        {
          label: "成功率 %",
          value: (payload) => {
            const rate = getPath(payload, "summary.jobs.success_rate");
            return rate === null || rate === undefined ? null : Math.round(Number(rate) * 1000) / 10;
          },
          sub: "终态窗口",
        },
        { label: "失败", valuePath: "summary.jobs.failed", sub: "时间窗口" },
        { label: "Callback 已送达", valuePath: "summary.callbacks.delivered", sub: "时间窗口" },
        { label: "卡住", valuePath: "stuck.total", sub: "超过 10m" },
        { label: "Callback 到期", valuePath: "summary.callbacks.due", sub: "当前到期" },
      ],
    },
    "overview.ingress_trend": {
      title: "入口趋势",
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
      title: "延迟 p95",
      question: "排队 / 执行 / 生命周期",
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
      title: "健康信号",
      question: "下一步检查",
      rendererType: "html.signal_list",
      dataSource: "overview",
      dataPath: "health.next_checks",
      emptyText: "没有后续检查",
    },
    "overview.stuck_samples": {
      title: "Stuck 样本",
      question: "超过 10m",
      rendererType: "html.table",
      dataSource: "overview",
      dataPath: "stuck.sample",
      emptyText: "未发现 stuck 样本",
      columns: [
        { key: "issue", label: "问题" },
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "job_status", label: "状态", render: statusBadge },
        { key: "job_type", label: "job_type" },
        { key: "since_at", label: "开始时间", value: (row) => formatDate(row.since_at) },
      ],
    },
    "recent_jobs.status": {
      rendererType: "status_line",
      dataSource: "recent_jobs",
      items: [
        { label: "分区", badgeDefault: "neutral", badgePath: "health.status", value: "recent_jobs" },
        { label: "生成时间", valuePath: "generated_at", format: "date" },
      ],
    },
    "recent_jobs.summary_cards": {
      title: "结果概览",
      question: "当前筛选",
      rendererType: "metric_cards",
      dataSource: "recent_jobs",
      cards: [
        { label: "总数", valuePath: "summary.total", sub: "筛选后 root Job" },
        { label: "排队", valuePath: "summary.queued", sub: "时间窗口" },
        { label: "运行中", valuePath: "summary.running", sub: "时间窗口" },
        { label: "成功", valuePath: "summary.succeeded", sub: "时间窗口" },
        { label: "失败", valuePath: "summary.failed", sub: "时间窗口" },
        { label: "终态", valuePath: "summary.terminal", sub: "已结束" },
      ],
    },
    "recent_jobs.table": {
      title: "最近任务",
      question: "点击 job_id 查看追踪",
      rendererType: "html.table",
      dataSource: "recent_jobs",
      dataPath: "jobs",
      emptyText: "当前筛选没有 root Job",
      columns: [
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "status", label: "状态", render: statusBadge },
        { key: "job_type", label: "job_type" },
        { key: "caller_id", label: "caller_id" },
        { key: "client_request_id", label: "client_request_id", wrap: true },
        { key: "progress_percent", label: "%" },
        { key: "progress_stage", label: "阶段" },
        { key: "callback_status", label: "Callback 状态", render: statusBadge },
        { key: "updated_at", label: "更新时间", value: (row) => formatDate(row.updated_at) },
        {
          key: "duration_or_age_seconds",
          label: "年龄/耗时秒",
          value: (row) => Math.round(Number(row.duration_or_age_seconds || 0)),
        },
      ],
    },
    "flow_capacity.status": {
      rendererType: "status_line",
      dataSource: "flow_capacity",
      items: [
        { label: "分区", badgeDefault: "neutral", badgePath: "health.status", value: "flow_capacity" },
        { label: "drain", badgeDefault: "neutral", badgePath: "drain.status", valuePath: "drain.status" },
        { label: "生成时间", valuePath: "generated_at", format: "date" },
      ],
    },
    "flow_capacity.next_checks": {
      title: "吞吐与容量",
      question: "CLI 排障入口",
      rendererType: "html.signal_list",
      dataSource: "flow_capacity",
      dataPath: "health.next_checks",
      emptyText: "没有后续检查",
    },
    "flow_capacity.capacity_cards": {
      title: "容量概览",
      question: "容量闸门与 headroom",
      rendererType: "metric_cards",
      dataSource: "flow_capacity",
      cards: [
        { label: "最大活跃 Job", valuePath: "capacity.current.max_active_jobs", sub: "配置值" },
        { label: "活跃 Job", valuePath: "capacity.current.active_jobs", sub: "全局闸门" },
        { label: "剩余额度", valuePath: "capacity.current.headroom", sub: "headroom" },
        { label: "排队", valuePath: "capacity.current.queued", sub: "全局" },
        { label: "运行中", valuePath: "capacity.current.running_active", sub: "全局" },
        {
          label: "接单 RPS",
          value: (payload) => {
            const value = getPath(payload, "capacity.window.accepted_submit_rps");
            return value === null || value === undefined ? null : Math.round(Number(value) * 1000) / 1000;
          },
          sub: "时间窗口",
        },
      ],
    },
    "flow_capacity.ingress_drain": {
      title: "入口与排空",
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
      title: "排空状态",
      question: "当前与时间窗口",
      rendererType: "metric_cards",
      dataSource: "flow_capacity",
      cards: [
        { label: "当前活跃", valuePath: "drain.current.active_jobs", sub: "当前 family" },
        { label: "非活跃运行", valuePath: "drain.current.running_inactive", sub: "当前 family" },
        { label: "窗口活跃", valuePath: "drain.window.active_jobs", sub: "窗口 family" },
        { label: "窗口失败", valuePath: "drain.window.failed", sub: "窗口 family" },
        { label: "卡住", valuePath: "drain.stuck.total", sub: "超过 10m" },
      ],
    },
    "flow_capacity.status_composition": {
      title: "状态构成",
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
      title: "延迟 p95",
      question: "排队 / 执行 / 生命周期",
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
      title: "job_type 热点",
      question: "活跃 / 失败 / p95",
      rendererType: "html.table",
      dataSource: "flow_capacity",
      dataPath: "job_type_hotspots",
      emptyText: "当前窗口没有 Job 类型热点",
      columns: [
        { key: "job_type", label: "job_type" },
        { key: "total", label: "总数" },
        { key: "active_jobs", label: "活跃" },
        { key: "queued", label: "排队" },
        { key: "running", label: "运行中" },
        { key: "failed", label: "失败" },
        { key: "queue_wait_p95_seconds", label: "排队 p95", value: (row) => secondsCell(row.queue_wait_p95_seconds) },
        { key: "run_p95_seconds", label: "执行 p95", value: (row) => secondsCell(row.run_p95_seconds) },
        { key: "lifecycle_p95_seconds", label: "生命周期 p95", value: (row) => secondsCell(row.lifecycle_p95_seconds) },
      ],
    },
    "failures_callbacks.status": {
      rendererType: "status_line",
      dataSource: "failures_callbacks",
      items: [
        { label: "分区", badgeDefault: "ok", badgePath: "health.status", value: "failures_callbacks" },
        { label: "生成时间", valuePath: "generated_at", format: "date" },
      ],
    },
    "failures_callbacks.summary_cards": {
      title: "失败与 Callback 概览",
      question: "失败与 Callback 闭环",
      rendererType: "metric_cards",
      dataSource: "failures_callbacks",
      cards: [
        { label: "失败记录", valuePath: "failure_summary.failed_records", sub: "family 窗口" },
        { label: "失败 root", valuePath: "failure_summary.failed_roots", sub: "root family" },
        { label: "Callback 到期", valuePath: "callback_summary.due", sub: "当前到期" },
        { label: "已送达", valuePath: "callback_summary.delivered", sub: "Callback" },
        { label: "死信", valuePath: "callback_summary.dead_letter", sub: "Callback" },
        { label: "卡住", valuePath: "stuck.total", sub: "超过 10m" },
      ],
    },
    "failures_callbacks.next_checks": {
      title: "失败与 Callback",
      question: "CLI 排障入口",
      rendererType: "html.signal_list",
      dataSource: "failures_callbacks",
      dataPath: "health.next_checks",
      emptyText: "没有后续检查",
    },
    "failures_callbacks.failure_groups_rank": {
      title: "失败分组",
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
      title: "失败分组明细",
      question: "失败分组明细",
      rendererType: "html.table",
      dataSource: "failures_callbacks",
      dataPath: "failure_groups",
      emptyText: "当前窗口没有 failure groups",
      columns: [
        { key: "error_code", label: "error_code" },
        { key: "error_kind", label: "error_kind" },
        { key: "failure_phase", label: "阶段" },
        { key: "count", label: "数量" },
        { key: "detail_type", label: "detail_type" },
        { key: "newest_updated_at", label: "最新时间", value: (row) => formatDate(row.newest_updated_at) },
      ],
    },
    "failures_callbacks.failed_samples": {
      title: "失败样本",
      question: "点击 job_id 查看追踪",
      rendererType: "html.table",
      dataSource: "failures_callbacks",
      dataPath: "failed_samples",
      emptyText: "当前窗口没有 failed Job",
      columns: [
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "record_scope", label: "范围" },
        { key: "workflow_node_key", label: "节点" },
        { key: "job_type", label: "job_type" },
        { key: "error_code", label: "error_code" },
        { key: "progress_percent", label: "%" },
        { key: "progress_stage", label: "阶段" },
        { key: "callback_status", label: "Callback 状态", render: statusBadge },
        { key: "attempt_status", label: "尝试状态", render: statusBadge },
        { key: "dispatch_status", label: "分发状态", render: statusBadge },
        {
          key: "duration_or_age_seconds",
          label: "年龄/耗时秒",
          value: (row) => Math.round(Number(row.duration_or_age_seconds || 0)),
        },
        { key: "updated_at", label: "更新时间", value: (row) => formatDate(row.updated_at) },
      ],
    },
    "failures_callbacks.callback_outbox": {
      title: "Callback outbox",
      question: "outbox 状态",
      rendererType: "html.table",
      dataSource: "failures_callbacks",
      dataPath: "callbacks",
      emptyText: "当前窗口没有 callback outbox",
      columns: [
        { key: "status", label: "状态", render: statusBadge },
        { key: "count", label: "数量" },
        { key: "due", label: "due" },
        { key: "oldest_age_seconds", label: "最旧秒", value: (row) => secondsCell(row.oldest_age_seconds) },
        { key: "last_http_status_seen", label: "http_seen" },
        { key: "sample_last_error_code", label: "error_code" },
        { key: "next_attempt_at", label: "下次尝试", value: (row) => formatDate(row.next_attempt_at) },
      ],
    },
    "failures_callbacks.callback_composition": {
      title: "Callback 状态构成",
      question: "窗口内 Callback 状态",
      rendererType: "echarts.stacked_bar",
      dataSource: "failures_callbacks",
      adapter: "callback_composition_rows",
      xField: "bucket",
      series: [
        { name: "pending", field: "pending" },
        { name: "leased", field: "leased" },
        { name: "retrying", field: "retrying" },
        { name: "delivered", field: "delivered" },
        { name: "skipped", field: "skipped" },
        { name: "failed", field: "failed" },
        { name: "dead_letter", field: "dead_letter" },
      ],
      colors: ["#d68c1f", "#1769aa", "#7f4fb3", "#12805c", "#5f6b7a", "#b95000", "#c9342f"],
    },
    "failures_callbacks.callback_samples": {
      title: "Callback 样本",
      question: "due / leased / dead_letter",
      rendererType: "html.table",
      dataSource: "failures_callbacks",
      dataPath: "callback_samples",
      emptyText: "当前窗口没有需要处理的 callback 样本",
      columns: [
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "status", label: "状态", render: statusBadge },
        { key: "event_type", label: "事件" },
        { key: "delivery_attempts", label: "尝试次数" },
        { key: "last_http_status", label: "http" },
        { key: "last_error_code", label: "error_code" },
        { key: "next_attempt_at", label: "下次尝试", value: (row) => formatDate(row.next_attempt_at) },
        { key: "updated_at", label: "更新时间", value: (row) => formatDate(row.updated_at) },
      ],
    },
    "job_trace.status": {
      rendererType: "status_line",
      dataSource: "job_trace",
      items: [
        { label: "job_id", badgePath: "job.status", valuePath: "job.job_id" },
        { label: "生成时间", valuePath: "generated_at", format: "date" },
      ],
    },
    "job_trace.summary": {
      title: "Job 摘要",
      question: "root 身份与生命周期",
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
          label: "进度",
          value: (payload) => `${getPath(payload, "job.progress_percent") ?? 0}% / ${getPath(payload, "job.progress_stage") || "-"}`,
        },
        { label: "Callback 状态", value: (payload) => getPath(payload, "job.callback_status") || "-" },
        { label: "创建时间", valuePath: "job.created_at", format: "date" },
        { label: "完成时间", valuePath: "job.finished_at", format: "date" },
      ],
    },
    "job_trace.job_request_json": {
      title: "Job 请求 JSON",
      question: "创建 Job 的请求入参",
      rendererType: "html.json_block",
      dataSource: "job_trace",
      value: (payload) => {
        const job = payload.job || {};
        return job.job_request_json;
      },
    },
    "job_trace.load_summary": {
      title: "压测摘要",
      question: "压测上下文",
      rendererType: "html.summary_table",
      dataSource: "job_trace",
      rows: [
        { label: "来源", value: (payload) => getPath(payload, "job.load_summary.source") || "-" },
        { label: "run_id", value: (payload) => getPath(payload, "job.load_summary.run_id") || "-" },
        { label: "profile", value: (payload) => getPath(payload, "job.load_summary.profile") || "-" },
        { label: "case_key", value: (payload) => getPath(payload, "job.load_summary.case_key") || "-" },
        { label: "sequence", value: (payload) => getPath(payload, "job.load_summary.sequence") ?? "-" },
      ],
    },
    "job_trace.workflow_summary": {
      title: "Workflow 摘要",
      question: "子任务状态",
      rendererType: "metric_cards",
      dataSource: "job_trace",
      cards: [
        { label: "子任务", value: (payload) => (payload.workflow_children || []).length, sub: "节点" },
        {
          label: "成功",
          value: (payload) => (payload.workflow_children || []).filter((row) => row.status === "succeeded").length,
          sub: "子任务",
        },
        {
          label: "失败",
          value: (payload) => (payload.workflow_children || []).filter((row) => row.status === "failed").length,
          sub: "子任务",
        },
        {
          label: "活跃",
          value: (payload) => (payload.workflow_children || []).filter((row) => ["queued", "running"].includes(row.status)).length,
          sub: "子任务",
        },
      ],
    },
    "job_trace.job_query_response_json": {
      title: "Job 查询返回 JSON",
      question: "调用方轮询/查询 Job 时看到的响应",
      rendererType: "html.json_block",
      dataSource: "job_trace",
      value: (payload) => {
        const job = payload.job || {};
        return job.job_query_response_json;
      },
    },
    "job_trace.callback_request_json": {
      title: "Callback 请求 JSON",
      question: "服务端投递给调用方的 callback body",
      rendererType: "html.json_block",
      dataSource: "job_trace",
      value: (payload) => (payload.callbacks || []).map((callback) => ({
        callback_id: callback.id,
        event_id: callback.event_id,
        event_type: callback.event_type,
        status: callback.status,
        delivery_attempts: callback.delivery_attempts,
        callback_request_json: callback.callback_request_json,
        created_at: callback.created_at,
        updated_at: callback.updated_at,
      })),
    },
    "job_trace.callback_response_json": {
      title: "Callback 返回 JSON",
      question: "调用方 callback 返回",
      rendererType: "html.json_block",
      dataSource: "job_trace",
      value: (payload) => (payload.callbacks || []).map((callback) => ({
        callback_id: callback.id,
        event_id: callback.event_id,
        event_type: callback.event_type,
        status: callback.status,
        delivery_attempts: callback.delivery_attempts,
        last_http_status: callback.last_http_status,
        callback_response_json: callback.callback_response_json,
        callback_error_json: callback.callback_error_json,
        callback_error_message: callback.callback_error_message,
        delivered_at: callback.delivered_at,
        dead_lettered_at: callback.dead_lettered_at,
        updated_at: callback.updated_at,
      })),
    },
    "job_trace.callback_summary": {
      title: "Callback 摘要",
      question: "投递状态",
      rendererType: "metric_cards",
      dataSource: "job_trace",
      cards: [
        { label: "Callback 数量", value: (payload) => (payload.callbacks || []).length, sub: "记录" },
        {
          label: "已送达",
          value: (payload) => (payload.callbacks || []).filter((row) => row.status === "delivered").length,
          sub: "Callback",
        },
        {
          label: "死信",
          value: (payload) => (payload.callbacks || []).filter((row) => row.status === "dead_letter").length,
          sub: "Callback",
        },
        {
          label: "尝试次数",
          value: (payload) => Math.max(0, ...(payload.callbacks || []).map((row) => Number(row.delivery_attempts || 0))),
          sub: "最大值",
        },
      ],
    },
    "job_trace.attempts": {
      title: "重试尝试",
      question: "重试决策",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "attempts",
      emptyText: "没有 attempts",
      columns: [
        { key: "purpose_attempt_no", label: "no" },
        { key: "purpose", label: "用途" },
        { key: "status", label: "状态", render: statusBadge },
        { key: "failure_phase", label: "阶段" },
        { key: "retry_eligible", label: "可重试" },
        { key: "retry_decision", label: "决策" },
        { key: "retry_decision_reason", label: "原因", wrap: true },
        { key: "policy_max_attempts", label: "最大次数" },
      ],
    },
    "job_trace.ai_calls": {
      title: "AI 调用",
      question: "provider 证据",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "ai_calls",
      emptyText: "没有 AI call ledger",
      columns: [
        { key: "status", label: "状态", render: statusBadge },
        { key: "operation", label: "操作" },
        { key: "model_id", label: "模型" },
        { key: "error_code", label: "error_code" },
        { key: "duration_ms", label: "ms" },
        { key: "billable_status", label: "billable" },
        { key: "error_message", label: "错误消息", wrap: true },
      ],
    },
    "job_trace.children": {
      title: "Workflow 子任务",
      question: "family 视图",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "workflow_children",
      emptyText: "没有 workflow children",
      columns: [
        { key: "workflow_node_key", label: "节点" },
        { key: "job_id", label: "job_id", render: jobLink },
        { key: "status", label: "状态", render: statusBadge },
        { key: "job_type", label: "job_type" },
        { key: "progress_percent", label: "%" },
        { key: "updated_at", label: "更新时间", value: (row) => formatDate(row.updated_at) },
      ],
    },
    "job_trace.timeline": {
      title: "时间线",
      question: "仅展示事件 payload 摘要",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "timeline",
      emptyText: "没有 timeline events",
      columns: [
        { key: "created_at", label: "创建时间", value: (row) => formatDate(row.created_at) },
        { key: "event_type", label: "事件" },
        { key: "from_status", label: "来源状态" },
        { key: "to_status", label: "目标状态" },
        { key: "reason", label: "原因" },
        { key: "event_payload_summary", label: "事件 payload 摘要", value: (row) => JSON.stringify(row.event_payload_summary), wrap: true },
      ],
    },
    "job_trace.callbacks": {
      title: "Callback 记录",
      question: "终态投递",
      rendererType: "html.table",
      dataSource: "job_trace",
      dataPath: "callbacks",
      emptyText: "没有 callbacks",
      columns: [
        { key: "event_type", label: "事件" },
        { key: "status", label: "状态", render: statusBadge },
        { key: "delivery_attempts", label: "尝试次数" },
        { key: "last_http_status", label: "http" },
        { key: "callback_error_message", label: "错误消息", wrap: true },
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
      title: "失败与 Callback",
      dataSource: "failures_callbacks",
      target: "view-root",
      groups: [
        { key: "summary" },
        { key: "main", className: "panel-grid" },
      ],
      placements: [
        { widgetId: "failures_callbacks.status", target: "status-line" },
        { widgetId: "failures_callbacks.summary_cards", group: "summary", chrome: "bare", hostClass: "stat-grid" },
        {
          widgetId: "failures_callbacks.failure_groups_rank",
          group: "main",
          hostClass: "chart chart-compact",
        },
        { widgetId: "failures_callbacks.callback_composition", group: "main", hostClass: "chart chart-compact" },
        {
          widgetId: "failures_callbacks.failure_groups_table",
          group: "main",
          panelClass: "panel panel-wide",
          hostClass: "table-wrap",
        },
        { widgetId: "failures_callbacks.failed_samples", group: "main", hostClass: "table-wrap" },
        { widgetId: "failures_callbacks.callback_outbox", group: "main", hostClass: "table-wrap" },
        { widgetId: "failures_callbacks.callback_samples", group: "main", hostClass: "table-wrap" },
        { widgetId: "failures_callbacks.next_checks", group: "main", hostClass: "signal-list" },
      ],
    },
    job_trace: {
      title: "Job 追踪",
      dataSource: "job_trace",
      target: "job-trace-widgets",
      emptyText: "输入 job_id 后加载 Job 追踪。",
      groups: [
        { key: "summary", title: "摘要", className: "trace-summary" },
        { key: "details", title: "明细", className: "trace-content" },
        { key: "evidence", title: "证据", className: "trace-content" },
      ],
      placements: [
        { widgetId: "job_trace.status", target: "status-line" },
        { widgetId: "job_trace.summary", group: "summary", hostClass: "table-wrap" },
        { widgetId: "job_trace.load_summary", group: "summary", hostClass: "table-wrap" },
        { widgetId: "job_trace.workflow_summary", group: "summary", chrome: "bare", hostClass: "stat-grid" },
        { widgetId: "job_trace.callback_summary", group: "summary", chrome: "bare", hostClass: "stat-grid" },
        { widgetId: "job_trace.job_request_json", group: "details" },
        { widgetId: "job_trace.job_query_response_json", group: "details" },
        { widgetId: "job_trace.callback_request_json", group: "details" },
        { widgetId: "job_trace.callback_response_json", group: "details" },
        { widgetId: "job_trace.attempts", group: "evidence", hostClass: "table-wrap" },
        { widgetId: "job_trace.ai_calls", group: "evidence", hostClass: "table-wrap" },
        { widgetId: "job_trace.children", group: "evidence", hostClass: "table-wrap" },
        { widgetId: "job_trace.timeline", group: "evidence", hostClass: "table-wrap" },
        { widgetId: "job_trace.callbacks", group: "evidence", hostClass: "table-wrap" },
      ],
    },
  });

  const WIDGET_DATA_ADAPTERS = Object.freeze({
    latency_p95_rows: (payload) => {
      const row = (getPath(payload, "latency") || [])[0] || {};
      return [
        { label: "排队", value: row.queue_wait_p95_seconds },
        { label: "执行", value: row.run_p95_seconds },
        { label: "生命周期", value: row.lifecycle_p95_seconds },
      ];
    },
    callback_composition_rows: (payload) => {
      const summary = getPath(payload, "callback_summary") || {};
      return [
        {
          bucket: "窗口",
          pending: summary.pending,
          leased: summary.leased,
          retrying: summary.retrying,
          delivered: summary.delivered,
          skipped: summary.skipped,
          failed: summary.failed,
          dead_letter: summary.dead_letter,
        },
      ];
    },
  });

  function $(selector) {
    return document.querySelector(selector);
  }

  function filterConfig() {
    return state.config?.filters || {};
  }

  function populateSelect(select, options, selected) {
    if (!select || !Array.isArray(options)) return;
    select.innerHTML = options
      .map((option) => `
        <option value="${escapeHtml(option)}" ${String(option) === String(selected) ? "selected" : ""}>${escapeHtml(option)}</option>
      `)
      .join("");
  }

  function initializeGlobalFilters() {
    const config = filterConfig();
    populateSelect(document.querySelector('[name="window"]'), config.windows, config.default_window);
    state.appliedFilters = readGlobalFilterDraft();
  }

  function normalizeControlValue(value) {
    return value === undefined || value === null ? "" : String(value).trim();
  }

  function readGlobalFilterDraft() {
    const form = new FormData($("#filters"));
    return {
      window: normalizeControlValue(form.get("window") || filterConfig().default_window),
      caller_id: normalizeControlValue(form.get("caller_id")),
      job_type: normalizeControlValue(form.get("job_type")),
      run_id: normalizeControlValue(form.get("run_id")),
    };
  }

  function appendGlobalFilter(params, filters, key) {
    const normalized = normalizeControlValue(filters[key]);
    if (normalized) params.set(key, normalized);
  }

  function filterParams() {
    const params = new URLSearchParams();
    const filters = state.appliedFilters;
    appendGlobalFilter(params, filters, "window");
    appendGlobalFilter(params, filters, "caller_id");
    appendGlobalFilter(params, filters, "job_type");
    appendGlobalFilter(params, filters, "run_id");
    return params;
  }

  function refreshWithGlobalFilters() {
    state.appliedFilters = readGlobalFilterDraft();
    loadSection(state.section);
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
      state.pageControlDrafts[section] ||= {};
      for (const control of controls) {
        if (control.default !== undefined && state.pageControls[section][control.key] === undefined) {
          state.pageControls[section][control.key] = control.default;
        }
        if (state.pageControlDrafts[section][control.key] === undefined && state.pageControls[section][control.key] !== undefined) {
          state.pageControlDrafts[section][control.key] = state.pageControls[section][control.key];
        }
      }
    }
  }

  function appliedControlValue(section, control, params) {
    if (params && control.key in params) return params[control.key];
    if (control.key === "job_id" && params?.jobId) return params.jobId;
    const saved = state.pageControls[section]?.[control.key];
    if (saved !== undefined) return saved;
    return control.default;
  }

  function draftControlValue(section, control, params) {
    if (params && control.key in params) return params[control.key];
    if (control.key === "job_id" && params?.jobId) return params.jobId;
    const draft = state.pageControlDrafts[section]?.[control.key];
    if (draft !== undefined) return draft;
    return appliedControlValue(section, control, params);
  }

  function routeControlsReady(section, params) {
    return pageControls(section)
      .filter((control) => control.binding === "route")
      .every((control) => {
        const value = appliedControlValue(section, control, params);
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
      const value = appliedControlValue(key, control, params);
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

  function currentPageJson() {
    if (!Object.prototype.hasOwnProperty.call(state.pageJsonBySection, state.section)) return undefined;
    return state.pageJsonBySection[state.section];
  }

  function setPageJson(section, payload) {
    state.pageJsonBySection[section] = payload;
    if (state.section === section) updatePageJsonActions();
  }

  function clearPageJson(section) {
    delete state.pageJsonBySection[section];
    if (state.section === section) updatePageJsonActions();
  }

  function actionStatusNode() {
    return document.getElementById("page-json-action-status");
  }

  function setActionStatus(message, tone = "neutral") {
    const status = actionStatusNode();
    if (!status) return;
    if (state.actionNoticeTimer) window.clearTimeout(state.actionNoticeTimer);
    state.actionNoticeTimer = null;
    status.textContent = message || "";
    status.dataset.tone = tone;
    if (!message) return;
    state.actionNoticeTimer = window.setTimeout(() => {
      status.textContent = "";
      status.dataset.tone = "neutral";
      state.actionNoticeTimer = null;
    }, 1800);
  }

  function ensurePageJsonActions() {
    const toolbar = document.querySelector(".toolbar");
    const meta = toolbar?.firstElementChild;
    if (!meta) return null;
    meta.classList.add("toolbar-meta");
    let actions = document.getElementById("page-json-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.id = "page-json-actions";
      actions.className = "toolbar-actions";
      actions.innerHTML = `
        <button class="secondary-button" type="button" data-page-json-action="copy">Copy Page JSON</button>
        <button class="secondary-button" type="button" data-page-json-action="export">Export Page JSON</button>
        <span id="page-json-action-status" class="toolbar-action-status" aria-live="polite"></span>
      `;
      meta.appendChild(actions);
    }
    return actions;
  }

  function exportFileName(section) {
    const suffix =
      section === "job_trace" ? String(state.pageControls.job_trace?.job_id || "").trim().replace(/[^a-zA-Z0-9._-]+/g, "-") : "";
    return suffix ? `ops-dashboard-${section}-${suffix}.json` : `ops-dashboard-${section}.json`;
  }

  function updatePageJsonActions() {
    const actions = ensurePageJsonActions();
    if (!actions) return;
    const pageJson = currentPageJson();
    const hasPageJson = pageJson !== undefined;
    const sectionTitle = LAYOUT_REGISTRY[state.section]?.title || state.section;
    const copyButton = actions.querySelector('[data-page-json-action="copy"]');
    const exportButton = actions.querySelector('[data-page-json-action="export"]');
    if (!copyButton || !exportButton) return;
    copyButton.disabled = !hasPageJson;
    exportButton.disabled = !hasPageJson;
    copyButton.title = hasPageJson ? `复制 ${sectionTitle} 整页当前 JSON` : `${sectionTitle} 当前暂无已加载 JSON`;
    exportButton.title = hasPageJson ? `导出 ${sectionTitle} 整页当前 JSON` : `${sectionTitle} 当前暂无已加载 JSON`;
    copyButton.setAttribute("aria-label", `${sectionTitle} Copy Page JSON`);
    exportButton.setAttribute("aria-label", `${sectionTitle} Export Page JSON`);
    if (!hasPageJson) setActionStatus("");
  }

  async function copyCurrentPageJson() {
    const pageJson = currentPageJson();
    if (pageJson === undefined) return;
    try {
      await navigator.clipboard.writeText(formatJson(pageJson));
      setActionStatus("已复制整页 JSON", "success");
    } catch (error) {
      setActionStatus(`复制失败：${error?.message || String(error)}`, "error");
    }
  }

  function exportCurrentPageJson() {
    const pageJson = currentPageJson();
    if (pageJson === undefined) return;
    try {
      const blob = new Blob([formatJson(pageJson)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = exportFileName(state.section);
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setActionStatus("已导出整页 JSON", "success");
    } catch (error) {
      setActionStatus(`导出失败：${error?.message || String(error)}`, "error");
    }
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
    if (state.section !== section) setActionStatus("");
    state.section = section;
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.section === section);
    });
    $("#section-title").textContent = LAYOUT_REGISTRY[section]?.title || section;
    updatePageJsonActions();
  }

  function renderControlInput(section, control, context) {
    const value = draftControlValue(section, control, context);
    if (control.type === "select") {
      const options = (control.options || [])
        .map((option) => `
          <option value="${escapeHtml(option)}" ${String(value) === String(option) ? "selected" : ""}>${escapeHtml(option)}</option>
        `)
        .join("");
      return `
        <label class="control-field control-${escapeHtml(control.key)}">
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
      control.placeholder ? `placeholder="${escapeHtml(control.placeholder)}"` : "",
      control.pattern ? `pattern="${escapeHtml(control.pattern)}"` : "",
      control.pattern ? `title="${escapeHtml(control.placeholder || control.label)}"` : "",
      control.type !== "number" ? "autocomplete=\"off\"" : "",
    ].filter(Boolean).join(" ");
    return `
      <label class="control-field control-${escapeHtml(control.key)}">
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
        <span class="query-dirty" data-page-query-dirty hidden>查询条件已修改，点击查询生效</span>
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
        clearPageJson(section);
        renderPage(section, null);
        return;
      }
      const payload = await fetchJson(dataSourceUrl(section));
      setPageJson(section, payload);
      renderPage(section, payload);
      scheduleRefresh(section);
    } catch (error) {
      const message = error.message || String(error);
      setError(message);
      if (section === "job_trace") {
        clearPageJson(section);
        renderPage(section, null, { message: `查询失败：${message}` });
        setActionStatus("查询失败，当前无可复制/导出 JSON", "error");
        return;
      }
      setActionStatus("刷新失败，复制/导出仍为上次成功 JSON", "error");
    }
  }

  async function loadJobTrace(jobId) {
    if (!jobId) return;
    state.pageControls.job_trace ||= {};
    state.pageControlDrafts.job_trace ||= {};
    state.pageControls.job_trace.job_id = jobId;
    state.pageControlDrafts.job_trace.job_id = jobId;
    await loadSection("job_trace");
  }

  function bindPageControls(section) {
    const form = $("#page-controls");
    if (!form) return;
    const dirtyNotice = form.querySelector("[data-page-query-dirty]");
    const updateDirtyNotice = () => {
      const values = {};
      for (const [key, value] of new FormData(form).entries()) {
        values[key] = normalizeControlValue(value);
      }
      state.pageControlDrafts[section] = { ...(state.pageControlDrafts[section] || {}), ...values };
      const controls = pageControls(section);
      const dirty = controls.some((control) => normalizeControlValue(state.pageControls[section]?.[control.key] ?? control.default) !== normalizeControlValue(values[control.key]));
      state.pageControlsDirty[section] = dirty;
      if (dirtyNotice) dirtyNotice.hidden = !dirty;
    };
    form.addEventListener("input", updateDirtyNotice);
    form.addEventListener("change", updateDirtyNotice);
    updateDirtyNotice();
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = {};
      for (const [key, value] of new FormData(event.currentTarget).entries()) {
        values[key] = normalizeControlValue(value);
      }
      state.pageControls[section] = { ...(state.pageControls[section] || {}), ...values };
      state.pageControlDrafts[section] = { ...(state.pageControlDrafts[section] || {}), ...values };
      state.pageControlsDirty[section] = false;
      if (dirtyNotice) dirtyNotice.hidden = true;
      if (section === "job_trace" && values.job_id) {
        window.location.hash = `job=${encodeURIComponent(values.job_id)}`;
      }
      await loadSection(section);
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
      initializeGlobalFilters();
      initializePageControls();
    } catch (error) {
      setError(error.message || String(error));
    }
    renderNavigation();
    ensurePageJsonActions();
    updatePageJsonActions();
    $("#filters").addEventListener("submit", (event) => {
      event.preventDefault();
      refreshWithGlobalFilters();
    });
    document.body.addEventListener("click", async (event) => {
      const actionButton = event.target.closest("[data-page-json-action]");
      if (actionButton) {
        if (actionButton.dataset.pageJsonAction === "copy") {
          await copyCurrentPageJson();
        } else if (actionButton.dataset.pageJsonAction === "export") {
          exportCurrentPageJson();
        }
        return;
      }
      const button = event.target.closest("[data-job-id]");
      if (!button) return;
      const jobId = button.dataset.jobId;
      window.location.hash = `job=${encodeURIComponent(jobId)}`;
      await loadJobTrace(jobId);
    });
    const hashJob = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("job");
    if (hashJob) {
      await loadJobTrace(hashJob);
      return;
    }
    await loadSection("overview");
  }

  window.addEventListener("resize", resizeCharts);
  window.addEventListener("DOMContentLoaded", init);
})();
