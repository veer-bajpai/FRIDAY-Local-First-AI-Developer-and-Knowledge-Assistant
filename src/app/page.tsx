"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  Archive,
  Bot,
  Brain,
  FileText,
  Grid2X2,
  LayoutGrid,
  Menu,
  MessageSquare,
  Moon,
  Plus,
  Search,
  Settings,
  Sparkles,
  Sun,
  Upload,
  X,
} from "lucide-react";
import AgentPanel from "./agent/AgentPanel";
import { ChatHeader, Composer, ContextRail, Thread } from "./ui/ChatView";
import { Dashboard } from "./ui/Dashboard";
import { Logo } from "./ui/Logo";
import {
  API,
  SUPPORTED_UPLOAD_PATTERN,
  UPLOAD_ACCEPT,
  api,
  errorMessage,
  type ActivityEvent,
  type AgentSessionSummary,
  type Collection,
  type Conversation,
  type Document,
  type Health,
  type Memory,
  type Message,
  type SettingsData,
  type Source,
  type Step,
} from "./ui/api";
import { useTheme } from "./ui/theme";
import {
  ActivityView,
  CollectionsView,
  DocumentsView,
  HistoryView,
  MemoryView,
  SearchView,
  SettingsView,
} from "./ui/Views";
import "./ui/ui.css";

type View =
  | "Home"
  | "Chat"
  | "Agent"
  | "Documents"
  | "Search"
  | "Collections"
  | "Memory"
  | "Activity"
  | "History"
  | "Settings";

const NAV: { view: View; icon: typeof MessageSquare }[] = [
  { view: "Home", icon: LayoutGrid },
  { view: "Chat", icon: MessageSquare },
  { view: "Agent", icon: Bot },
  { view: "Documents", icon: FileText },
  { view: "Search", icon: Search },
  { view: "Collections", icon: Grid2X2 },
  { view: "Memory", icon: Brain },
  { view: "Activity", icon: Activity },
  { view: "Settings", icon: Settings },
];

const SUBTITLES: Partial<Record<View, string>> = {
  Agent: "Inspect, edit and test a project with your approval",
  Documents: "Files FRIDAY can read, search and cite",
  Search: "Hybrid keyword + semantic search across your files",
  Collections: "Group documents by topic",
  Memory: "Facts and preferences FRIDAY keeps across chats",
  Activity: "Everything that happened in this workspace",
  History: "All saved conversations",
  Settings: "Models, retrieval and appearance",
};

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? "Working late, Veer" : hour < 12 ? "Good morning, Veer" : hour < 17 ? "Good afternoon, Veer" : "Good evening, Veer";
}

export default function Home() {
  const theme = useTheme();
  const [view, setView] = useState<View>("Home");
  const [menuOpen, setMenuOpen] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [documents, setDocuments] = useState<Document[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [sessions, setSessions] = useState<AgentSessionSummary[]>([]);
  const [settingsData, setSettingsData] = useState<SettingsData>();
  const [models, setModels] = useState<string[]>([]);
  const [health, setHealth] = useState<Health>();

  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string>();
  const [sending, setSending] = useState(false);
  const [liveStartedAt, setLiveStartedAt] = useState<number>();
  const [livePhrase, setLivePhrase] = useState<string>();
  const [uploading, setUploading] = useState(false);

  const activeRequest = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const report = useCallback((reason: unknown, fallback: string) => {
    setError(reason instanceof Error ? reason.message : fallback);
  }, []);

  /* ------------------------------------------------------------ loading */

  const loadDocuments = useCallback(() => api<Document[]>("/api/documents").then(setDocuments), []);
  const loadConversations = useCallback(() => api<Conversation[]>("/api/conversations").then(setConversations), []);
  const loadEvents = useCallback(() => api<ActivityEvent[]>("/api/activity?limit=200").then(setEvents), []);
  const loadMemories = useCallback(() => api<Memory[]>("/api/memories").then(setMemories), []);
  const loadSessions = useCallback(() => api<AgentSessionSummary[]>("/api/agent/sessions").then(setSessions), []);
  const loadHealth = useCallback(() => api<Health>("/api/health").then(setHealth), []);

  useEffect(() => {
    Promise.all([
      loadDocuments(),
      loadConversations(),
      loadEvents(),
      api<Collection[]>("/api/collections").then(setCollections),
      api<SettingsData>("/api/settings").then(setSettingsData),
    ]).catch((reason) => report(reason, "Could not reach the FRIDAY API. Is the backend running?"));
    // optional panels: failures here should not block the workspace
    loadMemories().catch(() => undefined);
    loadSessions().catch(() => undefined);
    loadHealth().catch(() => undefined);
    api<{ models?: { name: string }[] }>("/api/models")
      .then((data) => setModels((data.models ?? []).map((m) => m.name).filter((name) => !/embed/i.test(name))))
      .catch(() => undefined);
    const poll = window.setInterval(() => loadHealth().catch(() => undefined), 30000);
    return () => window.clearInterval(poll);
  }, [loadConversations, loadDocuments, loadEvents, loadHealth, loadMemories, loadSessions, report]);

  // refresh dashboard data whenever it is opened
  useEffect(() => {
    if (view !== "Home") return;
    loadEvents().catch(() => undefined);
    loadSessions().catch(() => undefined);
  }, [view, loadEvents, loadSessions]);

  // documents still processing: poll until ready
  useEffect(() => {
    if (!documents.some((d) => d.status === "processing")) return;
    const timer = window.setTimeout(() => loadDocuments().catch(() => undefined), 2000);
    return () => window.clearTimeout(timer);
  }, [documents, loadDocuments]);

  /* ------------------------------------------------------------ navigation */

  const go = useCallback((next: View) => {
    setView(next);
    setMenuOpen(false);
  }, []);

  const newChat = useCallback(() => {
    if (sending) return;
    setConversationId(undefined);
    setMessages([]);
    setDraft("");
    go("Chat");
  }, [go, sending]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.key.toLowerCase() === "k") {
        event.preventDefault();
        go("Search");
      } else if (mod && event.shiftKey && event.key.toLowerCase() === "o") {
        event.preventDefault();
        newChat();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, newChat]);

  async function openConversation(id: string) {
    if (sending) return;
    try {
      const conversation = await api<{ id: string; messages: Message[] }>(`/api/conversations/${id}`);
      setConversationId(conversation.id);
      setMessages(conversation.messages);
      go("Chat");
    } catch (reason) {
      report(reason, "Could not open the conversation");
    }
  }

  async function deleteConversation(id: string) {
    try {
      await api(`/api/conversations/${id}`, { method: "DELETE" });
      if (id === conversationId) {
        setConversationId(undefined);
        setMessages([]);
      }
      await loadConversations();
    } catch (reason) {
      report(reason, "Could not delete the conversation");
    }
  }

  async function changeModel(model: string) {
    if (!settingsData) return;
    try {
      setSettingsData(await api<SettingsData>("/api/settings", { method: "PUT", body: JSON.stringify({ ...settingsData, model }) }));
    } catch (reason) {
      report(reason, "Could not switch the model");
    }
  }

  /* ------------------------------------------------------------ chat */

  async function send(text: string) {
    const question = text.trim();
    if (!question || sending) return;
    go("Chat");
    setDraft("");
    setError("");

    const started = Date.now();
    const remembering = /^\s*(\/remember|remember(\s+that)?\s*:)/i.test(question);
    let steps: Step[] = [{ label: remembering ? "Saving to memory" : "Searching your documents and memory", state: "live" }];
    let content = "";
    let sources: Source[] | undefined;
    let memoriesUsed: Memory[] | undefined;
    let model: string | null | undefined;
    let note: string | undefined;
    let conversation = conversationId;
    let failed = false;

    const base = messages.length;
    const assistantIndex = base + 1;
    const paint = () =>
      setMessages((current) => {
        const next = current.slice(0, assistantIndex);
        next[assistantIndex] = {
          role: "assistant",
          content,
          steps,
          sources,
          memories: memoriesUsed,
          model,
          note,
          failed,
          elapsedMs: Date.now() - started,
          created_at: new Date().toISOString(),
        };
        return next;
      });

    setMessages((current) => [...current, { role: "user", content: question }]);
    setSending(true);
    setLiveStartedAt(started);
    setLivePhrase(remembering ? "Saving to memory" : undefined);
    paint();

    const controller = new AbortController();
    activeRequest.current = controller;
    let lastPaint = 0;

    try {
      const response = await fetch(`${API}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({ question, k: settingsData?.top_k ?? 3, conversation_id: conversationId }),
      });
      if (!response.ok) throw new Error(await errorMessage(response, "Chat request failed"));
      if (!response.body) throw new Error("Chat request failed");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          if (!block.startsWith("data: ")) continue;
          const payload = JSON.parse(block.slice(6)) as {
            token?: string;
            done?: boolean;
            error?: string;
            conversation_id?: string | null;
            sources?: Source[];
            memories?: Memory[];
            model?: string | null;
            note?: string | null;
            meta?: { model?: string | null; note?: string | null; sources?: number; memories?: number; memory?: Memory };
          };
          if (payload.conversation_id) conversation = payload.conversation_id;
          if (payload.error) throw new Error(payload.error);
          if (payload.meta) {
            model = payload.meta.model ?? model;
            note = payload.meta.note ?? undefined;
            if (payload.meta.memory) {
              steps = [{ label: "Saved to long-term memory", state: "done" }];
            } else {
              const found = payload.meta.sources ?? 0;
              const mem = payload.meta.memories ?? 0;
              steps = [
                {
                  label: found
                    ? `Found ${found} relevant passage${found === 1 ? "" : "s"} in your documents${mem ? ` · ${mem} memor${mem === 1 ? "y" : "ies"}` : ""}`
                    : `No matching documents${mem ? ` · using ${mem} memor${mem === 1 ? "y" : "ies"}` : " · answering from the model"}`,
                  state: "done",
                },
                { label: `Writing the answer with ${model ?? "the local model"}`, state: "live" },
              ];
              setLivePhrase("Writing");
            }
            paint();
          }
          if (payload.token) {
            content += payload.token;
            const now = performance.now();
            if (now - lastPaint > 40) {
              lastPaint = now;
              paint();
            }
          }
          if (payload.done) {
            sources = payload.sources ?? sources;
            memoriesUsed = payload.memories ?? memoriesUsed;
            model = payload.model ?? model;
            note = payload.note ?? note;
            steps = steps.map((step) => ({ ...step, state: "done" }));
          }
        }
      }
      steps = steps.map((step) => ({ ...step, state: "done" }));
      paint();
      if (remembering) loadMemories().catch(() => undefined);
    } catch (reason) {
      const aborted = reason instanceof Error && reason.name === "AbortError";
      failed = true;
      const message = aborted
        ? "Stopped."
        : reason instanceof TypeError
          ? "I could not reach the FRIDAY API. Check that the backend is running."
          : reason instanceof Error
            ? reason.message
            : "Chat request failed";
      steps = steps.map((step) => (step.state === "live" ? { ...step, state: aborted ? "done" : "error" } : step));
      if (!aborted) {
        steps.push({ label: message, state: "error" });
        setError(message);
      }
      content = content || (aborted ? "_Stopped before an answer was written._" : "");
      paint();
    } finally {
      activeRequest.current = null;
      setSending(false);
      setLiveStartedAt(undefined);
      setLivePhrase(undefined);
      if (conversation) setConversationId(conversation);
      loadConversations().catch(() => undefined);
      loadEvents().catch(() => undefined);
    }
  }

  /* ------------------------------------------------------------ uploads */

  async function upload(files: File[]) {
    const supported = files.filter((file) => SUPPORTED_UPLOAD_PATTERN.test(file.name));
    if (!supported.length) {
      setError("Choose PDF, Word, PowerPoint, Excel, text, Markdown, code, CSV, JSON, HTML or image files.");
      return;
    }
    setUploading(true);
    const failures: string[] = [];
    await Promise.all(
      supported.map(async (file) => {
        const form = new FormData();
        form.append("file", file);
        const response = await fetch(`${API}/api/documents/upload`, { method: "POST", body: form }).catch(() => null);
        if (!response) failures.push(`${file.name}: the API is unreachable`);
        else if (!response.ok) failures.push(`${file.name}: ${await errorMessage(response, "Upload failed")}`);
      }),
    );
    setUploading(false);
    const skipped = files.length - supported.length;
    if (failures.length) setError(failures.join(" · "));
    else setNotice(`Added ${supported.length} file${supported.length === 1 ? "" : "s"}${skipped ? ` · ${skipped} unsupported skipped` : ""}. Indexing…`);
    loadDocuments().catch(() => undefined);
    loadEvents().catch(() => undefined);
  }

  /* ------------------------------------------------------------ render */

  const online = health ? health.checks.ollama?.status !== "error" : null;
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const activeTitle = conversations.find((c) => c.id === conversationId)?.title ?? (messages.length ? messages[0].content.slice(0, 60) : "New chat");
  const extraction = health?.checks.extraction;
  const capability = health
    ? [online, health.checks.ollama?.chat_model?.installed, health.checks.ollama?.embed_model?.installed, extraction?.ocr, !!extraction?.vision_model]
    : [];
  const capabilityScore = capability.length ? capability.filter(Boolean).length / capability.length : 0;

  const composerProps = {
    value: draft,
    onChange: setDraft,
    onSubmit: () => void send(draft),
    onStop: () => activeRequest.current?.abort(),
    busy: sending,
    onAttach: () => fileInput.current?.click(),
    documentsCount: documents.length,
    memoriesCount: memories.length,
    onOpenDocuments: () => go("Documents"),
    onOpenMemory: () => go("Memory"),
    models,
    settings: settingsData,
    onModelChange: (model: string) => void changeModel(model),
  };

  const title =
    view === "Home" ? greeting() : view === "Chat" ? activeTitle : view;
  const subtitle =
    view === "Home"
      ? `${new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" })} · ${online === false ? "Ollama is offline" : "everything runs on this device"}`
      : view === "Chat"
        ? `${messages.filter((m) => m.role === "user").length} message${messages.filter((m) => m.role === "user").length === 1 ? "" : "s"} · answers cite your local files`
        : SUBTITLES[view];

  return (
    <div className="app">
      {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} />}
      <aside className={`side${menuOpen ? " is-open" : ""}`} aria-label="Navigation">
        <div className="side-brand">
          <Logo />
          <button type="button" className="icon-btn ghost sm menu-btn" aria-label="Close menu" onClick={() => setMenuOpen(false)}>
            <X size={16} />
          </button>
        </div>
        <button type="button" className="side-new" onClick={newChat} disabled={sending}>
          <Plus size={16} /> New chat <kbd>Ctrl ⇧ O</kbd>
        </button>
        <nav aria-label="Primary">
          {NAV.map(({ view: item, icon: Icon }) => {
            const count =
              item === "Chat" ? conversations.length : item === "Documents" ? documents.length : item === "Memory" ? memories.length
                : item === "Collections" ? collections.length : item === "Agent" ? sessions.length : 0;
            return (
              <button type="button" key={item} className={`nav${view === item ? " is-active" : ""}`} onClick={() => go(item)}
                aria-current={view === item ? "page" : undefined}>
                <Icon size={16} />
                {item}
                {count > 0 && <span className="count">{count}</span>}
              </button>
            );
          })}
        </nav>
        <div className="side-label">
          Recents
          {conversations.length > 0 && (
            <button type="button" onClick={() => go("History")}>
              <Archive size={11} style={{ verticalAlign: -1 }} /> All
            </button>
          )}
        </div>
        <div className="recents">
          {conversations.slice(0, 12).map((conversation) => (
            <button type="button" key={conversation.id} title={conversation.title}
              className={`recent${conversation.id === conversationId && view === "Chat" ? " is-active" : ""}`}
              onClick={() => void openConversation(conversation.id)}>
              {conversation.title}
            </button>
          ))}
          {!conversations.length && <div className="recent-empty">Your chats will appear here</div>}
        </div>
        <div className="side-spacer" />
        <button type="button" className="health-card" onClick={() => go("Home")}>
          <b><Sparkles size={14} /> {online === false ? "Ollama is offline" : "Local engine"}</b>
          <small>
            {!health
              ? "Checking local services…"
              : online === false
                ? "Start it with: ollama serve"
                : `${health.checks.ollama?.chat_model?.name ?? "model"} · ${extraction?.ocr ? "OCR ready" : "no OCR"}${extraction?.vision_model ? " · vision on" : ""}`}
          </small>
          <div className="bar"><i style={{ width: `${Math.round(capabilityScore * 100)}%` }} /></div>
        </button>
        <div className="me">
          <span className="avatar">VB</span>
          <div>
            <b>Veer Bajpai</b>
            <small>Local workspace</small>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <button type="button" className="icon-btn menu-btn" aria-label="Open menu" onClick={() => setMenuOpen(true)}>
            <Menu size={18} />
          </button>
          <div className="topbar-title">
            <h1 suppressHydrationWarning>{title}</h1>
            <p suppressHydrationWarning>{subtitle}</p>
          </div>
          <div className="topbar-actions">
            <button type="button" className="searchbox" onClick={() => go("Search")}>
              <Search size={15} />
              <span>Search your files</span>
              <kbd>Ctrl K</kbd>
            </button>
            <button type="button" className="icon-btn" onClick={theme.toggle}
              aria-label={theme.resolved === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              title={theme.resolved === "dark" ? "Light mode" : "Dark mode"}>
              {theme.resolved === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <button type="button" className="btn hide-sm" onClick={() => fileInput.current?.click()} disabled={uploading}>
              <Upload size={15} /> {uploading ? "Uploading…" : "Upload"}
            </button>
            <button type="button" className="btn primary" onClick={newChat} disabled={sending}>
              <MessageSquare size={15} /> <span className="hide-sm">New chat</span>
            </button>
          </div>
        </header>

        {error && (
          <div className="banner" role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => setError("")} aria-label="Dismiss"><X size={15} /></button>
          </div>
        )}
        {notice && !error && (
          <div className="banner info" role="status">
            <span>{notice}</span>
            <button type="button" onClick={() => setNotice("")} aria-label="Dismiss"><X size={15} /></button>
          </div>
        )}

        <div className={`content${view === "Chat" ? " fill" : ""}`}>
          {view === "Home" && (
            <Dashboard
              composer={<Composer {...composerProps} hero placeholder="Ask FRIDAY anything, or drop in a question about your files…" />}
              conversations={conversations}
              documents={documents}
              memories={memories}
              sessions={sessions}
              events={events}
              health={health}
              collectionsCount={collections.length}
              onOpenAgent={() => go("Agent")}
              onOpenDocuments={() => go("Documents")}
              onOpenActivity={() => go("Activity")}
            />
          )}

          {view === "Chat" && (
            <div className={`chat-layout${messages.length ? "" : " no-rail"}`}>
              <section className="chat-card" aria-label="Conversation">
                <ChatHeader title={activeTitle} online={online} />
                <Thread
                  messages={messages}
                  live={sending}
                  liveStartedAt={liveStartedAt}
                  livePhrase={livePhrase}
                  onRetry={(question) => void send(question)}
                  onStarter={(text) => setDraft(text)}
                />
                <div className="dock">
                  <div className="dock-inner">
                    <Composer {...composerProps} placeholder={messages.length ? "Reply to FRIDAY…" : "Message FRIDAY…"} autoFocus />
                    <div className="composer-note">FRIDAY can make mistakes. Check important information against the cited sources.</div>
                  </div>
                </div>
              </section>
              {messages.length > 0 && (
                <ContextRail message={lastAssistant} settings={settingsData} onOpenMemory={() => go("Memory")} />
              )}
            </div>
          )}

          {view === "Agent" && (
            <div className="agent-shell card">
              <AgentPanel />
            </div>
          )}
          {view === "Documents" && (
            <DocumentsView documents={documents} onUpload={() => fileInput.current?.click()}
              onChanged={() => void loadDocuments().catch(() => undefined)} onError={setError} />
          )}
          {view === "Search" && <SearchView />}
          {view === "Collections" && <CollectionsView collections={collections} onChanged={setCollections} onError={setError} />}
          {view === "Memory" && (
            <MemoryView memories={memories} onChanged={() => void loadMemories().catch(() => undefined)} onError={setError} />
          )}
          {view === "Activity" && <ActivityView events={events} />}
          {view === "History" && (
            <HistoryView conversations={conversations} activeId={conversationId} onSelect={(id) => void openConversation(id)}
              onNew={newChat} onDelete={(id) => void deleteConversation(id)} />
          )}
          {view === "Settings" && (
            <SettingsView settingsData={settingsData} models={models} themeMode={theme.mode} onTheme={theme.setMode}
              onSaved={setSettingsData} />
          )}
        </div>
      </main>

      <input
        ref={fileInput}
        hidden
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        onChange={(event) => {
          const files = event.target.files ? Array.from(event.target.files) : [];
          if (files.length) void upload(files);
          event.target.value = "";
        }}
      />
    </div>
  );
}
