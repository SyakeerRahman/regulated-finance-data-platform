import { useEffect, useRef, useState } from "react";
import { Panel, Pill } from "../components/Chrome.jsx";
import { Icon } from "../components/Icons.jsx";
import Markdown from "../components/Markdown.jsx";
import { ask, getAi, getAiEvaluation, getPolicy } from "../api.js";

const STARTERS = [
  "Which accounts had the most alerts this week?",
  "Show me open alerts outside Malaysia over MYR 300",
  "Why was the latest alert flagged, and which rule does it break?",
  "How is the live model doing, and how often does the AI agree with analysts?",
];

// The server accepts 20 messages. The oldest go first, so a long chat keeps working.
const KEEP = 18;

const TOOL_NAMES = {
  search_alerts: "Search alerts",
  get_alert: "Read one alert",
  account_history: "Account history",
  alert_stats: "Alert counts",
  top_accounts: "Top accounts",
  policy_rules: "The policy",
  model_info: "The model",
  ai_agreement: "AI agreement",
};

function Message({ message }) {
  if (message.role === "user") {
    return (
      <div className="ml-auto max-w-[85%] rounded-xl rounded-br-sm bg-series-1/20 px-3 py-2 text-sm text-ink">{message.content}</div>
    );
  }
  return (
    <div className="max-w-[95%] rounded-xl rounded-bl-sm border border-white/10 bg-white/[0.03] px-3 py-2">
      {message.error ? <p className="text-sm text-serious">{message.content}</p> : <Markdown text={message.content} />}
      {message.tools?.length > 0 && (
        <p className="mt-2 text-[11px] text-ink-muted">
          Read with: {[...new Set(message.tools.map((call) => TOOL_NAMES[call.tool] ?? call.tool))].join(", ")}
        </p>
      )}
    </div>
  );
}

export default function Ask() {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [policy, setPolicy] = useState(null);
  const [evaluation, setEvaluation] = useState(null);
  const bottom = useRef(null);

  useEffect(() => {
    getAi().then(setStatus).catch(() => {});
    getPolicy().then(setPolicy).catch(() => {});
    getAiEvaluation().then((answer) => setEvaluation(answer.evaluation)).catch(() => {});
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, busy]);

  const send = async (text) => {
    const question = text.trim();
    if (!question || busy) return;
    const next = [...messages, { role: "user", content: question }];
    setMessages(next);
    setDraft("");
    setBusy(true);
    try {
      // Only the words go back to the server. Errors and tool lists are for this screen.
      const history = next.filter((message) => !message.error).slice(-KEEP).map(({ role, content }) => ({ role, content }));
      const answer = await ask(history);
      setMessages((previous) => [...previous, { role: "assistant", content: answer.answer || "(no answer)", tools: answer.tools }]);
      getAi().then(setStatus).catch(() => {});
    } catch (failure) {
      setMessages((previous) => [...previous, { role: "assistant", content: `No answer: ${failure.message}`, error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const agreement = status?.agreement;

  return (
    <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
      <section className="flex min-h-[32rem] flex-col rounded-xl border border-white/10 bg-surface xl:col-span-8">
        <header className="flex items-center gap-3 border-b border-white/10 px-4 py-3">
          <Icon name="chat" className="h-5 w-5 text-series-1" />
          <h2 className="text-[15px] font-semibold text-ink">Ask the data</h2>
          {messages.length > 0 && (
            <button type="button" onClick={() => setMessages([])} className="ml-auto rounded-md px-2 py-1 text-xs text-ink-muted hover:bg-white/5 hover:text-ink">
              New chat
            </button>
          )}
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
          {messages.length === 0 && (
            <div className="space-y-3 py-6">
              <p className="text-sm text-ink-2">
                Ask about alerts, accounts, the policy or the model. The assistant reads the live data with read-only tools. It can explain and
                suggest, but only a person can decide an alert.
              </p>
              <div className="flex flex-wrap gap-2">
                {STARTERS.map((starter) => (
                  <button
                    key={starter}
                    type="button"
                    onClick={() => send(starter)}
                    disabled={status?.enabled === false}
                    className="rounded-full border border-white/15 px-3 py-1.5 text-left text-xs text-ink-2 hover:border-series-1/60 hover:text-ink disabled:opacity-40"
                  >
                    {starter}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message, index) => (
            <Message key={index} message={message} />
          ))}
          {busy && (
            <p className="flex items-center gap-2 text-sm text-ink-muted">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/20 border-t-series-1" aria-hidden="true" />
              Reading the dataâ€¦
            </p>
          )}
          <div ref={bottom} />
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            send(draft);
          }}
          className="flex gap-2 border-t border-white/10 p-3"
        >
          <label htmlFor="ask-input" className="sr-only">
            Your question
          </label>
          <input
            id="ask-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={2000}
            disabled={status?.enabled === false}
            placeholder={status?.enabled === false ? "The AI is off. Set LLM_API_KEY in .env." : "Ask a question about the alerts, an account or the model"}
            className="min-w-0 flex-1 rounded-lg border border-white/15 bg-plane px-3 py-2 text-sm text-ink placeholder:text-ink-muted"
          />
          <button
            type="submit"
            disabled={busy || !draft.trim() || status?.enabled === false}
            className="rounded-lg border border-series-1/60 px-4 py-2 text-sm font-medium text-series-1 hover:bg-series-1/10 disabled:opacity-40"
          >
            Ask
          </button>
        </form>
      </section>

      <div className="space-y-4 xl:col-span-4">
        <Panel title="The AI layer" note="What answers, and how well it agrees with analysts">
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 px-4 py-3 text-sm">
            <dt className="text-ink-muted">Status</dt>
            <dd>{status ? <Pill tone={status.enabled ? "good" : "muted"}>{status.enabled ? "On" : "Off"}</Pill> : "â€¦"}</dd>
            <dt className="text-ink-muted">Model</dt>
            <dd className="tabular truncate text-ink">{status?.model ?? "â€¦"}</dd>
            <dt className="text-ink-muted">Provider</dt>
            <dd className="tabular truncate text-ink">{status ? new URL(status.base_url).hostname : "â€¦"}</dd>
            <dt className="text-ink-muted">Calls today</dt>
            <dd className="tabular text-ink">
              {status?.budget ? `${status.budget.used} of ${status.budget.per_day}` : "…"}
            </dd>
            <dt className="text-ink-muted">Agreement</dt>
            <dd className="text-ink">
              {agreement?.rate != null
                ? `${Math.round(agreement.rate * 100)}% of ${agreement.decided} decided alerts`
                : "No decided alert has an AI suggestion yet"}
            </dd>
            {agreement?.unsure > 0 && (
              <>
                <dt className="text-ink-muted">Unsure</dt>
                <dd className="text-ink">{agreement.unsure} decided alerts, counted apart</dd>
              </>
            )}
          </dl>
          <div className="border-t border-white/10 px-4 py-3">
            <div className="text-[11px] font-medium uppercase tracking-wide text-ink-muted">Prompt versions</div>
            <ul className="mt-1 space-y-0.5 text-xs">
              {Object.entries(status?.prompts ?? {}).map(([name, version]) => (
                <li key={name} className="flex justify-between">
                  <span className="text-ink-2">{name}</span>
                  <span className="tabular text-ink-muted">{version}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-ink-muted">Each version is a hash of the prompt file, and every AI answer stores the one that wrote it.</p>
          </div>
        </Panel>

        <Panel title="Graded against the truth" note="The AI's suggestions on past alerts, scored against the real answers">
          {evaluation ? (
            <div className="space-y-2 px-4 py-3 text-sm">
              <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1">
                {[
                  ["Right, when it answers", evaluation.accuracy],
                  ["Always saying fraud", evaluation.baseline_accuracy],
                  ["False alarms it spots", evaluation.false_positives_caught],
                  ["Fraud it calls a false alarm", evaluation.matrix ? evaluation.matrix.likely_false_positive.fraud / Math.max(1, Object.values(evaluation.matrix).reduce((n, row) => n + row.fraud, 0)) : null],
                ].map(([label, value]) => (
                  <div key={label} className="contents">
                    <dt className="text-ink-2">{label}</dt>
                    <dd className="tabular text-right text-ink">{value == null ? "-" : `${Math.round(value * 100)}%`}</dd>
                  </div>
                ))}
              </dl>
              <p className="text-[11px] text-ink-muted">
                {evaluation.alerts} alerts · {evaluation.model} · prompt {evaluation.prompt_version}. The last row is why a person decides: trusting
                the AI alone would close that share of real fraud.
              </p>
            </div>
          ) : (
            <p className="px-4 py-3 text-sm text-ink-muted">
              Not graded yet. Run <code className="rounded bg-white/10 px-1">python -m finplat.ai_eval</code>.
            </p>
          )}
        </Panel>

        <Panel title={policy?.name ?? "The policy"} note="Code picks the rule. The AI only explains it">
          <ul className="max-h-96 divide-y divide-white/5 overflow-y-auto">
            {(policy?.rules ?? []).map((rule) => (
              <li key={rule.rule_id} className="px-4 py-2">
                <div className="flex gap-2 text-sm">
                  <span className="tabular shrink-0 text-series-1">{rule.rule_id}</span>
                  <span className="font-medium text-ink">{rule.title}</span>
                </div>
                <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{rule.text}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
