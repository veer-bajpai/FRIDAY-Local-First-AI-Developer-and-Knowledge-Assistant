"use client";

import { useState, type ReactNode } from "react";
import { Activity, Bot, FileText, MessageSquare } from "lucide-react";
import { AreaChart, DONUT_COLORS, Donut, Sparkline } from "./charts";
import { perDay, timeAgo, type ActivityEvent, type AgentSessionSummary, type Conversation, type Document, type Health, type Memory } from "./api";

function Stat({ label, value, foot, spark, muted }: { label: string; value: ReactNode; foot: string; spark?: number[]; muted?: boolean }) {
  return (
    <div className="card stat">
      <small>{label}</small>
      <div className="stat-row">
        <b>{value}</b>
        {spark && <Sparkline values={spark} />}
      </div>
      <span className={`stat-foot${muted ? " muted" : ""}`}>{foot}</span>
    </div>
  );
}

const TYPE_GROUPS: Record<string, string> = {
  PDF: "PDF",
  MD: "Markdown",
  MARKDOWN: "Markdown",
  TXT: "Text",
  DOCX: "Office",
  PPTX: "Office",
  XLSX: "Office",
  PNG: "Images",
  JPG: "Images",
  JPEG: "Images",
  WEBP: "Images",
  GIF: "Images",
  BMP: "Images",
};

const STATUS_LABEL: Record<string, string> = {
  completed: "Completed",
  interrupted: "Resumable",
  failed: "Failed",
  cancelled: "Cancelled",
  running: "Running",
  awaiting_approval: "Approval",
  idle: "New",
};

export function Dashboard({
  composer,
  conversations,
  documents,
  memories,
  sessions,
  events,
  health,
  collectionsCount,
  onOpenAgent,
  onOpenDocuments,
  onOpenActivity,
}: {
  composer: ReactNode;
  conversations: Conversation[];
  documents: Document[];
  memories: Memory[];
  sessions: AgentSessionSummary[];
  events: ActivityEvent[];
  health?: Health;
  collectionsCount: number;
  onOpenAgent: () => void;
  onOpenDocuments: () => void;
  onOpenActivity: () => void;
}) {
  const [days, setDays] = useState(14);

  const chats = events.filter((event) => event.type === "chat");
  const series = perDay(events.map((event) => event.created_at), days);
  const labels = series.map((_, index) => {
    const date = new Date();
    date.setDate(date.getDate() - (series.length - 1 - index));
    return date.toLocaleDateString("en-US", { day: "numeric", month: "short" });
  });
  const total = series.reduce((sum, value) => sum + value, 0);
  const half = Math.floor(series.length / 2);
  const recent = series.slice(half).reduce((a, b) => a + b, 0);
  const earlier = series.slice(0, half).reduce((a, b) => a + b, 0);
  const trend = earlier ? Math.round(((recent - earlier) / earlier) * 100) : null;

  const chunks = documents.reduce((sum, document) => sum + (document.chunks || 0), 0);
  const failedDocs = documents.filter((document) => document.status === "failed").length;
  const thisWeek = perDay(conversations.map((c) => c.updated_at), 7);
  const completed = sessions.filter((s) => s.status === "completed").length;
  const resumable = sessions.filter((s) => s.status === "interrupted").length;

  const groups = new Map<string, number>();
  for (const document of documents) {
    const group = TYPE_GROUPS[document.type?.toUpperCase()] ?? "Code & other";
    groups.set(group, (groups.get(group) ?? 0) + 1);
  }
  const parts = [...groups.entries()].map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);

  const ollama = health?.checks.ollama;
  const extraction = health?.checks.extraction;
  const healthRows: { name: string; state: string; tone: "ok" | "warn" | "error" }[] = health
    ? [
        { name: "Ollama", state: ollama?.status === "error" ? "offline" : "online", tone: ollama?.status === "error" ? "error" : "ok" },
        {
          name: ollama?.chat_model?.name ?? "Chat model",
          state: ollama?.chat_model?.installed ? "ready" : "not installed",
          tone: ollama?.chat_model?.installed ? "ok" : "warn",
        },
        {
          name: ollama?.embed_model?.name ?? "Embeddings",
          state: ollama?.embed_model?.installed ? "ready" : "not installed",
          tone: ollama?.embed_model?.installed ? "ok" : "warn",
        },
        { name: "OCR (Tesseract)", state: extraction?.ocr ? "ready" : "not installed", tone: extraction?.ocr ? "ok" : "warn" },
        {
          name: "Vision model",
          state: extraction?.vision_model ? String(extraction.vision_model) : "not set",
          tone: extraction?.vision_model ? "ok" : "warn",
        },
      ]
    : [];

  return (
    <div className="dash">
      <div className="stats">
        <Stat label="Conversations" value={conversations.length} spark={thisWeek}
          foot={`${thisWeek.reduce((a, b) => a + b, 0)} active this week · ${chats.length} recent answers`} />
        <Stat label="Documents indexed" value={documents.length}
          foot={`${chunks.toLocaleString()} chunks · ${collectionsCount} collection${collectionsCount === 1 ? "" : "s"}${failedDocs ? ` · ${failedDocs} failed` : ""}`}
          muted={!documents.length} />
        <Stat label="Memories" value={memories.length} foot={memories.length ? "used automatically in chat" : "type remember: in chat"} muted={!memories.length} />
        <Stat label="Agent sessions" value={sessions.length}
          foot={sessions.length ? `${completed} completed${resumable ? ` · ${resumable} resumable` : ""}` : "run a coding task"}
          muted={!sessions.length} />
      </div>

      {composer}

      <div className="bento">
        <div className="card span-wide">
          <div className="card-head">
            <Activity size={15} /> Activity
            <div className="seg" role="group" aria-label="Range">
              {[7, 14, 30].map((value) => (
                <button key={value} type="button" className={days === value ? "is-on" : ""} onClick={() => setDays(value)}>
                  {value}d
                </button>
              ))}
            </div>
          </div>
          <div className="big-number">
            <b>{total}</b>
            <span>
              events in {days} days
              {trend !== null && ` · ${trend >= 0 ? "↑" : "↓"} ${Math.abs(trend)}% vs previous`}
            </span>
          </div>
          <AreaChart values={series} labels={labels} />
        </div>

        <div className="card">
          <div className="card-head">
            <Bot size={15} /> Agent runs
            <button type="button" className="link" onClick={onOpenAgent}>Open</button>
          </div>
          <div className="rows">
            {sessions.slice(0, 5).map((session) => (
              <button type="button" className="row" key={session.full_id ?? session.id} onClick={onOpenAgent}>
                <span className={`dot ${session.busy ? "running" : session.status ?? ""}`} />
                <span className="grow">
                  <b>{session.title || "New session"}</b>
                  <small>{session.workspace} · {session.messages ?? 0} messages</small>
                </span>
                <span className={`tag ${session.busy ? "running" : session.status ?? ""}`}>
                  {session.busy ? "Running" : STATUS_LABEL[session.status ?? "idle"] ?? session.status}
                </span>
              </button>
            ))}
            {!sessions.length && (
              <div className="empty-mini">No agent sessions yet. The agent can inspect, edit and test a project with your approval.</div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <FileText size={15} /> Knowledge base
            <button type="button" className="link" onClick={onOpenDocuments}>Manage</button>
          </div>
          <div className="donut-wrap">
            <Donut parts={parts} size={116} label={`${documents.length} documents by type`} />
            <div className="legend">
              {parts.slice(0, 5).map((part, index) => (
                <div key={part.name}>
                  <i style={{ background: DONUT_COLORS[index % DONUT_COLORS.length] }} />
                  <span>{part.name}</span>
                  <b>{part.value}</b>
                </div>
              ))}
              {!parts.length && <div>No documents yet</div>}
            </div>
          </div>
          <div className="health-list">
            {healthRows.map((row) => (
              <div className="row" key={row.name}>
                <span className={`dot ${row.tone}`} />
                <span className="grow">{row.name}</span>
                <span className={`state ${row.tone}`}>{row.state}</span>
              </div>
            ))}
            {!health && <div className="empty-mini">Checking local services…</div>}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <MessageSquare size={15} /> Recent activity
          <button type="button" className="link" onClick={onOpenActivity}>View all</button>
        </div>
        <div className="rows">
          {events.slice(0, 5).map((event) => (
            <div className="row" key={event.id}>
              <span className={`dot ${event.status === "error" ? "error" : event.status === "processing" ? "warn" : "ok"}`} />
              <span className="grow">
                <b>{event.title}</b>
                {event.detail && <small>{event.detail}</small>}
              </span>
              <time>{timeAgo(event.created_at)}</time>
            </div>
          ))}
          {!events.length && <div className="empty-mini">Your uploads, chats and changes will show up here.</div>}
        </div>
      </div>
    </div>
  );
}
