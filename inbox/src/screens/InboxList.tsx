import { useEffect, useState } from "react";
import { api, ApiError, type InboxItem } from "../api";
import { timeAgo } from "../format";

export function InboxList({ onSignOut }: { onSignOut: () => void }) {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <main>
      <header className="topbar">
        <h1>Waiting For You</h1>
        <button className="link" onClick={onSignOut}>
          Sign Out
        </button>
      </header>
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
              {item.agent_name ?? "agent"} · {timeAgo(item.created_at)}
            </div>
          </a>
        ))
      )}
    </main>
  );
}
