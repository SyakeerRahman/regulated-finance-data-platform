import { useEffect, useState } from "react";
import { Panel, Pill } from "../components/Chrome.jsx";
import { getQuality } from "../api.js";

export default function Pipeline() {
  const [data, setData] = useState({ batches: [], checks: [], results: [] });
  const [error, setError] = useState("");
  useEffect(() => {
    getQuality()
      .then(setData)
      .catch((reason) => setError(String(reason.message ?? reason)));
  }, []);

  const byCell = new Map(data.results.map((row) => [`${row.batch_id}|${row.check}`, row]));
  const failures = data.results.filter((row) => !row.passed);

  return (
    <div className="space-y-4">
      <Panel
        title="Data quality"
        note="One column for each batch. A critical failure stops the run before gold."
        right={
          <span className="flex gap-2">
            <Pill tone="good">pass</Pill>
            <Pill tone="critical">fail</Pill>
          </span>
        }
      >
        {error ? (
          <p className="px-4 py-8 text-center text-sm text-critical">Could not load the checks: {error}</p>
        ) : data.batches.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-ink-muted">
            No checks recorded. Run: uv run python -m finplat.run 2026-09-01
          </p>
        ) : (
          <div className="overflow-x-auto px-3 py-3">
            <table className="text-sm">
              <thead>
                <tr>
                  <th className="sticky left-0 bg-surface px-2 py-1 text-left text-[11px] font-medium uppercase tracking-wide text-ink-muted">
                    Check
                  </th>
                  {data.batches.map((batch) => (
                    <th
                      key={batch}
                      className="tabular px-1 py-1 text-[11px] font-normal text-ink-muted"
                      title={batch}
                    >
                      {batch.replace("live-", "").slice(5)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.checks.map((check) => (
                  <tr key={check}>
                    <td className="sticky left-0 bg-surface px-2 py-1 whitespace-nowrap text-ink-2">{check}</td>
                    {data.batches.map((batch) => {
                      const cell = byCell.get(`${batch}|${check}`);
                      const tone = !cell ? "none" : cell.passed ? "pass" : cell.severity;
                      const look = {
                        none: "border-white/10 text-ink-muted",
                        pass: "border-good/40 bg-good/15 text-good",
                        warning: "border-warning/40 bg-warning/15 text-warning",
                        critical: "border-critical/40 bg-critical/20 text-critical",
                      }[tone];
                      const glyph = { none: "·", pass: "✓", warning: "!", critical: "✕" }[tone];
                      return (
                        <td key={batch} className="px-1 py-1">
                          {/* A glyph, not only a colour. Good and critical sit 4.1 apart under
                              deuteranopia, so the shape carries the meaning. */}
                          <span
                            title={cell ? `${check} on ${batch}: ${cell.detail}` : "not run"}
                            className={`flex h-6 w-7 items-center justify-center rounded border text-xs ${look}`}
                          >
                            {glyph}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="What failed" note="Hover any cell above for its detail">
        {/* "Every check passed" is a claim. Make it only when checks were loaded and ran. */}
        {error || data.results.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-ink-muted">No results to report.</p>
        ) : failures.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-ink-muted">Every check passed.</p>
        ) : (
          <ul className="space-y-2 px-4 py-3 text-sm">
            {failures.map((row) => (
              <li key={`${row.batch_id}-${row.check}`} className="flex flex-wrap items-baseline gap-2">
                <Pill tone={row.severity === "critical" ? "critical" : "warning"}>{row.severity}</Pill>
                <span className="tabular text-ink-muted">{row.batch_id}</span>
                <span className="text-ink">{row.check}</span>
                <span className="text-ink-2">{row.detail}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
