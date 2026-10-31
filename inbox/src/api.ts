export type RiskLevel = "low" | "medium" | "high" | "critical";

export interface InboxItem {
  id: string;
  action_display_name: string;
  risk_level: RiskLevel;
  headline: string;
  agent_name: string | null;
  created_at: string;
  expires_at: string | null;
  due_at: string;
}

export interface Decision {
  verdict: "approve" | "deny" | "modify";
  comment: string | null;
  principal_name: string;
  decided_at: string;
  args: Record<string, unknown>;
  changes: Record<string, { from: unknown; to: unknown }> | null;
  modified_screen: { headline: string } | null;
}

export interface InboxDetail {
  id: string;
  status: string;
  action_display_name: string;
  risk_level: RiskLevel;
  agent_name: string | null;
  rationale: string | null;
  rendered: {
    headline: string;
    blast_radius: string | null;
    if_denied: string | null;
  };
  context: Record<string, unknown>;
  args: Record<string, unknown>;
  created_at: string;
  expires_at: string | null;
  similar_approved_7d: number;
  decision: Decision | null;
  editable: Editable[];
  link_can_decide?: boolean | null;
}

export interface Me {
  id: string;
  name: string;
  roles: string[];
  available: boolean;
}

export interface Editable {
  field: string;
  type: "number" | "integer" | "string";
  only: "decrease" | "increase" | null;
  value: unknown;
}

const TOKEN_KEY = "izin.token";

export const tokenStore = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (token: string): void => localStorage.setItem(TOKEN_KEY, token),
  clear: (): void => localStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function send<T>(
  url: string,
  auth: Record<string, string>,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...auth,
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message =
      body?.error?.message ??
      (Array.isArray(body?.detail) ? body.detail[0]?.msg : undefined) ??
      response.statusText;
    throw new ApiError(
      response.status,
      body?.error?.code ?? "http_error",
      message,
    );
  }

  return (await response.json()) as T;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = tokenStore.get();
  return send<T>(
    `/v1/inbox${path}`,
    token ? { Authorization: `Bearer ${token}` } : {},
    init,
  );
}

export type DetailClient = Pick<typeof api, "get" | "decide">;

export function linkClient(token: string): DetailClient {
  const auth = { "Izin-Link": token };
  return {
    get: () => send<InboxDetail>("/v1/link", auth),
    decide: (_id, verdict, comment, changes) =>
      send<InboxDetail>("/v1/link/decision", auth, {
        method: "POST",
        body: JSON.stringify({
          verdict,
          comment: comment?.trim() || null,
          changes: changes ?? null,
        }),
      }),
  };
}

export const api = {
  me: () => call<Me>("/me"),
  list: () => call<InboxItem[]>("/requests"),
  get: (id: string) => call<InboxDetail>(`/requests/${id}`),
  decide: (
    id: string,
    verdict: "approve" | "deny" | "modify",
    comment?: string,
    changes?: Record<string, unknown>,
  ) =>
    call<InboxDetail>(`/requests/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({
        verdict,
        comment: comment?.trim() || null,
        changes: changes ?? null,
      }),
    }),
  setAvailability: (available: boolean) =>
    call<{ available: boolean }>("/me/availability", {
      method: "PUT",
      body: JSON.stringify({ available }),
    }),
};
