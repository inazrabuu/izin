import { Fragment, useCallback, useEffect, useState } from "react";
import { api, ApiError, type DetailClient, type InboxDetail } from "../api";
import { formatValue, timeAgo, truncateWords } from "../format";

const WHY_WORD_LIMIT = 40;

export function RequestDetail({
  id,
  onSignOut,
  client = api,
}: {
  id: string;
  onSignOut: () => void;
  client?: DetailClient;
}) {
  const [req, setReq] = useState<InboxDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [denying, setDenying] = useState(false);
  const [changing, setChanging] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [showFullWhy, setShowFullWhy] = useState(false);

  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) onSignOut();
      else setError(e instanceof Error ? e.message : "Something went wrong.");
    },
    [onSignOut],
  );

  const load = useCallback(async () => {
    try {
      setReq(await client.get(id));
    } catch (e) {
      fail(e);
    }
  }, [id, fail]);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(
    verdict: "approve" | "deny" | "modify",
    changes?: Record<string, unknown>,
  ) {
    setBusy(true);
    setError(null);
    try {
      setReq(
        await client.decide(
          id,
          verdict,
          verdict === "approve" ? undefined : reason,
          changes,
        ),
      );
      setDenying(false);
      setChanging(false);
    } catch (e) {
      fail(e);
      if (e instanceof ApiError && e.code === "already_decided") await load(); // show who did
    } finally {
      setBusy(false);
    }
  }

  if (!req) {
    return (
      <main>
        <a className="back" href="#/">
          ← Inbox
        </a>
        {error ? (
          <p className="error">{error}</p>
        ) : (
          <p className="meta">Loading…</p>
        )}
      </main>
    );
  }

  function startChanging() {
    setDraft(
      Object.fromEntries(req!.editable.map((f) => [f.field, String(f.value)])),
    );
    setReason("");
    setChanging(true);
  }

  const changes: Record<string, unknown> = {};
  for (const f of req.editable) {
    const raw = (draft[f.field] ?? "").trim();
    if (raw == "") continue;
    const value = f.type == "string" ? raw : Number(raw);
    if (value !== f.value) changes[f.field] = value;
  }
  const hasChanges = Object.keys(changes).length > 0;

  const why = req.rationale ?? "";
  const shortWhy = truncateWords(why, WHY_WORD_LIMIT);
  const contextEntries = Object.entries(req.context);

  return (
    <main>
      <a className="back" href="#/">
        ← Inbox
      </a>

      <article className="card">
        <div className="meta">
          <span className={`badge risk-${req.risk_level}`}>
            {req.risk_level}
          </span>{" "}
          {req.action_display_name} · {req.agent_name ?? "agent"} ·{" "}
          {timeAgo(req.created_at)}
        </div>

        {/* 1. The sentence. Large. No JSON. */}
        <p className="headline">{req.rendered.headline}</p>

        {/* 2. Why */}
        {why && (
          <section>
            <h2>Why</h2>
            <p>
              {showFullWhy ? why : shortWhy}{" "}
              {!showFullWhy && shortWhy !== why && (
                <button className="link" onClick={() => setShowFullWhy(true)}>
                  more
                </button>
              )}
            </p>
          </section>
        )}

        {/* 3. Blast radius */}
        {req.rendered.blast_radius && (
          <section>
            <h2>If you approve</h2>
            <p>{req.rendered.blast_radius}</p>
          </section>
        )}

        {/* 4. If you deny */}
        {req.rendered.if_denied && (
          <section>
            <h2>If you deny</h2>
            <p>{req.rendered.if_denied}</p>
          </section>
        )}

        {/* 5. Context */}
        <section>
          <h2>Context</h2>
          <p>
            {req.action_display_name} was approved {req.similar_approved_7d}{" "}
            {req.similar_approved_7d === 1 ? "time" : "times"} in the last 7
            days.
          </p>
          {contextEntries.length > 0 && (
            <>
              <p className="meta">From the agent (unverified):</p>
              <dl className="kv">
                {contextEntries.map(([key, value]) => (
                  <Fragment key={key}>
                    <dt>{key}</dt>
                    <dd>{formatValue(value)}</dd>
                  </Fragment>
                ))}
              </dl>
            </>
          )}
        </section>

        {/* 6. Raw args, collapsed */}
        <details>
          <summary className="meta">Raw arguments</summary>
          <pre>{JSON.stringify(req.args, null, 2)}</pre>
        </details>
      </article>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {req.decision ? (
        <p className="card outcome">
          {req.decision.verdict === "deny"
            ? "Denied"
            : req.decision.verdict === "modify"
              ? "Approved with changes"
              : "Approved"}{" "}
          by {req.decision.principal_name} · {timeAgo(req.decision.decided_at)}
          {req.decision.comment && (
            <span className="meta"> — “{req.decision.comment}”</span>
          )}
        </p>
      ) : req.link_can_decide === false ? (
        <p className="meta">
          This is a high-risk request. <a href="#/">Sign in to the inbox</a> to
          decide.
        </p>
      ) : changing ? (
        <div className="actions stacked">
          {req.editable.map((f) => (
            <label key={f.field} className="meta">
              {f.field}
              {f.only && ` (may only ${f.only})`}
              <input
                type={f.type === "string" ? "text" : "number"}
                value={draft[f.field] ?? ""}
                onChange={(e) =>
                  setDraft({ ...draft, [f.field]: e.target.value })
                }
              />
            </label>
          ))}
          <label htmlFor="change-reason" className="meta">
            Why are you changing this? The agent receives your reason.
          </label>
          <textarea
            id="change-reason"
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="row">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setChanging(false)}
            >
              Cancel
            </button>
            <button
              disabled={busy || !hasChanges || !reason.trim()}
              onClick={() => decide("modify", changes)}
            >
              Approve with changes
            </button>
          </div>
        </div>
      ) : denying ? (
        <div className="actions stacked">
          <label htmlFor="reason" className="meta">
            Why are you denying this? The agent receives your reason.
          </label>
          <textarea
            id="reason"
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
          <div className="row">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setDenying(false)}
            >
              Cancel
            </button>
            <button
              className="deny"
              disabled={busy || !reason.trim()}
              onClick={() => decide("deny")}
            >
              Confirm deny
            </button>
          </div>
        </div>
      ) : (
        <div className="actions">
          <button
            className="deny"
            disabled={busy}
            onClick={() => setDenying(true)}
          >
            Deny
          </button>
          {req.editable.length > 0 && (
            <button
              className="secondary"
              disabled={busy}
              onClick={startChanging}
            >
              Change…
            </button>
          )}
          <button
            className="approve"
            disabled={busy}
            onClick={() => decide("approve")}
          >
            Approve
          </button>
        </div>
      )}
    </main>
  );
}
