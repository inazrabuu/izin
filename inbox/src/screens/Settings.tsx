import { useEffect, useState } from "react";
import { api, ApiError, type Me } from "../api";

const E164 = /^\+[1-9]\d{6,14}$/;

export function Settings({ onSignOut }: { onSignOut: () => void }) {
  const [me, setMe] = useState<Me | null>(null);
  const [phone, setPhone] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .me()
      .then((m) => {
        setMe(m);
        setPhone(m.phone ?? "");
      })
      .catch(() => setError("Couldn't load your settings."));
  }, []);

  async function save(action: () => Promise<Me>) {
    setBusy(true);
    setError(null);
    try {
      setMe(await action());
      setAgreed(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (!me)
    return (
      <main className="screen">
        <p className="meta">{error ?? "Loading…"}</p>
      </main>
    );
  const optedIn = me.whatsapp_opted_in_at !== null;

  return (
    <main className="screen">
      <header className="bar">
        <a href="#/">← Inbox</a>
        <button className="link" onClick={onSignOut}>
          Sign Out
        </button>
      </header>
      <h1>WhatsApp notifications</h1>
      {optedIn ? (
        <>
          <p>
            You receive approval requests on WhatsApp at{" "}
            <strong>{me.phone}</strong>.
          </p>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => save(api.optOutWhatsApp)}
          >
            Stop WhatsApp notifications
          </button>
        </>
      ) : (
        <>
          <p>
            When a request needs you, izin can send a WhatsApp message with the
            action, its risk level and the deadline, plus a button that opens it
            here.
          </p>
          <p className="meta">
            Customer names and amounts are never sent to WhatsApp. Messages are
            delivered by Meta's WhatsApp service. You can stop at any time on
            this page.
          </p>
          <label htmlFor="phone" className="meta">
            Phone number, with country code
          </label>
          <input
            id="phone"
            inputMode="tel"
            placeholder="+62812…"
            value={phone}
            onChange={(e) => setPhone(e.target.value.replace(/[\s-]/g, ""))}
          />
          <label className="meta">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
            />{" "}
            I agree to receive approval requests on WhatsApp at this number.
          </label>
          <button
            disabled={busy || !agreed || !E164.test(phone)}
            onClick={() => save(() => api.optInWhatsApp(phone))}
          >
            Turn on WhatsApp
          </button>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </main>
  );
}
