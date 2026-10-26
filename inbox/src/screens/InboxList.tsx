import { useEffect, useState } from "react";
import { api, ApiError, type InboxItem, type Me } from "../api";
import { timeAgo, timeUntil } from "../format";

export function InboxList({ onSignOut }: { onSignOut: () => void }) {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await api.list();
        if (!cancelled) {
          setItems(data);
          setError(null);
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) onSignOut();
        else if (!cancelled) setError("Couldn't load requests. Retrying...");
      }
    }
    load();
    const timer = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [onSignOut]);

  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch(() => {});
  }, []);

  async function toggleAway() {
    if (!me) return;
    try {
      const { available } = await api.setAvailability(!me.available);
      setMe({ ...me, available });
    } catch {
      setError("Couldn't update your availability.");
    }
  }

  return (
    <main>
      <header className="topbar">
        <h1>Waiting For You</h1>
        {me && (
          <button className="link" onClick={toggleAway}>
            {me.available ? "Set Away" : "I'm back"}
          </button>
        )}
        <button className="link" onClick={onSignOut}>
          Sign Out
        </button>
      </header>
      {me && !me.available && (
        <p className="meta">
          You're away. Requests skip your step when nobody else on it is
          available.
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {items === null ? (
        <p className="meta">Loading</p>
      ) : items.length === 0 ? (
        <p className="meta">Nothing is waiting for you.</p>
      ) : (
        items.map((item) => (
          <a key={item.id} className="card" href={`#/requests/${item.id}`}>
            <div className="meta">
              <span className={`badge risk-${item.risk_level}`}>
                {item.risk_level}
              </span>
            </div>
            <p className="item-heading">{item.headline}</p>
            <div className="meta">
              {item.agent_name ?? "agent"} · {timeAgo(item.created_at)} · due
              {timeUntil(item.due_at)}
            </div>
          </a>
        ))
      )}
    </main>
  );
}
