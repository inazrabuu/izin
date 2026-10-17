export type RiskLevel = "low" | "medium" | "high" | "critical";

export interface InboxItem {
  id: string;
  action_display_name: string;
  risk_level: RiskLevel;
  headline: string;
  agent_name: string | null;
  created_at: string;
  expires_at: string | null;
}

export interface Decision {
  verdict: "approve" | "deny" | "modify";
  comment: string | null;
  principal_name: string;
  decided_at: string;
  args: Record<string, unknown>;
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
}

export interface Me {
  id: string;
  name: string;
  roles: string[];
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

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = tokenStore.get();
  const response = await fetch(`/v1/inbox${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
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

export const api = {
  me: () => call<Me>("/me"),
  list: () => call<InboxItem[]>("/requests"),
  get: (id: string) => call<InboxDetail>(`/requests/${id}`),
  decide: (id: string, verdict: "approve" | "deny", comment?: string) =>
    call<InboxDetail>(`/requests/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ verdict, comment: comment?.trim() || null }),
    }),
};
