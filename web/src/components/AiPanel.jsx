import { useEffect, useState } from "react";
import { Pill } from "./Chrome.jsx";
import { Icon } from "./Icons.jsx";
import Markdown from "./Markdown.jsx";
import {
  ALERT_STATUS,
  SUGGESTION,
  getAi,
  getCaseNote,
  getPolicy,
  getSimilar,
  money,
  narrate,
  pretty,
  reviewCaseNote,
  saveManualNote,
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

  const id = alert.alert_id;

  useEffect(() => {
    aiStatus().then((status) => setEnabled(status.enabled)).catch(() => setEnabled(false));
  }, []);

  // A new alert starts clean. The list refreshes every 10 seconds with a new object for the same
  // alert, so this keys on the id and not on the object.
  useEffect(() => {
    setAnswer(alert.ai_summary ? alert : null);
    setError(null);
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

      {enabled && answer && (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-white/10 pt-3">
          <button
            type="button"
            onClick={() => run(true)}
            disabled={busy}
            className="rounded-md border border-white/15 px-2.5 py-1 text-xs text-ink-2 hover:border-white/35 disabled:opacity-40"
          >
            Ask again
          </button>
        </div>
      )}

      {enabled && <CaseNote alertId={id} onOpenAlert={onOpenAlert} />}
    </div>
  );
}

const when = (iso) =>
  new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

const BUTTON = "rounded-md border px-2.5 py-1 text-xs font-medium disabled:opacity-40";

/**
 * The case note workflow. The agent drafts, the run waits, and the analyst approves the draft or
 * returns it with a comment. A draft is not a case note until it is approved. After 3 returns the
 * analyst writes the note by hand, starting from the last draft.
 */
function CaseNote({ alertId, onOpenAlert }) {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [comment, setComment] = useState("");
  const [returning, setReturning] = useState(false);
  const [manual, setManual] = useState("");

  // Read only. Opening an alert never starts a paid run.
  useEffect(() => {
    setState(null);
    setError(null);
    setComment("");
    setReturning(false);
    getCaseNote(alertId)
      .then((answer) => {
        setState(answer);
        setManual(answer.draft ?? "");
      })
      .catch((failure) => setError(failure.message));
  }, [alertId]);

  const act = async (label, request) => {
    setBusy(label);
    setError(null);
    try {
      const answer = await request();
      setState(answer);
      setManual(answer.draft ?? "");
      setComment("");
      setReturning(false);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(null);
    }
  };

  const start = (refresh) => act("drafting", () => writeCaseNote(alertId, refresh));
  const approve = () => act("saving", () => reviewCaseNote(alertId, "approve", null));
  const sendBack = () => act("drafting", () => reviewCaseNote(alertId, "return", comment.trim()));
  const saveByHand = () => act("saving", () => saveManualNote(alertId, manual.trim()));

  const status = state?.status;
  const note = state?.alert;
  const badge = { draft: ["warning", "Draft · not saved"], approved: ["good", "Approved"], manual: ["serious", "Write by hand"] }[status];

  return (
    <div className="mt-3 border-t border-white/10 pt-3">
      <div className="flex items-center gap-2 text-sm font-medium text-ink">
        Case note
        {badge && (
          <span className="ml-auto">
            <Pill tone={badge[0]}>{badge[1]}</Pill>
          </span>
        )}
      </div>

      {busy && (
        <div className="mt-2">
          <Waiting>{busy === "saving" ? "Saving the note…" : "The agent is reading the evidence and writing a draft…"}</Waiting>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-serious">The case note failed: {error}</p>}

      {!busy && status === "none" && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => start(false)} className={`${BUTTON} border-series-1/50 text-series-1 hover:bg-series-1/10`}>
            Write case note
          </button>
          <span className="text-xs text-ink-muted">The agent drafts it. Nothing is saved until you approve.</span>
        </div>
      )}

      {!busy && status === "draft" && (
        <div className="mt-2 rounded-md border border-dashed border-warning/40 bg-plane/60 p-3">
          <Markdown text={state.draft} />
          <CitedCases cases={state.cases} onOpenAlert={onOpenAlert} />
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
            <button type="button" onClick={approve} className={`${BUTTON} border-good/50 text-good hover:bg-good/10`}>
              Approve
            </button>
            <button
              type="button"
              onClick={() => setReturning((open) => !open)}
              aria-expanded={returning}
              className={`${BUTTON} border-white/20 text-ink hover:border-white/40`}
            >
              Return with comment
            </button>
            <span className="text-xs text-ink-muted">
              {state.returns_left} {state.returns_left === 1 ? "return" : "returns"} left
            </span>
          </div>
          {returning && (
            <div className="mt-2 space-y-2">
              <label htmlFor={`return-${alertId}`} className="block text-xs text-ink-2">
                What should the agent change?
              </label>
              <textarea
                id={`return-${alertId}`}
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                rows={2}
                maxLength={2000}
                placeholder="For example: name the payment hour, and leave out the open alerts."
                className="w-full rounded-md border border-white/15 bg-plane px-2 py-1.5 text-sm text-ink placeholder:text-ink-muted"
              />
              <button
                type="button"
                onClick={sendBack}
                disabled={!comment.trim()}
                className={`${BUTTON} border-white/20 text-ink hover:border-white/40`}
              >
                Send back for a new draft
              </button>
            </div>
          )}
        </div>
      )}

      {!busy && status === "manual" && (
        <div className="mt-2 space-y-2 rounded-md border border-serious/40 bg-plane/60 p-3">
          <p className="text-sm text-ink-2">The draft was returned 3 times. Write the note yourself. The last draft is below to start from.</p>
          <label htmlFor={`manual-${alertId}`} className="sr-only">
            Case note
          </label>
          <textarea
            id={`manual-${alertId}`}
            value={manual}
            onChange={(event) => setManual(event.target.value)}
            rows={10}
            maxLength={5000}
            className="w-full rounded-md border border-white/15 bg-plane px-2 py-1.5 font-mono text-xs text-ink"
          />
          <button type="button" onClick={saveByHand} disabled={!manual.trim()} className={`${BUTTON} border-good/50 text-good hover:bg-good/10`}>
            Save note
          </button>
        </div>
      )}

      {!busy && status === "approved" && note && (
        <div className="mt-2 rounded-md border border-white/10 bg-plane/60 p-3">
          <Markdown text={note.case_note} />
          <CitedCases cases={state.cases} onOpenAlert={onOpenAlert} />
          <p className="mt-2 text-[11px] text-ink-muted">
            {note.case_note_model ? "Drafted by the agent and approved" : "Written by hand"}
            {note.case_note_by ? ` by ${note.case_note_by}` : " (no login yet, so no name)"}
            {note.case_note_at ? `, ${when(note.case_note_at)}` : ""}.
          </p>
          <button type="button" onClick={() => start(true)} className={`mt-2 ${BUTTON} border-series-1/50 text-series-1 hover:bg-series-1/10`}>
            Draft a new note
          </button>
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
