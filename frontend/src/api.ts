import type { Session } from "./types";

const SESSION_KEY = "directdesk.session.v1";

export function loadSession(): Session | null {
  const stored = window.localStorage.getItem(SESSION_KEY);
  if (!stored) return null;
  try {
    return JSON.parse(stored) as Session;
  } catch {
    window.localStorage.removeItem(SESSION_KEY);
    return null;
  }
}

export function saveSession(session: Session | null): void {
  if (session) window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  else window.localStorage.removeItem(SESSION_KEY);
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  session: Session | null = loadSession()
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (session) headers.set("Authorization", `Bearer ${session.accessToken}`);
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (!response.ok) {
    if (response.status === 401 && session) {
      saveSession(null);
      window.dispatchEvent(new Event("directdesk:session-expired"));
    }
    const error = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(error?.detail ?? "요청을 처리하지 못했습니다.");
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}
