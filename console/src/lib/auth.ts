export interface AuthSession {
  apiBaseUrl: string;
  brainId: string | null;
  isSystemPat: boolean;
  csrfToken?: string;
}

const STORAGE_KEY = "brainapi-console-preferences";
// Remove old credential-bearing storage on upgrade, without reading its value.
sessionStorage.removeItem("brainapi-console-session");
localStorage.removeItem("brainapi-console-session");

export function loadSession(): AuthSession | null {
  try {
    const preferences = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    return preferences ? { apiBaseUrl: preferences.apiBaseUrl, brainId: null, isSystemPat: false } : null;
  } catch { return null; }
}

export function saveSession(session: AuthSession): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ apiBaseUrl: session.apiBaseUrl }));
}

export function clearSession(): void {
  sessionStorage.removeItem("brainapi-console-session");
}

export function normalizeBaseUrl(url: string): string { return url.replace(/\/+$/, ""); }
