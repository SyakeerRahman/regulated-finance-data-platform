const json = async (path, options) => {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${path} answered ${response.status}`);
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
export const clock = (iso) => (iso ? String(iso).slice(11, 19) : "");
