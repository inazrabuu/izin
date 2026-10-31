import { useCallback, useEffect, useMemo, useState } from "react";
import { linkClient, tokenStore } from "./api";
import { InboxList } from "./screens/InboxList";
import { RequestDetail } from "./screens/RequestDetail";
import { SignIn } from "./screens/SignIn";

function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => tokenStore.get());
  const hash = useHashRoute();
  const linkToken = /^#\/link\/([\w.-]+)$/.exec(hash)?.[1] ?? null;
  const client = useMemo(
    () => (linkToken ? linkClient(linkToken) : null),
    [linkToken],
  );

  const signOut = useCallback(() => {
    tokenStore.clear();
    setToken(null);
  }, []);

  const match = /^#\/requests\/([0-9a-f-]{36})$/.exec(hash);

  if (client) {
    return (
      <RequestDetail
        key={linkToken!}
        id="link"
        client={client}
        onSignOut={() => {
          window.location.hash = "#/";
        }}
      />
    );
  }
  if (!token) return <SignIn onSignedIn={setToken} />;

  return match ? (
    <RequestDetail key={match[1]} id={match[1]} onSignOut={signOut} />
  ) : (
    <InboxList onSignOut={signOut} />
  );
}
