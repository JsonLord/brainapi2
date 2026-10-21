import { clearSession, loadSession, saveSession, normalizeBaseUrl, type AuthSession } from "./auth";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export interface BrainRecord {
  id?: string;
  name_key: string;
}

export interface ApiFetchOptions {
  logoutOn401?: boolean;
}

export interface Workspace {
  id?: string;
  brain_id: string;
  slug: string;
  display_name: string;
  description?: string | null;
  archived: boolean;
  api_base_url: string;
  console_url: string;
}

export interface CreateWorkspaceInput {
  slug: string;
  display_name: string;
  description?: string | null;
}

export interface UpdateWorkspaceInput {
  display_name?: string;
  description?: string | null;
  archived?: boolean;
}

let session: AuthSession | null = null;

export function getSession(): AuthSession | null {
  return session;
}

export function setSession(next: AuthSession | null): void {
  session = next;
}

function authHeaders(brainId?: string | null, path?: string): Headers {
  const headers = new Headers();
  if (session?.csrfToken) headers.set("X-CSRF-Token", session.csrfToken);
  if (brainId && path && !path.startsWith("/system")) headers.set("X-Brain-ID", brainId);
  return headers;
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  overrideBrainId?: string,
  fetchOptions: ApiFetchOptions = {},
): Promise<T> {
  if (!session) {
    throw new ApiError(401, "Not authenticated");
  }

  const headers = authHeaders(overrideBrainId ?? session.brainId, path);
  for (const [key, value] of new Headers(options.headers).entries()) {
    headers.set(key, value);
  }
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const url = `${normalizeBaseUrl(session.apiBaseUrl)}${path}`;
  const response = await fetch(url, { ...options, headers, credentials: "include" });

  if (response.status === 401 && fetchOptions.logoutOn401 !== false) {
    clearSession();
    setSession(null);
    if (!window.location.pathname.endsWith("/login")) {
      window.location.href = "/console/login";
    }
    throw new ApiError(401, "Unauthorized");
  }

  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!response.ok) {
    const detail =
      typeof data === "object" && data && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `Request failed (${response.status})`;
    throw new ApiError(response.status, detail);
  }

  return data as T;
}

export function workspaceApiFetch<T>(
  workspace: Pick<Workspace, "brain_id">,
  path: string,
  options: RequestInit = {},
  fetchOptions: ApiFetchOptions = {},
): Promise<T> {
  return apiFetch<T>(path, options, workspace.brain_id, fetchOptions);
}

export async function fetchWorkspaces(): Promise<Workspace[]> {
  const result = await apiFetch<{ workspaces: Workspace[] }>(
    "/system/workspaces",
  );
  return result.workspaces;
}

export function fetchWorkspace(slug: string): Promise<Workspace> {
  return apiFetch<Workspace>(`/system/workspaces/${encodeURIComponent(slug)}`);
}

export function createWorkspace(
  input: CreateWorkspaceInput,
): Promise<Workspace> {
  return apiFetch<Workspace>("/system/workspaces", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateWorkspace(
  slug: string,
  input: UpdateWorkspaceInput,
): Promise<Workspace> {
  return apiFetch<Workspace>(
    `/system/workspaces/${encodeURIComponent(slug)}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
}

interface SessionIdentity {
  is_system: boolean;
  brain_id: string | null;
  csrf_token?: string;
}

export async function connectSession(apiBaseUrl: string, token: string): Promise<AuthSession> {
  const base = normalizeBaseUrl(apiBaseUrl);
  const response = await fetch(`${base}/auth/session`, {
    method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!response.ok) throw new ApiError(response.status, "Access token rejected");
  const identity: SessionIdentity = await response.json();
  session = { apiBaseUrl: base, brainId: identity.brain_id, isSystemPat: identity.is_system, csrfToken: identity.csrf_token };
  // The credential has served its purpose; all subsequent calls use the cookie.
  return session;
}

export async function restoreSession(): Promise<AuthSession | null> {
  const base = loadSession()?.apiBaseUrl ?? window.location.origin;
  const response = await fetch(`${normalizeBaseUrl(base)}/auth/me`, { credentials: "include" });
  if (!response.ok) { session = null; return null; }
  const identity: SessionIdentity = await response.json();
  session = { apiBaseUrl: base, brainId: identity.brain_id, isSystemPat: identity.is_system, csrfToken: identity.csrf_token };
  saveSession(session);
  return session;
}

export async function logoutSession(): Promise<void> {
  try { await apiFetch("/auth/logout", { method: "POST" }); }
  finally { clearSession(); session = null; }
}

export function mergeBrainOptions(
  brains: BrainRecord[],
  activeBrainId: string,
): BrainRecord[] {
  const byKey = new Map<string, BrainRecord>();
  for (const brain of brains) {
    byKey.set(brain.name_key, brain);
  }
  if (activeBrainId && !byKey.has(activeBrainId)) {
    byKey.set(activeBrainId, { name_key: activeBrainId });
  }
  return [...byKey.values()].sort((a, b) =>
    a.name_key.localeCompare(b.name_key),
  );
}
