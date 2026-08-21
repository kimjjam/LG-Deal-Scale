import type { Session } from "./types";

const SESSION_KEY = "directdesk.session.v1";
const API_BASE_URL = import.meta.env.VITE_API_URL?.replace(/\/$/, "") ?? "";

function requestHeaders(options: RequestInit, session: Session | null): Headers {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (session) headers.set("Authorization", `Bearer ${session.accessToken}`);
  return headers;
}

async function request(path: string, options: RequestInit, session: Session | null): Promise<Response> {
  const response = await fetch(`${API_BASE_URL}/api${path}`, {
    ...options,
    headers: requestHeaders(options, session)
  });
  if (!response.ok) {
    if (response.status === 401 && session) {
      saveSession(null);
      window.dispatchEvent(new Event("directdesk:session-expired"));
    }
    const error = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(detailMessage(error?.detail));
  }
  return response;
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (!item || typeof item !== "object" || !("msg" in item) || typeof item.msg !== "string") return [];
      const location = "loc" in item && Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
      return [`${location ? `${location}: ` : ""}${item.msg}`];
    });
    if (messages.length) return messages.join("\n");
  }
  return "요청을 처리하지 못했습니다.";
}

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

export function sessionStaffId(session: Session): string | null {
  try {
    const payload = JSON.parse(atob(session.accessToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))) as { sub?: unknown };
    return typeof payload.sub === "string" ? payload.sub : null;
  } catch {
    return null;
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  session: Session | null = loadSession()
): Promise<T> {
  const response = await request(path, options, session);
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export async function downloadCsv(path: string, filename: string, session: Session): Promise<void> {
  const response = await request(path, {}, session);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
