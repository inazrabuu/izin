import { useState, type FormEvent } from "react";
import { api, tokenStore } from "../api";

export function SignIn({
  onSignedIn,
}: {
  onSignedIn: (token: string) => void;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const token = value.trim();
    setBusy(true);
    setError(null);
    tokenStore.set(token);

    try {
      await api.me();
      onSignedIn(token);
    } catch {
      tokenStore.clear();
      setError(
        "That token wasn't recognised. Ask your izin admin for a new one.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>Izin</h1>
      <form className="card" onSubmit={submit}>
        <label htmlFor="token" className="meta">
          Your inbox token
        </label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          <button className="approve" disabled={busy || !value.trim()}>
            Sign In
          </button>
        </div>
      </form>
    </main>
  );
}
