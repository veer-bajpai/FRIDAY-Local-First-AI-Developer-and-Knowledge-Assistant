/** Typed client for the FRIDAY FastAPI backend. */

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Source = {
  title: string;
  chunk: number;
  page?: number | null;
  score?: number;
  method?: string;
  file_type?: string;
  document_id?: string;
};
export type Memory = {
  id: string;
  content: string;
  kind: string;
  source?: string | null;
  created_at: string;
  updated_at: string;
  score?: number;
};
export type Step = { label: string; state: "live" | "done" | "error" };
export type Message = {
  role: "user" | "assistant";
  content: string;
  created_at?: string;
  sources?: Source[];
  memories?: Memory[];
  note?: string;
  model?: string | null;
  steps?: Step[];
  elapsedMs?: number;
  failed?: boolean;
};
export type Document = {
  id: string;
  name: string;
  type: string;
  size: number;
  chunks: number;
  status: string;
  error?: string | null;
};
export type Conversation = {
  id: string;
  title: string;
  updated_at: string;
  created_at?: string;
};
export type ActivityEvent = {
  id: number;
  type: string;
  title: string;
  detail?: string;
  status: string;
  created_at: string;
};
export type Collection = { id: string; name: string; document_count: number };
export type SettingsData = {
  model: string;
  embed_model: string;
  top_k: number;
  max_distance: number;
  temperature: number;
  system_prompt: string;
};
export type AgentSessionSummary = {
  id: string;
  full_id?: string;
  workspace: string;
  busy: boolean;
  mode: string;
  status?: string;
  title?: string;
  messages?: number;
};
type Check = { status: string; [key: string]: unknown };
export type Health = {
  status: "ok" | "degraded" | "error";
  checks: {
    database?: Check & { documents?: number };
    ollama?: Check & {
      models?: number;
      chat_model?: { name?: string; installed?: boolean };
      embed_model?: { name?: string; installed?: boolean };
      error?: string;
    };
    agent?: Check & { sessions?: number; busy?: number };
    extraction?: Check & { ocr?: boolean; vision_model?: string | null };
  };
};
export type SearchResult = {
  document_id: string;
  name: string;
  title?: string;
  chunk: number;
  page?: number | null;
  content: string;
  score: number;
  method?: string;
};

export const SUPPORTED_UPLOAD_PATTERN =
  /\.(pdf|md|markdown|txt|rst|log|csv|tsv|json|jsonl|html?|xml|ya?ml|toml|ini|cfg|docx|pptx|xlsx|py|js|jsx|ts|tsx|java|kt|go|rs|c|h|cpp|hpp|cs|rb|php|swift|sql|sh|ps1|css|scss|png|jpe?g|gif|webp|bmp|tiff?)$/i;
export const UPLOAD_ACCEPT =
  ".pdf,.md,.markdown,.txt,.csv,.tsv,.json,.html,.htm,.docx,.pptx,.xlsx,.py,.js,.ts,.tsx,.jsx,.java,.go,.rs,.c,.cpp,.cs,.rb,.php,.sql,.sh,.ps1,.css,.yaml,.yml,.toml,.png,.jpg,.jpeg,.gif,.webp,.bmp,.tif,.tiff";

/** Turn a failed response into the backend's human-readable message. */
export async function errorMessage(response: Response, fallback = "Request failed") {
  const text = await response.text().catch(() => "");
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (Array.isArray(parsed.detail))
      return "The request was not valid. Check the values and try again.";
  } catch {
    /* not JSON */
  }
  if (response.status === 429) return "Too many requests. Wait a moment and try again.";
  if (response.status === 401) return "The FRIDAY API needs an access token for this connection.";
  return text || `${fallback} (${response.status})`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {});
  const hasBody = init?.body !== undefined && !(init.body instanceof FormData);
  if (hasBody && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API}${path}`, { ...init, headers });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export function formatSize(bytes: number) {
  if (!bytes) return "0 KB";
  return bytes > 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function timeAgo(iso?: string) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return "now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  if (seconds < 172800) return "Yesterday";
  return new Date(iso).toLocaleDateString("en-US", { day: "numeric", month: "short" });
}

export function clock(ms: number) {
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** Counts per day for the last `days` days (oldest first). */
export function perDay(dates: string[], days = 14) {
  const buckets = new Array<number>(days).fill(0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  for (const value of dates) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) continue;
    date.setHours(0, 0, 0, 0);
    const diff = Math.round((today.getTime() - date.getTime()) / 86400000);
    if (diff >= 0 && diff < days) buckets[days - 1 - diff] += 1;
  }
  return buckets;
}

