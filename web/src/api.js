const json = async (path, options) => {
  const response = await fetch(path, options);
  if (!response.ok) {
    // FastAPI puts the reason in `detail`. The AI panel shows it, so "no key" reads as no key.
    const detail = await response.json().then((body) => body?.detail, () => null);
    const error = new Error(typeof detail === "string" ? detail : `${path} answered ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
};

export const getState = () => json("/api/state");
export const getAlerts = (status) => json(`/api/alerts?limit=50${status ? `&status=${status}` : ""}`);
export const getModel = () => json("/api/model");
export const getQuality = () => json("/api/quality");
export const getAccount = (id) => json(`/api/accounts/${encodeURIComponent(id)}`);

export const start = (rate) => json(`/api/start?rate=${rate}`, { method: "POST" });
export const stop = () => json("/api/stop", { method: "POST" });
export const decide = (id, status) => json(`/api/alerts/${id}/decision?status=${status}`, { method: "POST" });
export const exportLabels = () => json("/api/labels/export", { method: "POST" });

export const money = (value) =>
  new Intl.NumberFormat("en-MY", { style: "currency", currency: "MYR" }).format(value);
// Local time, the same clock the charts use. The API sends UTC.
export const clock = (iso) => (iso ? new Date(iso).toTimeString().slice(0, 8) : "");

export const getLatestAlert = () => json("/api/alerts?limit=1");

export const COUNTRY_NAMES = {
  MY: "Malaysia",
  SG: "Singapore",
  TH: "Thailand",
  ID: "Indonesia",
  GB: "United Kingdom",
  US: "United States",
  NG: "Nigeria",
};

/** "online_gaming" -> "Online gaming". */
export const pretty = (value) => {
  const text = String(value ?? "").replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export const getAlertSummary = () => json("/api/alerts/summary");

/** One page of alerts. Empty filters are left out of the query. */
export const searchAlerts = (filters) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== "" && value != null) params.set(key, value);
  }
  return json(`/api/alerts?${params}`);
};

export const decideMany = (alertIds, status) =>
  json("/api/alerts/decisions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ alert_ids: alertIds, status }),
  });

/** The short name of each SHAP reason, for a filter or a table cell. The full sentence is on the alert. */
export const REASON_LABELS = {
  amount_vs_account: "Unusual for account",
  is_abroad: "Outside Malaysia",
  is_risky_category: "Resellable category",
  amount: "Large amount",
  is_online: "Card not present",
  is_night: "Night time",
  prior_transactions: "Thin history",
  hour: "Time of day",
};

export const reasonLabel = (key) => REASON_LABELS[key] ?? (key ? pretty(key) : "-");

/** Status colours and words, the same on every chart, pill and legend. */
export const ALERT_STATUS = {
  open: { label: "Open", tone: "warning", color: "var(--warning)" },
  confirmed_fraud: { label: "Confirmed", tone: "critical", color: "var(--critical)" },
  false_positive: { label: "False positive", tone: "info", color: "var(--series-1)" },
};

export const getPipeline = () => json("/api/pipeline");
export const triggerDag = (dagId) => json(`/api/pipeline/dags/${encodeURIComponent(dagId)}/trigger`, { method: "POST" });

/** 5,201,975 -> "5.0 MB". */
export const bytes = (value) => {
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
};

/** How long ago, in the largest unit that fits. */
export const age = (iso) => {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return `${Math.round(seconds)} s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
};

export const getEvaluation = (version) => json(`/api/model/evaluation?version=${encodeURIComponent(version)}`);
export const getDrift = () => json("/api/model/drift");
export const promoteVersion = (version) => json(`/api/model/versions/${encodeURIComponent(version)}/promote`, { method: "POST" });

export const getLakeTables = () => json("/api/lake/tables");
export const getLakeRows = (filters) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== "" && value != null) params.set(key, value);
  }
  return json(`/api/lake/rows?${params}`);
};
export const getTrace = (id) => json(`/api/lake/trace/${encodeURIComponent(id)}`);

// Stage G: the AI layer.
export const getAi = () => json("/api/ai");
export const getAiEvaluation = () => json("/api/ai/evaluation");
let policyRequest = null;
/** The policy does not change while the page is open, so it is fetched once. */
export const getPolicy = () => {
  policyRequest ??= json("/api/policy").catch((error) => {
    policyRequest = null;
    throw error;
  });
  return policyRequest;
};
export const narrate = (id, refresh = false) =>
  json(`/api/alerts/${id}/narrative${refresh ? "?refresh=true" : ""}`, { method: "POST" });
export const writeCaseNote = (id, refresh = false) =>
  json(`/api/alerts/${id}/case-note${refresh ? "?refresh=true" : ""}`, { method: "POST" });
export const ask = (messages) =>
  json("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages }) });

export const SUGGESTION = {
  likely_fraud: { label: "Likely fraud", tone: "critical" },
  likely_false_positive: { label: "Likely false positive", tone: "info" },
  unsure: { label: "Unsure", tone: "muted" },
};
