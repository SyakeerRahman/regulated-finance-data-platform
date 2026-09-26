import { useEffect, useState } from "react";
import { Panel, Pill, Stat, Table } from "../components/Chrome.jsx";
import { getModel } from "../api.js";

export default function Model() {
  const [data, setData] = useState({ versions: [] });
  useEffect(() => {
    getModel().then(setData);
  }, []);

  const live = data.versions.find((version) => version.version === data.live);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Live version" value={data.live ? `v${data.live}` : "none"} sub={data.name} />
        <Stat label="PR-AUC" value={live?.pr_auc ?? "-"} sub="the threshold-free score" />
        <Stat label="Precision" value={live?.precision ?? "-"} sub="at the chosen threshold" />
        <Stat label="Recall" value={live?.recall ?? "-"} sub="at the chosen threshold" />
      </div>

      <Panel
        title="Registered versions"
        note="Promotion changes the live model with no deploy. Rollback is the same command."
      >
        <Table
          rows={data.versions}
          rowKey={(row) => row.version}
          empty="Nothing registered. Run: uv run python -m finplat.train 2027-01-01"
          columns={[
            { key: "version", label: "Version", mono: true, render: (row) => `v${row.version}` },
            { key: "pr_auc", label: "PR-AUC", right: true },
            { key: "precision", label: "Precision", right: true },
            { key: "recall", label: "Recall", right: true },
            {
              key: "alias",
              label: "Alias",
              render: (row) =>
                row.alias === "production" ? <Pill tone="good">production</Pill> : <Pill tone="muted">none</Pill>,
            },
          ]}
        />
      </Panel>

      <Panel title="Why accuracy is not here">
        <p className="px-4 py-3 text-sm text-ink-2">
          Fraud is about 1.5% of rows. A model that always answers &ldquo;not fraud&rdquo; scores 98.5%
          accuracy and catches nothing. PR-AUC does not depend on a threshold, so it is the number
          that compares two runs. Precision and recall are reported at the threshold the training run
          chose from the precision-recall curve, and the live service reads that threshold from the
          registry rather than holding one of its own.
        </p>
      </Panel>
    </div>
  );
}
