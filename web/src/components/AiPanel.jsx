import { useEffect, useState } from "react";
import { Pill } from "./Chrome.jsx";
import { Icon } from "./Icons.jsx";
import Markdown from "./Markdown.jsx";
import {
  ALERT_STATUS,
  SUGGESTION,
  getAi,
  getCitedCases,
  getPolicy,
  getSimilar,
  money,
  narrate,
  pretty,
  writeCaseNote,
} from "../api.js";

let statusRequest = null;
const aiStatus = () => {
  statusRequest ??= getAi().catch((error) => {
    statusRequest = null;
    throw error;
  });
  return statusRequest;
};

/** The rule the alert breaks. Chosen by code when the alert was raised, so it shows with or without the AI. */
export function PolicyCitation({ ruleIds }) {
  const [policy, setPolicy] = useState(null);
  useEffect(() => {
    getPolicy().then(setPolicy).catch(() => {});
  }, []);

  if (!ruleIds?.length) return null;
  const byId = Object.fromEntries((policy?.rules ?? []).map((rule) => [rule.rule_id, rule]));
  const [first, ...others] = ruleIds;
  const rule = byId[first];

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-series-1">
        <Icon name="shield" className="h-4 w-4" />
        Policy rule
        <span className="tabular ml-auto rounded bg-series-1/15 px-1.5 text-xs">{first}</span>
      </div>
      {rule ? (
        <>
          <p className="mt-1.5 text-sm font-medium text-ink">{rule.title}</p>
          <p className="mt-0.5 text-sm leading-relaxed text-ink-2">{rule.text}</p>
        </>
      ) : (
        <p className="mt-1.5 text-sm text-ink-muted">Loading the policy.</p>
      )}
      {others.length > 0 && (
        <p className="mt-2 text-xs text-ink-muted">
          Also breaks:{" "}
          {others.map((id, index) => (
            <span key={id} title={byId[id]?.title}>
              {index > 0 && ", "}
              {id}
              {byId[id] ? ` (${byId[id].title})` : ""}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

function Waiting({ children }) {
  return (
    <p className="flex items-center gap-2 text-sm text-ink-muted">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/20 border-t-series-1" aria-hidden="true" />
      {children}
    </p>
  );
}

/** Two sentences and a suggestion from the language model. Advice beside the decision, never in it. */
export function AiNarrative({ alert, onOpenAlert }) {
  const [enabled, setEnabled] = useState(null);
  const [answer, setAnswer] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const [noteBusy, setNoteBusy] = useState(false);
  const [noteError, setNoteError] = useState(null);

  const id = alert.alert_id;

  useEffect(() => {
    aiStatus().then((status) => setEnabled(status.enabled)).catch(() => setEnabled(false));
  }, []);

  // A new alert starts clean. The list refreshes every 10 seconds with a new object for the same
  // alert, so this keys on the id and not on the object.
  useEffect(() => {
    setAnswer(alert.ai_summary ? alert : null);
    setNote(alert.case_note ? { text: alert.case_note, tools: [], cases: [] } : null);
    setError(null);
    setNoteError(null);
    // A stored note keeps the ids it cited. Their rows come from the server, as known at this alert.
    if (alert.case_note_cases?.length) {
      getCitedCases(id)
        .then((answer) => setNote((open) => (open ? { ...open, cases: answer.cases } : open)))
        .catch(() => {});
    }
  }, [id]);

  const run = async (refresh) => {
    setBusy(true);
    setError(null);
    try {
      setAnswer(await narrate(id, refresh));
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  // Ask once when an alert without a narrative is opened. The answer is stored, so the next look is free.
  useEffect(() => {
    if (enabled && !alert.ai_summary) run(false);
  }, [id, enabled]);

  const investigate = async (refresh) => {
    setNoteBusy(true);
    setNoteError(null);
    try {
      const result = await writeCaseNote(id, refresh);
      setNote({ text: result.alert.case_note, tools: result.tools, cases: result.cases ?? [] });
    } catch (failure) {
      setNoteError(failure.message);
    } finally {
      setNoteBusy(false);
    }
  };

  const suggestion = answer && SUGGESTION[answer.ai_suggestion];

  return (
    <div className="rounded-lg border border-series-1/30 bg-series-1/[0.06] p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-ink">
        <Icon name="spark" className="h-4 w-4 text-series-1" />
        AI analyst
        {suggestion && (
          <span className="ml-auto">
            <Pill tone={suggestion.tone}>
              {suggestion.label} · {answer.ai_confidence}
            </Pill>
          </span>
        )}
      </div>

      <div className="mt-2 space-y-2">
        {enabled === false && (
          <p className="text-sm text-ink-muted">
            The AI is off. The SHAP reason and the policy rule above still apply. To turn it on, set{" "}
            <code className="rounded bg-white/10 px-1">LLM_API_KEY</code> in <code className="rounded bg-white/10 px-1">.env</code>.
          </p>
        )}
        {busy && <Waiting>Reading the alert and the account…</Waiting>}
        {error && (
          <p className="text-sm text-serious">
            The AI did not answer: {error}. The SHAP reason and the policy rule above still apply.
          </p>
        )}
        {answer && !busy && (
          <>
            <p className="text-sm leading-relaxed text-ink-2">{answer.ai_summary}</p>
            {answer.ai_check && (
              <p className="text-sm text-ink-2">
                <span className="font-medium text-ink">Check first: </span>
                {answer.ai_check}
              </p>
            )}
            <p className="text-[11px] text-ink-muted">
              Advice only. Your decision is the label. {answer.ai_model} · prompt {answer.ai_prompt_version}
            </p>
          </>
        )}
      </div>

      {enabled && (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-white/10 pt-3">
          <button
            type="button"
            onClick={() => investigate(Boolean(note))}
            disabled={noteBusy}
            className="rounded-md border border-series-1/50 px-2.5 py-1 text-xs font-medium text-series-1 hover:bg-series-1/10 disabled:opacity-40"
          >
            {note ? "Rewrite case note" : "Write case note"}
          </button>
          {answer && (
            <button
              type="button"
              onClick={() => run(true)}
              disabled={busy}
              className="rounded-md border border-white/15 px-2.5 py-1 text-xs text-ink-2 hover:border-white/35 disabled:opacity-40"
            >
              Ask again
            </button>
          )}
        </div>
      )}

      {noteBusy && (
        <div className="mt-3">
          <Waiting>The agent is reading the alert, the account history and the policy…</Waiting>
        </div>
      )}
      {noteError && <p className="mt-3 text-sm text-serious">The case note failed: {noteError}</p>}
      {note && !noteBusy && (
        <div className="mt-3 rounded-md border border-white/10 bg-plane/60 p-3">
          <Markdown text={note.text} />
          <CitedCases cases={note.cases} onOpenAlert={onOpenAlert} />
          {note.tools.length > 0 && (
            <p className="mt-2 text-[11px] text-ink-muted">Read with: {[...new Set(note.tools.map((call) => call.tool))].join(", ")}</p>
          )}
        </div>
      )}
    </div>
  );
}

// What a past case's outcome looks like. Pending is not a verdict, so it gets no colour of one.
const OUTCOME = { ...ALERT_STATUS, pending: { label: "Pending", tone: "muted" } };
const SOURCE = { analyst: "analyst", label: "chargeback or dispute", simulated: "simulated, demo" };

/** The past cases the note names by id, with their outcome as known when this alert was raised. */
function CitedCases({ cases, onOpenAlert }) {
  if (!cases?.length) return null;
  return (
    <div className="mt-3 border-t border-white/10 pt-2">
      <p className="text-[11px] font-medium uppercase tracking-wide text-ink-muted">Past cases cited</p>
      <ul className="mt-1.5 space-y-1 text-xs">
        {cases.map((row) => {
          const outcome = OUTCOME[row.outcome] ?? OUTCOME.pending;
          return (
            <li key={row.alert_id} className="grid grid-cols-[3.5rem_5rem_minmax(0,1fr)_auto] items-center gap-2">
              <button
                type="button"
                onClick={() => onOpenAlert?.(row.alert_id)}
                className="tabular text-left text-series-1 underline-offset-2 hover:underline"
                title="Open this alert"
              >
                #{row.alert_id}
              </button>
              <span className="tabular text-right text-ink">{money(row.amount)}</span>
              <span className="truncate text-ink-2">
                {row.country} · {pretty(row.category)}
                {row.outcome_source ? ` · ${SOURCE[row.outcome_source] ?? row.outcome_source}` : ""}
              </span>
              <Pill tone={outcome.tone}>{outcome.label}</Pill>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The alerts whose payments look most like this one, and what analysts decided about them. */
export function SimilarAlerts({ alertId }) {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    setRows(null);
    getSimilar(alertId).then((answer) => setRows(answer.similar)).catch(() => setRows([]));
  }, [alertId]);

  if (!rows?.length) return null;
  const decided = rows.filter((row) => row.status !== "open");
  const confirmed = decided.filter((row) => row.status === "confirmed_fraud").length;

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-ink">
        Similar alerts
        <span className="ml-auto text-xs font-normal text-ink-muted">
          {decided.length ? `${confirmed} of ${decided.length} decided were fraud` : "none decided yet"}
        </span>
      </div>
      <ul className="mt-2 space-y-1 text-xs">
        {rows.map((row) => {
          const status = ALERT_STATUS[row.status] ?? ALERT_STATUS.open;
          return (
            <li key={row.alert_id} className="grid grid-cols-[3.5rem_5rem_minmax(0,1fr)_auto] items-center gap-2">
              <span className="tabular text-ink-muted">#{row.alert_id}</span>
              <span className="tabular text-right text-ink">{money(row.amount)}</span>
              <span className="truncate text-ink-2">
                {row.country} · {pretty(row.category)} · {row.channel}
              </span>
              <Pill tone={status.tone}>{status.label}</Pill>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
