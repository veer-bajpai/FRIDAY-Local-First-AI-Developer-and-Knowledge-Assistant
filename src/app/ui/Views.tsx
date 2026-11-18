"use client";

import { useState, type ReactNode } from "react";
import {
  Activity,
  Brain,
  Grid2X2,
  MessageSquare,
  Monitor,
  Moon,
  Plus,
  RefreshCw,
  Search,
  Sun,
  Trash2,
  Upload,
} from "lucide-react";
import { docKind } from "./ChatView";
import {
  api,
  formatSize,
  timeAgo,
  type ActivityEvent,
  type Collection,
  type Conversation,
  type Document,
  type Memory,
  type SearchResult,
  type SettingsData,
} from "./api";
import type { ThemeMode } from "./theme";

export function Empty({
  icon,
  title,
  text,
  action,
}: {
  icon: ReactNode;
  title: string;
  text: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}

/* ---------------------------------------------------------------- documents */

export function DocumentsView({
  documents,
  onUpload,
  onChanged,
  onError,
}: {
  documents: Document[];
  onUpload: () => void;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [query, setQuery] = useState("");
  const shown = documents.filter((document) => document.name.toLowerCase().includes(query.trim().toLowerCase()));

  async function act(path: string, method: string) {
    try {
      await api(path, { method });
      onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Request failed");
    }
  }

  return (
    <div className="page">
      <div className="toolbar">
        <label className="field grow">
          <Search size={15} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter documents" aria-label="Filter documents" />
        </label>
        <button type="button" className="btn primary" onClick={onUpload}>
          <Upload size={15} /> Add files
        </button>
      </div>
      {shown.length ? (
        <div className="card list-card">
          <div className="rows">
            {shown.map((document) => {
              const kind = docKind(document.name, document.type);
              return (
                <div className="row" key={document.id}>
                  <span className={`doc-icon ${kind.cls}`}>{kind.label}</span>
                  <span className="grow">
                    <b>{document.name}</b>
                    <small>
                      {formatSize(document.size)} · {document.chunks} chunk{document.chunks === 1 ? "" : "s"}
                    </small>
                    {document.error && (
                      <span className={document.status === "failed" ? "err-text" : "warn-text"}>{document.error}</span>
                    )}
                  </span>
                  <span className={`tag ${document.status}`}>{document.status}</span>
                  <span className="list-actions">
                    <button type="button" className="icon-btn ghost sm" title="Re-read this file (e.g. after installing OCR)"
                      aria-label={`Reprocess ${document.name}`} onClick={() => void act(`/api/documents/${document.id}/reprocess`, "POST")}>
                      <RefreshCw size={14} />
                    </button>
                    <button type="button" className="icon-btn ghost sm" title="Delete" aria-label={`Delete ${document.name}`}
                      onClick={() => {
                        if (window.confirm(`Delete ${document.name}? Its indexed text is removed too.`))
                          void act(`/api/documents/${document.id}`, "DELETE");
                      }}>
                      <Trash2 size={14} />
                    </button>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="card">
          <Empty icon={<Upload size={20} />} title={query ? "No matching documents" : "Your knowledge base is empty"}
            text="Add PDFs, Word, PowerPoint, Excel, Markdown, code or images. FRIDAY indexes them locally and cites them in answers."
            action={<button type="button" className="btn accent" onClick={onUpload}><Plus size={15} /> Add files</button>} />
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- search */

export function SearchView({ initialQuery = "" }: { initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  async function run(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) return;
    setRunning(true);
    setError("");
    try {
      const response = await api<{ results: SearchResult[] }>("/api/search", {
        method: "POST",
        body: JSON.stringify({ query: query.trim(), k: 12 }),
      });
      setResults(response.results);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Search failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page">
      <form className="toolbar" onSubmit={run}>
        <label className="field grow">
          <Search size={15} />
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)}
            placeholder="Search everything you've indexed" aria-label="Search indexed documents" />
          <kbd>Enter</kbd>
        </label>
        <button type="submit" className="btn primary" disabled={running || !query.trim()}>
          {running ? "Searching…" : "Search"}
        </button>
      </form>
      {error && <div className="banner" style={{ margin: 0 }}><span>{error}</span></div>}
      {results === null && !running && (
        <div className="card">
          <Empty icon={<Search size={20} />} title="Search your knowledge"
            text="Keyword (BM25) and semantic matches are combined, then ranked. Each result shows the file, page and how well it matched." />
        </div>
      )}
      {results?.length === 0 && (
        <div className="card">
          <Empty icon={<Search size={20} />} title="No matches" text="Try different words, or add more documents." />
        </div>
      )}
      {results?.map((result) => {
        const title = result.title ?? result.name;
        const kind = docKind(title);
        return (
          <article className="card result" key={`${result.document_id}-${result.chunk}`}>
            <div className="result-head">
              <span className={`doc-icon ${kind.cls}`}>{kind.label}</span>
              <div>
                <b>{title}</b>
                <small>
                  {result.page ? `Page ${result.page} · ` : ""}chunk {result.chunk + 1}
                  {result.method ? ` · ${result.method}` : ""}
                </small>
              </div>
              <span className="score">
                {Math.round(result.score * 100)}%
                <span className="meter"><i style={{ width: `${Math.round(result.score * 100)}%` }} /></span>
              </span>
            </div>
            <p>{result.content.length > 520 ? `${result.content.slice(0, 520)}…` : result.content}</p>
          </article>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- collections */

export function CollectionsView({
  collections,
  onChanged,
  onError,
}: {
  collections: Collection[];
  onChanged: (collections: Collection[]) => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return;
    try {
      const collection = await api<Collection>("/api/collections", {
        method: "POST",
        body: JSON.stringify({ title: name.trim() }),
      });
      onChanged([...collections, collection]);
      setName("");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not create the collection");
    }
  }
  return (
    <div className="page">
      <form className="toolbar" onSubmit={create}>
        <label className="field grow">
          <Grid2X2 size={15} />
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="New collection name" aria-label="Collection name" />
        </label>
        <button type="submit" className="btn primary" disabled={!name.trim()}>
          <Plus size={15} /> Create
        </button>
      </form>
      {collections.length ? (
        <div className="card list-card">
          <div className="rows">
            {collections.map((collection) => (
              <div className="row" key={collection.id}>
                <span className="doc-icon office"><Grid2X2 size={15} /></span>
                <span className="grow">
                  <b>{collection.name}</b>
                  <small>{collection.document_count} document{collection.document_count === 1 ? "" : "s"}</small>
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="card">
          <Empty icon={<Grid2X2 size={20} />} title="No collections yet"
            text="Group related documents so searches and answers can focus on one topic." />
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- memory */

export function MemoryView({
  memories,
  onChanged,
  onError,
}: {
  memories: Memory[];
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("fact");

  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!text.trim()) return;
    try {
      await api("/api/memories", { method: "POST", body: JSON.stringify({ content: text.trim(), kind }) });
      setText("");
      onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not save the memory");
    }
  }

  async function remove(id: string) {
    try {
      await api(`/api/memories/${id}`, { method: "DELETE" });
      onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not delete the memory");
    }
  }

  return (
    <div className="page">
      <form className="toolbar" onSubmit={add}>
        <label className="field grow">
          <Brain size={15} />
          <input value={text} onChange={(event) => setText(event.target.value)}
            placeholder="Something FRIDAY should remember, e.g. I prefer short answers" aria-label="New memory" maxLength={2000} />
        </label>
        <select className="btn" value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Memory type">
          <option value="fact">Fact</option>
          <option value="preference">Preference</option>
          <option value="project">Project</option>
          <option value="instruction">Instruction</option>
        </select>
        <button type="submit" className="btn primary" disabled={!text.trim()}>
          <Plus size={15} /> Save
        </button>
      </form>
      {memories.length ? (
        <div className="card list-card">
          <div className="rows">
            {memories.map((memory) => (
              <div className="row" key={memory.id}>
                <span className="doc-icon office"><Brain size={15} /></span>
                <span className="grow">
                  <b style={{ whiteSpace: "normal" }}>{memory.content}</b>
                  <small>
                    {memory.kind} · saved {timeAgo(memory.created_at)}
                    {memory.source?.startsWith("conversation") ? " from chat" : ""}
                  </small>
                </span>
                <button type="button" className="icon-btn ghost sm" aria-label="Delete memory" title="Delete"
                  onClick={() => void remove(memory.id)}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="card">
          <Empty icon={<Brain size={20} />} title="No memories yet"
            text="Add one here, or type “remember: …” in chat. Relevant memories are added to answers automatically; instructions always apply." />
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- activity & history */

export function ActivityView({ events }: { events: ActivityEvent[] }) {
  return (
    <div className="page">
      {events.length ? (
        <div className="card list-card">
          <div className="rows">
            {events.map((event) => (
              <div className="row" key={event.id}>
                <span className={`dot ${event.status === "error" ? "error" : event.status === "processing" ? "warn" : "ok"}`} />
                <span className="grow">
                  <b>{event.title}</b>
                  <small>{event.detail || event.type}</small>
                </span>
                <span className="tag">{event.type}</span>
                <time>{timeAgo(event.created_at)}</time>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="card">
          <Empty icon={<Activity size={20} />} title="No activity yet" text="Uploads, chats, settings changes and memories appear here." />
        </div>
      )}
    </div>
  );
}

export function HistoryView({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: {
  conversations: Conversation[];
  activeId?: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const shown = conversations.filter((c) => c.title.toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <div className="page">
      <div className="toolbar">
        <label className="field grow">
          <Search size={15} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search conversations" aria-label="Search conversations" />
        </label>
        <button type="button" className="btn primary" onClick={onNew}>
          <Plus size={15} /> New chat
        </button>
      </div>
      {shown.length ? (
        <div className="card list-card">
          <div className="rows">
            {shown.map((conversation) => (
              <div className="row" key={conversation.id}>
                <MessageSquare size={16} />
                <button type="button" className="grow" style={{ border: 0, background: "none", textAlign: "left", padding: 0 }}
                  onClick={() => onSelect(conversation.id)}>
                  <b style={conversation.id === activeId ? { color: "var(--accent-text)" } : undefined}>{conversation.title}</b>
                  <small>Updated {timeAgo(conversation.updated_at)}</small>
                </button>
                <button type="button" className="icon-btn ghost sm" aria-label={`Delete ${conversation.title}`} title="Delete"
                  onClick={() => {
                    if (window.confirm(`Delete “${conversation.title}”?`)) onDelete(conversation.id);
                  }}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="card">
          <Empty icon={<MessageSquare size={20} />} title={query ? "No matches" : "No conversations yet"}
            text="Your chats are saved locally and listed here."
            action={<button type="button" className="btn accent" onClick={onNew}><Plus size={15} /> Start a chat</button>} />
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- settings */

const THEME_CHOICES: { mode: ThemeMode; label: string; icon: ReactNode; swatch: string[] }[] = [
  { mode: "system", label: "System", icon: <Monitor size={14} />, swatch: ["#f4f5f8", "#0c0d11"] },
  { mode: "light", label: "Light", icon: <Sun size={14} />, swatch: ["#ffffff", "#eef0ff"] },
  { mode: "dark", label: "Dark", icon: <Moon size={14} />, swatch: ["#14151b", "#1d1e26"] },
];

export function SettingsView({
  settingsData,
  models,
  themeMode,
  onTheme,
  onSaved,
}: {
  settingsData?: SettingsData;
  models: string[];
  themeMode: ThemeMode;
  onTheme: (mode: ThemeMode) => void;
  onSaved: (settings: SettingsData) => void;
}) {
  const [draft, setDraft] = useState<SettingsData | undefined>(settingsData);
  const [state, setState] = useState<{ saving: boolean; error: string; saved: boolean }>({ saving: false, error: "", saved: false });
  const current = draft ?? settingsData;

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!current) return;
    setState({ saving: true, error: "", saved: false });
    try {
      const saved = await api<SettingsData>("/api/settings", { method: "PUT", body: JSON.stringify(current) });
      onSaved(saved);
      setDraft(saved);
      setState({ saving: false, error: "", saved: true });
    } catch (reason) {
      setState({ saving: false, error: reason instanceof Error ? reason.message : "Could not save", saved: false });
    }
  }

  return (
    <div className="page">
      <div className="settings-grid">
        <div className="card">
          <div className="card-head">Appearance</div>
          <div className="theme-options" role="radiogroup" aria-label="Theme">
            {THEME_CHOICES.map((choice) => (
              <button type="button" role="radio" aria-checked={themeMode === choice.mode} key={choice.mode}
                className={`theme-option${themeMode === choice.mode ? " is-on" : ""}`} onClick={() => onTheme(choice.mode)}>
                <span className="theme-preview">
                  {choice.swatch.map((color) => <span key={color} style={{ background: color }} />)}
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>{choice.icon} {choice.label}</span>
              </button>
            ))}
          </div>
          <p className="empty-mini" style={{ paddingBottom: 0 }}>
            System follows your Windows light/dark setting. The sun/moon button in the header switches instantly.
          </p>
        </div>
        <div className="card">
          <div className="card-head">Keyboard</div>
          <div className="kv"><span>Search</span><b><kbd>Ctrl</kbd> <kbd>K</kbd></b></div>
          <div className="kv"><span>New chat</span><b><kbd>Ctrl</kbd> <kbd>Shift</kbd> <kbd>O</kbd></b></div>
          <div className="kv"><span>Send message</span><b><kbd>Enter</kbd></b></div>
          <div className="kv"><span>New line</span><b><kbd>Shift</kbd> <kbd>Enter</kbd></b></div>
        </div>
      </div>
      {current ? (
        <form className="card form" onSubmit={save}>
          <div className="card-head" style={{ marginBottom: 0 }}>Assistant</div>
          <div className="form-row">
            <label>
              Chat model
              <select value={current.model} onChange={(event) => setDraft({ ...current, model: event.target.value })}>
                {[...new Set([current.model, ...models])].map((model) => <option key={model}>{model}</option>)}
              </select>
            </label>
            <label>
              Embedding model
              <input value={current.embed_model} onChange={(event) => setDraft({ ...current, embed_model: event.target.value })} />
            </label>
            <label>
              Temperature <small>0 = precise · 1 = creative</small>
              <input type="number" min="0" max="2" step="0.1" value={current.temperature}
                onChange={(event) => setDraft({ ...current, temperature: Number(event.target.value) })} />
            </label>
          </div>
          <div className="form-row">
            <label>
              Sources per answer
              <input type="number" min="1" max="20" value={current.top_k}
                onChange={(event) => setDraft({ ...current, top_k: Number(event.target.value) })} />
            </label>
            <label>
              Similarity limit <small>2 = no limit</small>
              <input type="number" min="0" max="2" step="0.1" value={current.max_distance}
                onChange={(event) => setDraft({ ...current, max_distance: Number(event.target.value) })} />
            </label>
            <span />
          </div>
          <label>
            System prompt
            <textarea rows={6} value={current.system_prompt} maxLength={8000}
              onChange={(event) => setDraft({ ...current, system_prompt: event.target.value })} />
          </label>
          <div className="toolbar">
            <button type="submit" className="btn primary" disabled={state.saving}>
              {state.saving ? "Saving…" : "Save settings"}
            </button>
            {state.saved && <span className="saved-note">Saved</span>}
            {state.error && <span className="err-text">{state.error}</span>}
          </div>
        </form>
      ) : (
        <div className="card"><div className="empty-mini">Loading settings…</div></div>
      )}
    </div>
  );
}
