"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  Bot,
  Brain,
  FileText,
  Grid2X2,
  LayoutGrid,
  Menu,
  Moon,
  Search,
  Settings,
  Sparkles,
  Sun,
  Upload,
  X,
} from "lucide-react";
import AgentPanel from "./agent/AgentPanel";
import { Dashboard } from "./ui/Dashboard";
import { Logo } from "./ui/Logo";
import {
  API,
  SUPPORTED_UPLOAD_PATTERN,
  UPLOAD_ACCEPT,
  api,
  apiErrorMessage,
  errorMessage,
  type ActivityEvent,
  type AgentSessionSummary,
  type Collection,
  type Document,
  type Health,
  type Memory,
  type SettingsData,
} from "./ui/api";
import { useTheme } from "./ui/theme";
import {
  ActivityView,
  CollectionsView,
  DocumentsView,
  MemoryView,
  SearchView,
  SettingsView,
} from "./ui/Views";
import "./ui/ui.css";

type View =
  | "Home"
  | "Agent"
  | "Documents"
  | "Search"
  | "Collections"
  | "Memory"
  | "Activity"
  | "Settings";

const NAV: { view: View; icon: typeof Activity }[] = [
  { view: "Home", icon: LayoutGrid },
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
  Memory: "Facts and preferences available to your workspace",
  Activity: "Everything that happened in this workspace",
  Settings: "Knowledge indexing and appearance",
};

function greeting() {
  const hour = new Date().getHours();
  return hour < 5
    ? "Working late, Veer"
    : hour < 12
      ? "Good morning, Veer"
      : hour < 17
        ? "Good afternoon, Veer"
        : "Good evening, Veer";
}

export default function Home() {
  const theme = useTheme();
  const [view, setView] = useState<View>("Home");
  const [menuOpen, setMenuOpen] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [documents, setDocuments] = useState<Document[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [sessions, setSessions] = useState<AgentSessionSummary[]>([]);
  const [settingsData, setSettingsData] = useState<SettingsData>();
  const [health, setHealth] = useState<Health>();

  const [uploading, setUploading] = useState(false);

  const fileInput = useRef<HTMLInputElement>(null);

  const report = useCallback((reason: unknown, fallback: string) => {
    setError(apiErrorMessage(reason, fallback));
  }, []);

  /* ------------------------------------------------------------ loading */

  const loadDocuments = useCallback(
    () => api<Document[]>("/api/documents").then(setDocuments),
    [],
  );
  const loadEvents = useCallback(
    () => api<ActivityEvent[]>("/api/activity?limit=200").then(setEvents),
    [],
  );
  const loadMemories = useCallback(
    () => api<Memory[]>("/api/memories").then(setMemories),
    [],
  );
  const loadSessions = useCallback(
    () => api<AgentSessionSummary[]>("/api/agent/sessions").then(setSessions),
    [],
  );
  const loadHealth = useCallback(
    () => api<Health>("/api/health").then(setHealth),
    [],
  );

  useEffect(() => {
    Promise.all([
      loadDocuments(),
      loadEvents(),
      api<Collection[]>("/api/collections").then(setCollections),
      api<SettingsData>("/api/settings").then(setSettingsData),
    ]).catch((reason) =>
      report(reason, "Could not reach the FRIDAY API. Is the backend running?"),
    );
    // optional panels: failures here should not block the workspace
    loadMemories().catch(() => undefined);
    loadSessions().catch(() => undefined);
    loadHealth().catch(() => undefined);
    const poll = window.setInterval(
      () => loadHealth().catch(() => undefined),
      30000,
    );
    return () => window.clearInterval(poll);
  }, [
    loadDocuments,
    loadEvents,
    loadHealth,
    loadMemories,
    loadSessions,
    report,
  ]);

  // refresh dashboard data whenever it is opened
  useEffect(() => {
    if (view !== "Home") return;
    loadEvents().catch(() => undefined);
    loadSessions().catch(() => undefined);
  }, [view, loadEvents, loadSessions]);

  // documents still processing: poll until ready
  useEffect(() => {
    if (!documents.some((d) => d.status === "processing")) return;
    const timer = window.setTimeout(
      () => loadDocuments().catch(() => undefined),
      2000,
    );
    return () => window.clearTimeout(timer);
  }, [documents, loadDocuments]);

  /* ------------------------------------------------------------ navigation */

  const go = useCallback((next: View) => {
    setView(next);
    setMenuOpen(false);
  }, []);

  /* ------------------------------------------------------------ uploads */

  async function upload(files: File[]) {
    const supported = files.filter((file) =>
      SUPPORTED_UPLOAD_PATTERN.test(file.name),
    );
    if (!supported.length) {
      setError(
        "Choose PDF, Word, PowerPoint, Excel, text, Markdown, code, CSV, JSON, HTML or image files.",
      );
      return;
    }
    setUploading(true);
    const failures: string[] = [];
    await Promise.all(
      supported.map(async (file) => {
        const form = new FormData();
        form.append("file", file);
        const response = await fetch(`${API}/api/documents/upload`, {
          method: "POST",
          body: form,
        }).catch(() => null);
        if (!response) failures.push(`${file.name}: the API is unreachable`);
        else if (!response.ok)
          failures.push(
            `${file.name}: ${await errorMessage(response, "Upload failed")}`,
          );
      }),
    );
    setUploading(false);
    const skipped = files.length - supported.length;
    if (failures.length) setError(failures.join(" · "));
    else
      setNotice(
        `Added ${supported.length} file${supported.length === 1 ? "" : "s"}${skipped ? ` · ${skipped} unsupported skipped` : ""}. Indexing…`,
      );
    loadDocuments().catch(() => undefined);
    loadEvents().catch(() => undefined);
  }

  /* ------------------------------------------------------------ render */

  const online = health ? health.checks.ollama?.status !== "error" : null;
  const extraction = health?.checks.extraction;
  const capability = health
    ? [
        online,
        health.checks.ollama?.agent_model?.installed,
        health.checks.ollama?.embed_model?.installed,
        extraction?.ocr,
        !!extraction?.vision_model,
      ]
    : [];
  const capabilityScore = capability.length
    ? capability.filter(Boolean).length / capability.length
    : 0;

  const title = view === "Home" ? greeting() : view;
  const subtitle =
    view === "Home"
      ? `${new Date().toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "short" })} · ${online === false ? "Ollama is offline" : "everything runs on this device"}`
      : SUBTITLES[view];

  return (
    <div className="app">
      {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} />}
      <aside
        className={`side${menuOpen ? " is-open" : ""}`}
        aria-label="Navigation"
      >
        <div className="side-brand">
          <Logo />
          <button
            type="button"
            className="icon-btn ghost sm menu-btn"
            aria-label="Close menu"
            onClick={() => setMenuOpen(false)}
          >
            <X size={16} />
          </button>
        </div>
        <nav aria-label="Primary">
          {NAV.map(({ view: item, icon: Icon }) => {
            const count =
              item === "Documents"
                ? documents.length
                : item === "Memory"
                  ? memories.length
                  : item === "Collections"
                    ? collections.length
                    : item === "Agent"
                      ? sessions.length
                      : 0;
            return (
              <button
                type="button"
                key={item}
                className={`nav${view === item ? " is-active" : ""}`}
                onClick={() => go(item)}
                aria-current={view === item ? "page" : undefined}
              >
                <Icon size={16} />
                {item}
                {count > 0 && <span className="count">{count}</span>}
              </button>
            );
          })}
        </nav>
        <div className="side-spacer" />
        <button
          type="button"
          className="health-card"
          onClick={() => go("Home")}
        >
          <b>
            <Sparkles size={14} />{" "}
            {online === false ? "Ollama is offline" : "Local engine"}
          </b>
          <small>
            {!health
              ? "Checking local services…"
              : online === false
                ? "Start it with: ollama serve"
                : `${health.checks.ollama?.agent_model?.name ?? "agent model"} · ${extraction?.ocr ? "OCR ready" : "no OCR"}${extraction?.vision_model ? " · vision on" : ""}`}
          </small>
          <div className="bar">
            <i style={{ width: `${Math.round(capabilityScore * 100)}%` }} />
          </div>
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
          <button
            type="button"
            className="icon-btn menu-btn"
            aria-label="Open menu"
            onClick={() => setMenuOpen(true)}
          >
            <Menu size={18} />
          </button>
          <div className="topbar-title">
            <h1 suppressHydrationWarning>{title}</h1>
            <p suppressHydrationWarning>{subtitle}</p>
          </div>
          <div className="topbar-actions">
            <button
              type="button"
              className="searchbox"
              onClick={() => go("Search")}
            >
              <Search size={15} />
              <span>Search your files</span>
              <kbd>Ctrl K</kbd>
            </button>
            <button
              type="button"
              className="icon-btn"
              onClick={theme.toggle}
              aria-label={
                theme.resolved === "dark"
                  ? "Switch to light mode"
                  : "Switch to dark mode"
              }
              title={theme.resolved === "dark" ? "Light mode" : "Dark mode"}
            >
              {theme.resolved === "dark" ? (
                <Sun size={17} />
              ) : (
                <Moon size={17} />
              )}
            </button>
            <button
              type="button"
              className="btn hide-sm"
              onClick={() => fileInput.current?.click()}
              disabled={uploading}
            >
              <Upload size={15} /> {uploading ? "Uploading…" : "Upload"}
            </button>
          </div>
        </header>

        {error && (
          <div className="banner" role="alert">
            <span>{error}</span>
            <button
              type="button"
              onClick={() => setError("")}
              aria-label="Dismiss"
            >
              <X size={15} />
            </button>
          </div>
        )}
        {notice && !error && (
          <div className="banner info" role="status">
            <span>{notice}</span>
            <button
              type="button"
              onClick={() => setNotice("")}
              aria-label="Dismiss"
            >
              <X size={15} />
            </button>
          </div>
        )}

        <div className="content">
          {view === "Home" && (
            <Dashboard
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

          {view === "Agent" && (
            <div className="agent-shell card">
              <AgentPanel />
            </div>
          )}
          {view === "Documents" && (
            <DocumentsView
              documents={documents}
              onUpload={() => fileInput.current?.click()}
              onChanged={() => void loadDocuments().catch(() => undefined)}
              onError={setError}
            />
          )}
          {view === "Search" && <SearchView />}
          {view === "Collections" && (
            <CollectionsView
              collections={collections}
              onChanged={setCollections}
              onError={setError}
            />
          )}
          {view === "Memory" && (
            <MemoryView
              memories={memories}
              onChanged={() => void loadMemories().catch(() => undefined)}
              onError={setError}
            />
          )}
          {view === "Activity" && <ActivityView events={events} />}
          {view === "Settings" && (
            <SettingsView
              settingsData={settingsData}
              themeMode={theme.mode}
              onTheme={theme.setMode}
              onSaved={setSettingsData}
            />
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
          const files = event.target.files
            ? Array.from(event.target.files)
            : [];
          if (files.length) void upload(files);
          event.target.value = "";
        }}
      />
    </div>
  );
}
