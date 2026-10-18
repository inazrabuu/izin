import { useCallback, useEffect, useState } from "react";
import { tokenStore } from "./api";
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
  const signOut = useCallback(() => {
    tokenStore.clear();
    setToken(null);
  }, []);

  if (!token) return <SignIn onSignedIn={setToken} />;

  const match = /^#\/requests\/([0-9a-f-]{36})$/.exec(hash);

  return match ? (
    <RequestDetail key={match[1]} id={match[1]} onSignOut={signOut} />
  ) : (
    <InboxList onSignOut={signOut} />
  );
}
