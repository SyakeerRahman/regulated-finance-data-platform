import { Icon } from "./Icons.jsx";

const LOOK = {
  healthy: { ring: "border-good/50", dot: "bg-good", text: "text-good", glyph: "✓" },
  warning: { ring: "border-warning/60", dot: "bg-warning", text: "text-warning", glyph: "!" },
  error: { ring: "border-critical/60", dot: "bg-critical", text: "text-critical", glyph: "✕" },
  idle: { ring: "border-white/15", dot: "bg-ink-muted", text: "text-ink-muted", glyph: "·" },
};

function Node({ node, last }) {
  const look = LOOK[node.status] ?? LOOK.idle;
  return (
    <li className="relative min-w-0">
      <div className={`flex h-full flex-col gap-2 rounded-lg border bg-white/[0.02] p-3 ${look.ring}`}>
        <div className="flex items-center gap-2">
          <Icon name={node.icon} className="h-4 w-4 shrink-0 text-ink-2" />
          <span className="truncate text-sm font-semibold text-ink">{node.title}</span>
          {/* The word carries the state. The colour only repeats it. */}
          <span className={`ml-auto inline-flex shrink-0 items-center gap-1 text-[11px] font-medium ${look.text}`}>
            <span aria-hidden="true">{look.glyph}</span>
            {node.statusText}
          </span>
        </div>
        {node.lines.map((line) => (
          <span key={line} className="tabular truncate text-xs text-ink-2">
            {line}
          </span>
        ))}
      </div>
      {!last && (
        <>
          <span className="absolute -right-5 top-1/2 hidden -translate-y-1/2 text-ink-muted xl:block" aria-hidden="true">
            →
          </span>
          <span className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-ink-muted xl:hidden" aria-hidden="true">
            ↓
          </span>
        </>
      )}
    </li>
  );
}

/** One lane of the flow. `nodes` run left to right on a wide screen and top to bottom on a phone. */
export function Lane({ name, schedule, nodes }) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">{name}</h3>
        <span className="text-xs text-ink-muted">{schedule}</span>
      </div>
      <ol className="grid gap-6 xl:grid-cols-5 xl:gap-8">
        {nodes.map((node, index) => (
          <Node key={node.title} node={node} last={index === nodes.length - 1} />
        ))}
      </ol>
    </div>
  );
}

export function FlowLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
      {[
        ["healthy", "Healthy"],
        ["warning", "Needs a look"],
        ["error", "Failing"],
      ].map(([status, label]) => (
        <span key={status} className="inline-flex items-center gap-1.5">
          <span className={LOOK[status].text} aria-hidden="true">
            {LOOK[status].glyph}
          </span>
          {label}
        </span>
      ))}
    </div>
  );
}
