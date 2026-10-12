const BASE = import.meta.env.VITE_API_URL ?? "";
const TOKEN_KEY = "accessToken";

/** The JWT lives in sessionStorage: it is cleared when the tab closes and is never sent anywhere but our API. */
export const auth = {
  get: (): string => { try { return sessionStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; } },
  set: (t: string) => { try { sessionStorage.setItem(TOKEN_KEY, t); } catch { /* storage blocked */ } },
  clear: () => { try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked */ } },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

interface Opts { method?: string; body?: unknown; form?: FormData }

export async function api<T>(path: string, o: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = auth.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (o.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(BASE + path, {
    method: o.method ?? (o.body !== undefined || o.form ? "POST" : "GET"),
    headers, body: o.form ?? (o.body !== undefined ? JSON.stringify(o.body) : undefined),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && token) {  // expired, revoked, or issued by another database
      auth.clear();
      window.dispatchEvent(new CustomEvent("auth-expired", { detail: typeof data.detail === "string" ? data.detail : "" }));
    }
    const detail = typeof data.detail === "string" ? data.detail : Array.isArray(data.detail) ? data.detail[0]?.msg : res.statusText;
    throw new ApiError(res.status, detail ?? "Request failed");
  }
  return data as T;
}
